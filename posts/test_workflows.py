from __future__ import annotations

import io
import stat
import tempfile
import zipfile
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.templatetags.static import static
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .admin_uploads import ARTICLE_SESSION_KEY, PROJECT_SESSION_KEY
from .forms import ProjectModelForm
from .models import Post, Project, ProjectMedia, Series
from .services import (
    ContentProcessError,
    extract_zip_safely,
    parse_upload_file,
    process_markdown_content,
    rewrite_image_paths,
)
from .utils import render_markdown_with_toc


class _AttributeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.images.append(dict(attrs))


class MarkdownSanitizationTests(SimpleTestCase):
    def test_toc_includes_top_level_sections_and_decodes_titles_once(self):
        html, toc = render_markdown_with_toc(
            '# Methods & Results\n\n## **Costs** &amp; Limits\n\n'
            '### Details\n\n#### Edge cases\n\n# Summary\n'
        )
        self.assertEqual([item['title'] for item in toc], [
            'Methods & Results', 'Costs & Limits', 'Details', 'Edge cases', 'Summary',
        ])
        self.assertEqual([item['depth'] for item in toc], [0, 1, 2, 3, 0])
        for item in toc:
            self.assertIn(f'id="{item["anchor"]}"', html)
        _, toc = render_markdown_with_toc('## First section\n\n### Child')
        self.assertEqual([item['depth'] for item in toc], [0, 1])

    def test_scripts_embeds_events_and_dangerous_urls_are_removed(self):
        source = (
            '## Title\n\n<script>alert(1)</script><iframe src="https://evil.invalid"></iframe>'
            '<img src="https://example.com/a.png" onerror="alert(1)">'
            '<a href="javascript:alert(1)">bad</a><a href="https://example.com">good</a>'
        )
        html, toc = render_markdown_with_toc(source)
        self.assertNotIn("<script", html)
        self.assertNotIn("<iframe", html)
        self.assertNotIn("onerror=", html)
        self.assertNotIn("javascript:", html)
        self.assertIn('href="https://example.com"', html)
        self.assertEqual(toc[0]["title"], "Title")

    def test_math_restoration_cannot_escape_an_html_attribute(self):
        html, _ = render_markdown_with_toc(
            '<img src="https://example.com/a.png" alt="$x\\alpha\\\" onerror=\\\"alert(1)$">'
        )
        parser = _AttributeParser()
        parser.feed(html)
        self.assertEqual(len(parser.images), 1)
        self.assertEqual(set(parser.images[0]), {"src", "alt"})
        self.assertIn("&quot;", html)

    def test_tables_code_and_math_remain_available(self):
        html, _ = render_markdown_with_toc(
            "## Research\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n"
            "```python\nprint('safe')\n```\n\n$\\alpha_1$"
        )
        self.assertIn("<table>", html)
        self.assertIn("highlight", html)
        self.assertIn("$\\alpha_1$", html)


class TemporaryMediaTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self._temp = tempfile.TemporaryDirectory()
        self._media_override = override_settings(MEDIA_ROOT=Path(self._temp.name) / "media")
        self._media_override.enable()
        self.addCleanup(self._temp.cleanup)
        self.addCleanup(self._media_override.disable)

    @staticmethod
    def image(name="diagram.png"):
        stream = io.BytesIO()
        Image.new("RGB", (3, 3), "navy").save(stream, "PNG")
        return SimpleUploadedFile(name, stream.getvalue(), content_type="image/png")


