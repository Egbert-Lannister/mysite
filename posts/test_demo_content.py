from __future__ import annotations

import hashlib
import re
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone as datetime_timezone
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from .models import Post, Project, ProjectMedia, Series


ARTICLE_SLUGS = (
    "demo-idempotent-deployment-jobs",
    "demo-backward-compatible-schema-changes",
    "demo-reproducible-evaluation-harness",
    "demo-dataset-slices-as-questions",
    "demo-debugging-hypothesis-ledger",
    "demo-design-review-checklist",
)
PROJECT_SLUGS = (
    "demo-media-layout-workbench",
    "demo-editorial-release-checklist",
)
DEMO_SLUGS = (*ARTICLE_SLUGS, *PROJECT_SLUGS)
MEDIA_PROJECT_SLUG = "demo-media-layout-workbench"
EXPECTED_MEDIA_SIZES = {
    (1600, 640),
    (1200, 800),
    (1000, 1000),
    (800, 1200),
    (320, 240),
}


def _model_has_field(model, name):
    return name in {field.name for field in model._meta.get_fields()}


class DemoContentCommandTests(TestCase):
    def setUp(self):
        super().setUp()
        self._temp = tempfile.TemporaryDirectory()
        self.media_root = Path(self._temp.name) / "media"
        self._media_override = override_settings(MEDIA_ROOT=self.media_root)
        self._media_override.enable()
        self.addCleanup(self._temp.cleanup)
        self.addCleanup(self._media_override.disable)

    def seed(self, *args):
        output = StringIO()
        call_command("seed_demo_content", *args, stdout=output, verbosity=1)
        return output.getvalue()

    def media_file_digests(self):
        if not self.media_root.exists():
            return {}
        return {
            str(path.relative_to(self.media_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.media_root.rglob("*")
            if path.is_file()
        }

    def test_first_and_second_runs_keep_rows_and_files_unchanged(self):
        fixed_date = datetime(2026, 1, 5, 8, 0, tzinfo=datetime_timezone.utc)
        user_post = Post.objects.create(
            title="Keep User Content",
            slug="user-existing-content",
            description="Owned by the user",
            content="This body must remain untouched.",
            category="engineering",
            published=True,
            date=fixed_date,
        )
        robo = Post.objects.create(
            title="Robo-Imagine",
            slug="robo-imagine",
            description="Existing project",
            content="Existing Robo content must remain untouched.",
            category="projects",
            published=True,
            date=fixed_date,
        )

        self.seed()
        first_counts = (Post.objects.count(), Series.objects.count(), ProjectMedia.objects.count())
        first_files = self.media_file_digests()
        first_rows = list(Post.objects.order_by("slug").values_list("slug", "updated_at", "content_hash"))
        first_media_names = list(ProjectMedia.objects.order_by("title").values_list("file", flat=True))
        self.assertEqual(first_counts, (10, 2, 5))
        self.assertEqual(len(first_files), 5)

        second_output = self.seed()
        self.assertEqual(
            (Post.objects.count(), Series.objects.count(), ProjectMedia.objects.count()),
            first_counts,
        )
        self.assertEqual(self.media_file_digests(), first_files)
        self.assertEqual(
            list(Post.objects.order_by("slug").values_list("slug", "updated_at", "content_hash")),
            first_rows,
        )
        self.assertEqual(
            list(ProjectMedia.objects.order_by("title").values_list("file", flat=True)),
            first_media_names,
        )
        self.assertIn("Created: 0 series, 0 posts, 0 media", second_output)

        user_post.refresh_from_db()
        robo.refresh_from_db()
        self.assertEqual(user_post.content, "This body must remain untouched.")
        self.assertEqual(user_post.description, "Owned by the user")
        self.assertEqual(robo.content, "Existing Robo content must remain untouched.")
        self.assertEqual(robo.description, "Existing project")
        self.assertFalse(ProjectMedia.objects.filter(project=robo).exists())

    def test_articles_are_substantive_english_with_aware_dates_and_series_order(self):
        self.seed()
        articles = Post.objects.filter(slug__in=ARTICLE_SLUGS)
        self.assertEqual(articles.count(), 6)
        self.assertEqual(
            Counter(articles.values_list("category", flat=True)),
            Counter({"engineering": 2, "research": 2, "notes": 2}),
        )
        start = datetime(2026, 8, 1, tzinfo=datetime_timezone.utc)
        end = datetime(2026, 11, 1, tzinfo=datetime_timezone.utc)
        for article in articles:
            combined = f"{article.title}\n{article.description}\n{article.content}"
            self.assertIsNone(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", combined))
            self.assertNotIn("lorem ipsum", combined.lower())
            self.assertGreater(len(article.description), 100)
            self.assertGreater(len(article.content), 1000)
            self.assertTrue(article.published)
            self.assertTrue(timezone.is_aware(article.date))
            self.assertTrue(start <= article.date < end)
            self.assertEqual(article.date.tzinfo, datetime_timezone.utc)
            self.assertEqual(len(article.content_hash), 64)

        engineering = Series.objects.get(slug="demo-reliable-engineering-practice")
        evaluation = Series.objects.get(slug="demo-evaluation-practice")
        self.assertEqual(
            list(engineering.posts.order_by("series_order").values_list("slug", "series_order")),
            [
                ("demo-idempotent-deployment-jobs", 1),
                ("demo-backward-compatible-schema-changes", 2),
            ],
        )
        self.assertEqual(
            list(evaluation.posts.order_by("series_order").values_list("slug", "series_order")),
            [
                ("demo-reproducible-evaluation-harness", 1),
                ("demo-dataset-slices-as-questions", 2),
            ],
        )
        for article in articles.filter(category="notes"):
            self.assertIsNone(article.series)
            self.assertIsNone(article.series_order)

    def test_projects_and_original_media_have_correct_ownership_sections_and_sizes(self):
        self.seed()
        projects = Project.objects.filter(slug__in=PROJECT_SLUGS)
        self.assertEqual(projects.count(), 2)
        for project in projects:
            self.assertEqual(project.category, "projects")
            self.assertTrue(project.published)
            self.assertEqual(project.github_url, "")
            self.assertEqual(project.demo_url, "")
            self.assertEqual(project.paper_url, "")
            self.assertIn("## Overview", project.content)
            for field_name, heading in (
                ("project_method", "Method"),
                ("project_experiments", "Experiments"),
                ("project_conclusion", "Conclusion"),
            ):
                if _model_has_field(Post, field_name):
                    self.assertTrue(getattr(project, field_name))
                else:
                    self.assertIn(f"## {heading}", project.content)

        media_project = Post.objects.get(slug=MEDIA_PROJECT_SLUG)
        other_project = Post.objects.get(slug="demo-editorial-release-checklist")
        media_items = ProjectMedia.objects.all()
        self.assertEqual(media_items.count(), 5)
        self.assertFalse(ProjectMedia.objects.filter(project=other_project).exists())
        self.assertEqual(
            {item.section for item in media_items},
            {"method", "results", "comparison", "gallery"},
        )

        actual_sizes = set()
        for item in media_items:
            self.assertEqual(item.project_id, media_project.pk)
            self.assertEqual(item.kind, ProjectMedia.KIND_IMAGE)
            self.assertTrue(item.title.startswith("Illustrative Demo:"))
            self.assertTrue(item.description)
            self.assertTrue(item.file.name.startswith(f"projects/{MEDIA_PROJECT_SLUG}/"))
            self.assertNotIn("robo-imagine", item.file.name)
            path = Path(item.file.path)
            self.assertTrue(path.is_file())
            with Image.open(path) as image:
                image.load()
                self.assertEqual(image.format, "PNG")
                actual_sizes.add(image.size)
                if _model_has_field(ProjectMedia, "width"):
                    self.assertEqual(item.width, image.width)
                if _model_has_field(ProjectMedia, "height"):
                    self.assertEqual(item.height, image.height)
            if _model_has_field(ProjectMedia, "file_size"):
                self.assertEqual(item.file_size, path.stat().st_size)
        self.assertEqual(actual_sizes, EXPECTED_MEDIA_SIZES)

    def test_dry_run_writes_nothing_and_reports_no_missing_items_after_seed(self):
        output = self.seed("--dry-run")
        self.assertIn("DRY RUN", output)
        self.assertIn("Would create 2 series", output)
        self.assertIn("Would create 8 posts", output)
        self.assertIn("Would create 5 media", output)
        self.assertEqual(Post.objects.count(), 0)
        self.assertEqual(Series.objects.count(), 0)
        self.assertEqual(ProjectMedia.objects.count(), 0)
        self.assertEqual(self.media_file_digests(), {})

        self.seed()
        first_files = self.media_file_digests()
        second_output = self.seed("--dry-run")
        self.assertIn("Would create 0 series", second_output)
        self.assertIn("Would create 0 posts", second_output)
        self.assertIn("Would create 0 media", second_output)
        self.assertEqual(self.media_file_digests(), first_files)
        self.assertEqual(Post.objects.filter(slug__in=DEMO_SLUGS).count(), 8)
        self.assertEqual(ProjectMedia.objects.count(), 5)


class FaviconAssetTests(SimpleTestCase):
    def test_svg_is_simple_brand_geometry_and_png_sizes_are_correct(self):
        image_dir = Path(settings.BASE_DIR) / "static" / "images"
        svg_path = image_dir / "favicon.svg"
        self.assertTrue(svg_path.is_file())
        root = ET.parse(svg_path).getroot()
        namespace = "{http://www.w3.org/2000/svg}"
        self.assertEqual(root.attrib["viewBox"], "0 0 64 64")
        self.assertEqual(len(root.findall(f".//{namespace}rect")), 1)
        self.assertEqual(len(root.findall(f".//{namespace}path")), 1)
        circles = root.findall(f".//{namespace}circle")
        self.assertEqual(len(circles), 1)
        self.assertEqual(circles[0].attrib["fill"], "#63D69C")
        for forbidden in ("text", "linearGradient", "radialGradient", "filter"):
            self.assertIsNone(root.find(f".//{namespace}{forbidden}"))

        for filename, expected_size in (
            ("favicon-32.png", (32, 32)),
            ("apple-touch-icon.png", (180, 180)),
            ("favicon-192.png", (192, 192)),
            ("favicon-512.png", (512, 512)),
        ):
            with self.subTest(filename=filename):
                path = image_dir / filename
                self.assertTrue(path.is_file())
                with Image.open(path) as image:
                    image.load()
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.size, expected_size)
                    self.assertEqual(image.mode, "RGBA")
                    width, height = image.size
                    center = image.getpixel((width // 2, height // 2))
                    dot = image.getpixel((round(46 * width / 64), round(45 * height / 64)))
                    self.assertTrue(all(channel >= 240 for channel in center[:3]))
                    self.assertEqual(center[3], 255)
                    self.assertLess(dot[0], 140)
                    self.assertGreater(dot[1], 180)
                    self.assertGreater(dot[1], dot[0] + 50)
                    self.assertGreater(dot[1], dot[2] + 30)
                    self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_original_avatar_has_optimized_responsive_derivatives(self):
        image_dir = Path(settings.BASE_DIR) / "static" / "images"
        self.assertTrue((image_dir / "logo.jpg").is_file())
        for size in (96, 192):
            with self.subTest(size=size), Image.open(image_dir / f"avatar-{size}.webp") as avatar:
                avatar.load()
                self.assertEqual(avatar.format, "WEBP")
                self.assertEqual(avatar.size, (size, size))
