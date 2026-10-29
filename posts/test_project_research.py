from __future__ import annotations

import io
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation
from PIL import Image

from .admin_uploads import PROJECT_SESSION_KEY
from .models import Post, ProjectMedia
from .project_layout import build_media_sections, layout_media_group
from .templatetags.english_dates import english_date


def png_bytes(size=(8, 6)):
    stream = io.BytesIO()
    Image.new("RGB", size, "#2a9d66").save(stream, "PNG")
    return stream.getvalue()


def html_zip():
    stream = io.BytesIO()
    html = b'''<!doctype html><html lang="en"><head><title>Safe HTML project</title>
<meta name="description" content="A safely imported research demo"></head><body><main>
<section class="project-html-section"><h1>Safe project</h1><p>Overview text.</p>
<script>alert(1)</script><figure data-section="results" data-group="Demo experiment">
<img src="assets/demo.png" alt="Demo" onerror="alert(2)"><figcaption>Demo evidence</figcaption>
</figure></section></main></body></html>'''
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("index.html", html)
        bundle.writestr("assets/demo.png", png_bytes((11, 7)))
    return SimpleUploadedFile("project.zip", stream.getvalue(), content_type="application/zip")


def layout_item(orientation="standard", *, video=False, section="results", group="Experiment"):
    return SimpleNamespace(is_video=video, orientation=orientation, section=section, group_name=group)


class ResearchLayoutAndDateTests(TestCase):
    def test_english_dates_ignore_active_locale(self):
        value = datetime(2026, 10, 24, 12, 0, tzinfo=datetime_timezone.utc)
        with translation.override("zh-hans"):
            self.assertEqual(english_date(value), "October 24, 2026")
            self.assertEqual(english_date(value, "short"), "Oct 24, 2026")
            self.assertEqual(english_date(value, "month_year"), "Oct 2026")

    def test_rows_keep_items_and_balance_four_five_and_nine(self):
        for count, expected in ((1, [1]), (2, [2]), (3, [3]), (4, [2, 2]), (5, [3, 2]), (9, [3, 3, 3])):
            with self.subTest(count=count):
                rows = layout_media_group([layout_item() for _ in range(count)])
                self.assertEqual([row["count"] for row in rows], expected)
                self.assertEqual([len(row["items"]) for row in rows], expected)
                self.assertEqual(sum(len(row["items"]) for row in rows), count)

    def test_orientation_and_logical_groups_control_layout(self):
        rows = layout_media_group([layout_item("wide"), layout_item(), layout_item()])
        self.assertEqual([(row["frame"], row["count"]) for row in rows], [("wide", 1), ("standard", 2)])
        portraits = layout_media_group([layout_item("portrait"), layout_item("portrait")])
        self.assertEqual(portraits[0]["frame"], "portrait")
        sections = build_media_sections([layout_item(group="Bridge"), layout_item(section="comparison", group="VLM"), layout_item(section="comparison", group="VLM")])
        self.assertEqual([section["key"] for section in sections], ["results", "comparison"])
        self.assertEqual(sections[0]["groups"][0]["title"], "Bridge")
        self.assertEqual(len(sections[1]["groups"][0]["rows"][0]["items"]), 2)


class ResearchWorkflowTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="research_workflow_")
        self.addCleanup(self.temporary.cleanup)
        self.media_root = Path(self.temporary.name) / "media"
        self.media_root.mkdir()
        self.settings_override = override_settings(MEDIA_ROOT=str(self.media_root))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = get_user_model().objects.create_superuser("research-admin", "research@example.com", "strong-test-password")
        self.client.force_login(self.user)

    def payload(self, slug="structured-project", title="Structured project"):
        return {
            "editor_token": self.client.session[PROJECT_SESSION_KEY]["editor_token"],
            "title": title, "slug": slug, "description": "A concise summary", "tags": "robotics, evaluation", "date": "",
            "content": "The overview states the problem.", "project_method": "The method uses a controlled pipeline.",
            "project_experiments": "Experiments compare two settings.", "project_conclusion": "The conclusion states the verified scope.",
            "project_status": "active", "project_tech_stack": "Python, Django", "github_url": "", "paper_url": "", "demo_url": "",
            "new_media_section": "results", "new_media_group": "",
        }

    def upload_html_draft(self):
        response = self.client.post(reverse("admin_project_upload"), {"import_mode": "html", "upload_file": html_zip()})
        self.assertEqual(response.status_code, 302)
        data = self.client.session.get(PROJECT_SESSION_KEY)
        self.assertIsNotNone(data)
        self.addCleanup(shutil.rmtree, data.get("staging_dir", ""), ignore_errors=True)
        return data

    def test_media_metadata_is_read_from_real_files(self):
        project = Post.objects.create(title="Metadata", slug="metadata-project", category="projects", content="body", date=timezone.now())
        image = ProjectMedia(project=project, file=ContentFile(png_bytes((13, 9)), name="figure.png"), kind="image", title="Figure")
        image.save()
        self.assertEqual((image.width, image.height), (13, 9))
        self.assertGreater(image.file_size, 0)
        video = ProjectMedia(project=project, file=ContentFile(b"demo-video", name="rollout.mp4"), kind="video", title="Rollout")
        video.save()
        self.assertEqual((video.width, video.height), (0, 0))
        self.assertEqual(video.file_size, len(b"demo-video"))

    def test_structured_page_renders_paper_sections_and_media(self):
        project = Post.objects.create(
            title="Research", slug="research-page", category="projects", content="Overview", published=True,
            project_method="Method", project_experiments="Evidence", project_conclusion="Conclusion", date=timezone.now(),
        )
        for index, title in enumerate(("Rollout A", "Rollout B"), 1):
            ProjectMedia.objects.create(project=project, file=ContentFile(b"video", name=f"rollout-{index}.mp4"), kind="video", section="results", group_name="Bridge · Seen tasks", title=title, order=index)
        response = self.client.get(reverse("posts:project_detail", args=[project.slug]))
        for text in ("01 / Overview", "02 / Approach", "03 / Evidence", "04 / Conclusion", "Experiment 01", "Bridge · Seen tasks", "Rollout A", "Rollout B"):
            self.assertContains(response, text)
        self.assertContains(response, "project-media-row-2")
        self.assertContains(response, 'playsinline preload="metadata"')

    def test_html_mode_is_sanitized_again_at_public_render(self):
        project = Post.objects.create(
            title="HTML", slug="html-render", category="projects", published=True, project_content_mode="html",
            content='<section class="project-html-section"><h2>Safe</h2><script>alert(9)</script><img src="/media/example.png" onerror="alert(8)"></section>', date=timezone.now(),
        )
        response = self.client.get(reverse("posts:project_detail", args=[project.slug]))
        self.assertContains(response, "HTML package")
        self.assertContains(response, "<h2>Safe</h2>", html=True)
        self.assertNotContains(response, "alert(9)")
        self.assertNotContains(response, "onerror=")

    def test_manual_editor_exposes_structured_sections_and_persists_them(self):
        self.client.get(reverse("admin_project_upload_blank"))
        editor = self.client.get(reverse("admin_project_upload_preview"))
        for field in ("id_content", "id_project_method", "id_project_experiments", "id_project_conclusion"):
            self.assertContains(editor, field)
        for text in ("01 · 基本信息", "05 · 媒体与自动布局", "06 · Overview", "07 · Method", "08 · Experiments", "由 Overview 生成摘要"):
            self.assertContains(editor, text)
        response = self.client.post(reverse("admin_project_upload_preview"), {**self.payload(), "action": "draft"})
        self.assertEqual(response.status_code, 302)
        project = Post.objects.get(slug="structured-project")
        self.assertEqual(project.category, "projects")
        self.assertFalse(project.published)
        self.assertEqual(project.project_content_mode, "structured")
        self.assertEqual(project.project_method, "The method uses a controlled pipeline.")
        self.assertEqual(project.project_conclusion, "The conclusion states the verified scope.")

    def test_html_upload_preview_publish_and_public_delivery(self):
        self.upload_html_draft()
        preview = self.client.get(reverse("admin_project_upload_preview"))
        self.assertContains(preview, "已清理的页面")
        self.assertNotContains(preview, "alert(1)")
        response = self.client.post(reverse("admin_project_upload_preview"), {**self.payload("safe-html-project", "Safe HTML project"), "action": "publish"})
        self.assertEqual(response.status_code, 302)
        project = Post.objects.get(slug="safe-html-project")
        self.assertTrue(project.published)
        self.assertEqual(project.project_content_mode, "html")
        self.assertEqual(project.project_media.count(), 1)
        media = project.project_media.get()
        self.assertEqual((media.width, media.height), (11, 7))
        self.assertIn(media.file.url, project.content)
        public = self.client.get(reverse("posts:project_detail", args=[project.slug]))
        self.assertContains(public, media.file.url)
        self.assertContains(public, "Demo evidence")
        self.assertNotContains(public, "alert(1)")
        self.assertNotContains(public, "onerror=")
        self.assertNotIn(PROJECT_SESSION_KEY, self.client.session)

    def test_html_save_failure_rolls_back_database_and_storage(self):
        self.upload_html_draft()
        with patch("posts.admin_uploads.ProjectMedia.save", side_effect=RuntimeError("simulated failure")):
            response = self.client.post(reverse("admin_project_upload_preview"), {**self.payload("html-rollback", "HTML rollback"), "action": "publish"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Post.objects.filter(slug="html-rollback").exists())
        self.assertEqual(list(self.media_root.rglob("*.png")), [])
        self.assertIn(PROJECT_SESSION_KEY, self.client.session)

    def test_html_duplicate_slug_never_overwrites_existing_project(self):
        existing = Post.objects.create(title="Existing", slug="existing-project", category="projects", content="Keep me", date=timezone.now())
        self.upload_html_draft()
        response = self.client.post(reverse("admin_project_upload_preview"), {**self.payload(existing.slug, "Replacement"), "action": "publish"})
        self.assertEqual(response.status_code, 400)
        existing.refresh_from_db()
        self.assertEqual(existing.content, "Keep me")
        self.assertEqual(Post.objects.filter(slug=existing.slug).count(), 1)