class ZipSecurityTests(TemporaryMediaTestCase):
    @staticmethod
    def archive(entries):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as bundle:
            for name, value in entries:
                bundle.writestr(name, value)
        stream.seek(0)
        return stream

    def test_zip_slip_is_skipped_and_scripts_are_not_extracted(self):
        bundle = self.archive([
            ("../escape.md", "bad"), ("safe.md", "# Safe"),
            ("payload.js", "alert(1)"), ("image.png", b"not-rendered-here"),
        ])
        target = Path(self._temp.name) / "extract"
        md, media, warnings = extract_zip_safely(bundle, target)
        self.assertEqual(md.read_text(), "# Safe")
        self.assertEqual(len(media), 1)
        self.assertFalse((Path(self._temp.name) / "escape.md").exists())
        self.assertFalse((target / "payload.js").exists())
        self.assertTrue(any("危险路径" in warning for warning in warnings))

    def test_entry_and_cumulative_byte_budgets_are_enforced(self):
        with patch("posts.services.MAX_ZIP_ENTRIES", 2):
            with self.assertRaisesMessage(ValueError, "条目过多"):
                extract_zip_safely(self.archive([("a.md", "1"), ("b.png", b"2"), ("c.png", b"3")]), Path(self._temp.name) / "entries")
        with patch("posts.services.MAX_ZIP_UNCOMPRESSED_SIZE", 10):
            with self.assertRaisesMessage(ValueError, "累计大小"):
                extract_zip_safely(self.archive([("a.md", "12345678"), ("b.png", b"12345678")]), Path(self._temp.name) / "bytes")

    def test_symlinks_are_rejected(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as bundle:
            info = zipfile.ZipInfo("link.md")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(info, "target.md")
        stream.seek(0)
        with self.assertRaisesMessage(ValueError, "符号链接"):
            extract_zip_safely(stream, Path(self._temp.name) / "symlink")

    def test_nested_archive_limit_is_shared(self):
        inner = self.archive([("inside.md", "# Inside")])
        outer = io.BytesIO()
        with zipfile.ZipFile(outer, "w") as bundle:
            bundle.writestr("nested.zip", inner.getvalue())
        upload = SimpleUploadedFile("outer.zip", outer.getvalue())
        with patch("posts.services.MAX_NESTED_ARCHIVES", 0):
            with self.assertRaisesMessage(ValueError, "未找到"):
                parse_upload_file(upload)

    def test_encoded_filename_cannot_escape_media_directory(self):
        source_dir = Path(self._temp.name) / "source"
        source_dir.mkdir()
        image = source_dir / "..%2F..%2Fescape.png"
        image.write_bytes(b"test")
        copied = []
        content, _ = rewrite_image_paths("![](..%2F..%2Fescape.png)", "safe-post", source_dir, [image], copied)
        expected_dir = Path(self._temp.name) / "media" / "posts" / "safe-post"
        self.assertEqual(copied[0].parent, expected_dir)
        self.assertTrue(copied[0].is_file())
        self.assertIn("/media/posts/safe-post/escape.png", content)
        self.assertFalse((Path(self._temp.name) / "escape.png").exists())

    def test_copied_file_is_removed_when_database_save_fails(self):
        source_dir = Path(self._temp.name) / "source"
        source_dir.mkdir()
        image = source_dir / "figure.png"
        image.write_bytes(b"test")
        copied = []
        with patch("posts.services.Post.save", side_effect=IntegrityError("simulated")):
            with self.assertRaises(ContentProcessError):
                process_markdown_content(
                    "Body ![](figure.png)", [image], source_dir,
                    overrides={"title": "Rollback", "slug": "rollback", "category": "engineering"},
                    copied_files=copied,
                )
        self.assertEqual(copied, [])
        self.assertFalse((Path(self._temp.name) / "media" / "posts" / "rollback" / "figure.png").exists())


class ContentServiceTests(TemporaryMediaTestCase):
    def test_duplicate_slug_never_overwrites_existing_content(self):
        original = Post.objects.create(
            title="Original", slug="same-slug", content="original body",
            category="engineering", date=timezone.now(), published=True,
        )
        with self.assertRaises(ContentProcessError) as caught:
            process_markdown_content(
                "replacement", [], None,
                overrides={"title": "Replacement", "slug": original.slug, "category": "engineering"},
            )
        self.assertEqual(caught.exception.field, "slug")
        original.refresh_from_db()
        self.assertEqual(original.content, "original body")
        self.assertEqual(Post.objects.count(), 1)

    def test_non_project_update_preserves_project_metadata(self):
        post = Post.objects.create(
            title="Legacy", slug="legacy", content="old", category="engineering",
            date=timezone.now(), github_url="https://github.com/example/repo",
            paper_url="https://example.com/paper", project_featured=True,
        )
        process_markdown_content(
            "new", [], None, update_existing=True,
            overrides={"title": "Legacy", "slug": post.slug, "category": "engineering"},
        )
        post.refresh_from_db()
        self.assertEqual(post.github_url, "https://github.com/example/repo")
        self.assertTrue(post.project_featured)

    def test_import_update_cannot_convert_article_and_project(self):
        project = Project.objects.create(title="Project", slug="project", content="body", date=timezone.now())
        with self.assertRaises(ContentProcessError) as caught:
            process_markdown_content(
                "new", [], None, update_existing=True,
                overrides={"title": "Project", "slug": project.slug, "category": "engineering"},
            )
        self.assertEqual(caught.exception.field, "category")
        project.refresh_from_db()
        self.assertEqual(project.category, "projects")

    def test_project_links_tags_and_featured_can_be_cleared(self):
        project = Project.objects.create(
            title="Clear", slug="clear", content="body", date=timezone.now(),
            github_url="https://github.com/example/repo", paper_url="https://example.com/paper",
            demo_url="https://example.com/demo", project_featured=True,
            project_tech_stack="Python, Django",
        )
        project.tags.add("research")
        form = ProjectModelForm(instance=project, data={
            "title": "Clear", "slug": "clear", "description": "", "project_status": "active",
            "project_tech_stack": "", "tags": "", "github_url": "", "paper_url": "",
            "demo_url": "", "content": "body", "date": project.date.strftime("%Y-%m-%d %H:%M:%S"),
            "published": "1",
        })
        self.assertTrue(form.is_valid(), form.errors)
        project = form.save()
        self.assertEqual(project.github_url, "")
        self.assertEqual(project.paper_url, "")
        self.assertEqual(project.demo_url, "")
        self.assertFalse(project.project_featured)
        self.assertEqual(project.tags.count(), 0)


def article_payload(**overrides):
    data = {
        "title": "Workflow article", "slug": "workflow-article", "description": "Summary",
        "category": "engineering", "tags": "django, testing", "date": "",
        "content": "## Heading\n\nSafe body", "series": "", "series_order": "",
        "action": "draft",
    }
    data.update(overrides)
    return data


def project_payload(**overrides):
    data = {
        "title": "Workflow project", "slug": "workflow-project", "description": "Project summary",
        "project_status": "active", "project_tech_stack": "Django, Python", "tags": "research",
        "github_url": "https://github.com/example/project", "paper_url": "", "demo_url": "",
        "date": "", "content": "## Abstract\n\nProject body", "action": "draft",
    }
    data.update(overrides)
    return data


class AdminPermissionAndCsrfTests(TestCase):
    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser("workflow-admin", "admin@example.com", "password")
        self.staff = get_user_model().objects.create_user("limited-staff", "staff@example.com", "password", is_staff=True)

    def test_staff_without_model_permission_gets_403(self):
        self.client.force_login(self.staff)
        for url in (
            reverse("admin_upload"), reverse("admin_project_upload"),
            reverse("admin_post_preview_markdown"), reverse("admin_post_generate_tags"),
            reverse("admin_post_generate_summary"),
        ):
            response = self.client.post(url, {"content": "body"}) if "preview-markdown" in url or "generate" in url else self.client.get(url)
            self.assertEqual(response.status_code, 403, url)

    def test_article_permission_does_not_grant_project_creation(self):
        self.staff.user_permissions.add(Permission.objects.get(codename="add_post"))
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("admin_upload")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin_project_upload")).status_code, 403)

    def test_csrf_is_required_for_workflow_and_preview_posts(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.superuser)
        client.get(reverse("admin_upload_blank"))
        response = client.post(reverse("admin_upload_preview"), article_payload())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Post.objects.filter(slug="workflow-article").exists())
        self.assertEqual(client.post(reverse("admin_post_preview_markdown"), {"content": "body"}).status_code, 403)
        self.assertEqual(client.post(reverse("admin_project_upload"), {}).status_code, 403)

    def test_anonymous_upload_is_redirected_not_rendered(self):
        response = self.client.get(reverse("admin_upload"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)

    def test_admin_login_uses_the_new_static_favicon_set(self):
        response = self.client.get(reverse("admin:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'rel="icon"')
        self.assertContains(response, static("images/favicon.svg"))
        self.assertContains(response, static("images/favicon-32.png"))
        self.assertContains(response, static("images/apple-touch-icon.png"))

    def test_admin_uses_django_classic_blue_grey_palette(self):
        colors = settings.UNFOLD["COLORS"]
        self.assertEqual(colors["primary"]["600"], "65 118 144")  # #417690
        self.assertEqual(colors["primary"]["400"], "121 174 200")  # #79aec8
        self.assertEqual(colors["primary"]["200"], "196 220 232")  # #c4dce8
        self.assertEqual(colors["base"]["50"], "248 248 248")

    def test_standard_article_and_project_change_pages_render(self):
        self.client.force_login(self.superuser)
        article = Post.objects.create(title="Article", slug="standard-article", content="body", category="engineering", date=timezone.now())
        project = Project.objects.create(title="Project", slug="standard-project", content="body", date=timezone.now())
        article_page = self.client.get(reverse("admin:posts_post_change", args=[article.pk]))
        project_page = self.client.get(reverse("admin:posts_project_change", args=[project.pk]))
        self.assertEqual(article_page.status_code, 200)
        self.assertEqual(project_page.status_code, 200)
        self.assertNotContains(article_page, 'name="upload_file"')
        self.assertContains(project_page, "实验图片与视频")
        self.assertContains(project_page, "project_media-TOTAL_FORMS")


class ArticleWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("article-admin", "article@example.com", "password")
        self.client.force_login(self.user)

    def test_draft_then_public_publish_boundaries(self):
        self.client.get(reverse("admin_upload_blank"))
        response = self.client.post(reverse("admin_upload_preview"), article_payload())
        self.assertEqual(response.status_code, 302)
        post = Post.objects.get(slug="workflow-article")
        self.assertFalse(post.published)
        self.assertEqual(self.client.get(post.get_absolute_url()).status_code, 404)

        self.client.get(reverse("admin_upload_blank"))
        response = self.client.post(reverse("admin_upload_preview"), article_payload(
            title="Published article", slug="published-article", action="publish",
            content="Body <script>alert(1)</script>",
        ))
        self.assertEqual(response.status_code, 302)
        public = self.client.get(reverse("posts:post_detail", args=["published-article"]))
        self.assertEqual(public.status_code, 200)
        self.assertNotContains(public, "<script>alert(1)</script>")

    def test_duplicate_slug_rerenders_typed_content_and_keeps_session(self):
        Post.objects.create(title="Existing", slug="workflow-article", content="keep", category="engineering", date=timezone.now())
        self.client.get(reverse("admin_upload_blank"))
        response = self.client.post(reverse("admin_upload_preview"), article_payload(content="Do not lose this text"))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Do not lose this text", status_code=400)
        self.assertContains(response, "已存在", status_code=400)
        self.assertEqual(Post.objects.get(slug="workflow-article").content, "keep")
        self.assertEqual(self.client.session[ARTICLE_SESSION_KEY]["content"], "Do not lose this text")

    def test_unexpected_content_error_keeps_draft_session(self):
        self.client.get(reverse("admin_upload_blank"))
        with patch("posts.admin_uploads.process_markdown_content", side_effect=ContentProcessError("simulated failure", "content")):
            response = self.client.post(reverse("admin_upload_preview"), article_payload(action="publish"))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "simulated failure", status_code=400)
        self.assertIn(ARTICLE_SESSION_KEY, self.client.session)
        self.assertFalse(Post.objects.filter(slug="workflow-article").exists())


class ProjectWorkflowTests(TemporaryMediaTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser("project-admin", "project@example.com", "password")
        self.client.force_login(self.user)

    def test_blank_project_publishes_through_proxy_not_article_admin(self):
        self.client.get(reverse("admin_project_upload_blank"))
        response = self.client.post(reverse("admin_project_upload_preview"), project_payload(action="publish"))
        self.assertEqual(response.status_code, 302)
        project = Project.objects.get(slug="workflow-project")
        self.assertTrue(project.published)
        self.assertEqual(project.category, "projects")
        self.assertEqual(self.client.get(project.get_absolute_url()).status_code, 200)
        article_list = self.client.get(reverse("admin:posts_post_changelist"))
        project_list = self.client.get(reverse("admin:posts_project_changelist"))
        self.assertFalse(article_list.context["cl"].queryset.filter(pk=project.pk).exists())
        self.assertTrue(project_list.context["cl"].queryset.filter(pk=project.pk).exists())

    def test_staged_media_is_owned_by_project_and_isolated_from_other_projects(self):
        self.client.get(reverse("admin_project_upload_blank"))
        response = self.client.post(reverse("admin_project_upload_preview"), project_payload(
            action="add_media", new_media_files=self.image(), new_media_section="method", new_media_group="Vision",
        ))
        self.assertEqual(response.status_code, 302)
        session = self.client.session[PROJECT_SESSION_KEY]
        self.assertEqual(len(session["project_media"]), 1)
        item = session["project_media"][0]
        response = self.client.post(reverse("admin_project_upload_preview"), project_payload(
            action="publish", **{
                f"media_{item['id']}_include": "1", f"media_{item['id']}_title": "Only this project",
                f"media_{item['id']}_section": "method", f"media_{item['id']}_group_name": "Vision",
                f"media_{item['id']}_order": "1", f"media_{item['id']}_description": "Method diagram",
            },
        ))
        self.assertEqual(response.status_code, 302)
        project = Post.objects.get(slug="workflow-project")
        media = ProjectMedia.objects.get(project=project)
        self.assertTrue(Path(media.file.path).is_file())
        self.assertEqual(media.section, "method")
        page = self.client.get(project.get_absolute_url())
        self.assertContains(page, "Only this project")
        self.assertContains(page, media.file.url)
        other = Project.objects.create(title="Other", slug="other", content="Other body", date=timezone.now(), published=True)
        other_page = self.client.get(other.get_absolute_url())
        self.assertNotContains(other_page, "Only this project")
        self.assertNotContains(other_page, media.file.url)
        self.assertNotContains(other_page, "robo-imagine/")

    def test_media_database_failure_rolls_back_post_and_deletes_new_file(self):
        self.client.get(reverse("admin_project_upload_blank"))
        self.client.post(reverse("admin_project_upload_preview"), project_payload(
            action="add_media", new_media_files=self.image(), new_media_section="results",
        ))
        item = self.client.session[PROJECT_SESSION_KEY]["project_media"][0]
        payload = project_payload(action="publish", **{
            f"media_{item['id']}_include": "1", f"media_{item['id']}_title": "Rollback media",
            f"media_{item['id']}_section": "results", f"media_{item['id']}_group_name": "",
            f"media_{item['id']}_order": "0", f"media_{item['id']}_description": "",
        })
        with patch("posts.admin_uploads.ProjectMedia.save", side_effect=IntegrityError("simulated media failure")):
            response = self.client.post(reverse("admin_project_upload_preview"), payload)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "数据库已回滚", status_code=400)
        self.assertFalse(Post.objects.filter(slug="workflow-project").exists())
        media_root = Path(self._temp.name) / "media" / "projects" / "workflow-project"
        self.assertFalse(media_root.exists() and any(media_root.rglob("*.png")))
        self.assertIn(PROJECT_SESSION_KEY, self.client.session)
        self.assertEqual(len(self.client.session[PROJECT_SESSION_KEY]["project_media"]), 1)


class PublicProjectAndSeriesTests(TemporaryMediaTestCase):
    def test_article_navigation_stays_in_its_category_even_with_a_series(self):
        series = Series.objects.create(title='Reading path', slug='reading-path')
        for category, label in [('engineering', 'Engineering'), ('research', 'Research'), ('notes', 'Notes')]:
            with self.subTest(category=category):
                post = Post.objects.create(
                    title=label, slug=category + '-navigation', content='# Costs & Limits',
                    category=category, series=series, published=True, date=timezone.now(),
                )
                page = self.client.get(post.get_absolute_url())
                url = reverse('posts:' + category + '_list')
                for nav_class in ['nav-link', 'mobile-nav-link']:
                    self.assertContains(page, f'<a href="{url}" class="{nav_class}" aria-current="page">{label}</a>', html=True)
                    self.assertNotContains(page, f'<a href="{reverse("posts:series_list")}" class="{nav_class}" aria-current="page">Series</a>', html=True)
                self.assertContains(page, f'<a href="{url}" class="back-link">← Back to {label}</a>', html=True)
                self.assertContains(page, 'Costs &amp; Limits')
                self.assertNotContains(page, 'Costs &amp;amp; Limits')

    def test_featured_projects_precede_newer_regular_projects(self):
        old_featured = Project.objects.create(
            title="Featured", slug="featured", content="body", published=True,
            project_featured=True, date=timezone.now() - timedelta(days=3),
        )
        Project.objects.create(title="New", slug="new", content="body", published=True, date=timezone.now())
        page = self.client.get(reverse("posts:projects_list"))
        self.assertEqual(list(page.context["posts"]), [old_featured, Post.objects.get(slug="new")])
        home = self.client.get(reverse("posts:index"))
        self.assertEqual(list(home.context["projects"])[0].pk, old_featured.pk)

    def test_home_and_project_hero_use_only_the_current_project_image(self):
        project = Project.objects.create(title="Visual", slug="visual-project", content="body", published=True, project_featured=True, date=timezone.now())
        other = Project.objects.create(title="Other", slug="other-project", content="body", published=True, date=timezone.now())
        image = ProjectMedia.objects.create(project=project, file=self.image("framework.png"), kind="image", section="framework", title="Current framework")
        other_image = ProjectMedia.objects.create(project=other, file=self.image("other.png"), kind="image", section="framework", title="Other framework")
        home = self.client.get(reverse("posts:index"))
        self.assertContains(home, image.file.url)
        home_projects = {item.pk: item for item in home.context["projects"]}
        self.assertEqual(home_projects[project.pk].card_image, image)
        self.assertEqual(home_projects[other.pk].card_image, other_image)
        projects_index = self.client.get(reverse("posts:projects_list"))
        indexed_projects = {item.pk: item for item in projects_index.context["posts"]}
        self.assertEqual(indexed_projects[project.pk].card_image, image)
        self.assertEqual(indexed_projects[other.pk].card_image, other_image)
        self.assertContains(projects_index, image.file.url)
        self.assertContains(projects_index, other_image.file.url)
        page = self.client.get(project.get_absolute_url())
        self.assertContains(page, "Current framework")
        self.assertEqual(page.content.decode().count(image.file.url), 2)
        self.assertNotContains(page, other_image.file.url)

    def test_public_interface_remains_english_after_visual_redesign(self):
        Post.objects.create(title="Latest", slug="latest-english-ui", content="body", published=True, date=timezone.now())
        page = self.client.get(reverse("posts:index"))
        self.assertContains(page, '<html lang="en">', html=False)
        for label in ("Engineering", "Research", "Notes", "Projects", "Series", "Latest Article"):
            self.assertContains(page, label)
        self.assertNotContains(page, "构建代码")
        self.assertNotContains(page, "Build with care.")

    def test_public_brand_uses_original_avatar_and_full_byline(self):
        page = self.client.get(reverse("posts:index"))
        self.assertContains(page, "by Egbert Lannister")
        self.assertContains(page, static("images/avatar-96.webp"))
        self.assertContains(page, static("images/avatar-192.webp"))
        self.assertContains(page, 'class="site-brand-avatar"')

    def test_draft_project_is_private(self):
        project = Project.objects.create(title="Draft", slug="draft", content="secret", published=False, date=timezone.now())
        self.assertEqual(self.client.get(project.get_absolute_url()).status_code, 404)
        self.assertNotContains(self.client.get(reverse("posts:index")), "Draft")

    def test_home_adapts_series_layout_when_only_lead_article_exists(self):
        empty = self.client.get(reverse("posts:index"))
        self.assertNotContains(empty, 'class="home-lower"')
        Post.objects.create(title="Only article", slug="only-article", content="body", published=True, date=timezone.now())
        Series.objects.create(title="Featured path", slug="featured-path", is_featured=True)
        page = self.client.get(reverse("posts:index"))
        self.assertContains(page, "home-lower home-lower-series-only")
        self.assertNotContains(page, 'class="home-writing"')

    def test_series_contains_articles_not_projects(self):
        series = Series.objects.create(title="Series", slug="series")
        article = Post.objects.create(title="Article", slug="article", content="body", category="engineering", series=series, published=True, date=timezone.now())
        Project.objects.create(title="Project", slug="series-project", content="body", series=series, published=True, date=timezone.now())
        self.assertEqual(list(series.get_posts()), [article])

    def test_project_video_uses_associated_file_and_safe_playback_attributes(self):
        project = Project.objects.create(title="Video", slug="video-project", content="body", published=True, date=timezone.now())
        media = ProjectMedia.objects.create(
            project=project, file=SimpleUploadedFile("demo.mp4", b"video", content_type="video/mp4"),
            kind="video", section="results", group_name="Seen", title="Associated video",
        )
        page = self.client.get(project.get_absolute_url())
        self.assertContains(page, "Associated video")
        self.assertContains(page, media.file.url)
        self.assertContains(page, 'controls playsinline preload="metadata"')
        self.assertContains(page, "project-media-row-1")
        self.assertContains(page, "project-media-frame")
        self.assertContains(page, "experiment-group-header")
        self.assertContains(page, "Seen")
        self.assertNotContains(page, "autoplay")
        self.assertNotContains(page, "/static/projects/robo-imagine/")

    def test_project_media_cannot_belong_to_an_article(self):
        article = Post.objects.create(title="Article", slug="plain-article", content="body", category="engineering", date=timezone.now())
        media = ProjectMedia(project=article, file=self.image(), kind="image", section="gallery", title="Invalid")
        with self.assertRaisesMessage(ValidationError, "媒体只能关联"):
            media.full_clean()
