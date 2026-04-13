"""
Tests for the posts app — Series model, ZIP upload service, and markdown processing.
"""

from __future__ import annotations

import io
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Post, Series, compute_content_hash, generate_series_slug, generate_unique_slug
from .services import (
    extract_title_from_markdown,
    extract_zip_safely,
    is_safe_path,
    parse_upload_file,
    process_markdown_content,
    rewrite_image_paths,
)

TEMP_MEDIA = tempfile.mkdtemp(prefix="test_media_")


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class SeriesModelTests(TestCase):
    def setUp(self):
        self.series = Series.objects.create(
            title="Test Series",
            slug="test-series",
            description="A test series",
        )

    def test_str(self):
        self.assertEqual(str(self.series), "Test Series")

    def test_auto_slug(self):
        s = Series(title="Auto Slug Series")
        s.save()
        self.assertTrue(s.slug)
        self.assertIn("auto-slug", s.slug)

    def test_get_posts_empty(self):
        self.assertEqual(list(self.series.get_posts()), [])

    def test_get_posts_returns_published_ordered(self):
        now = timezone.now()
        p1 = Post.objects.create(
            title="First", slug="first", content="c1", date=now,
            category="engineering", series=self.series, series_order=2,
        )
        p2 = Post.objects.create(
            title="Second", slug="second", content="c2", date=now,
            category="engineering", series=self.series, series_order=1,
        )
        Post.objects.create(
            title="Draft", slug="draft", content="c3", date=now,
            category="engineering", series=self.series, series_order=3,
            published=False,
        )

        posts = list(self.series.get_posts())
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0].pk, p2.pk)
        self.assertEqual(posts[1].pk, p1.pk)

    def test_post_count(self):
        now = timezone.now()
        Post.objects.create(
            title="P1", slug="p1", content="c", date=now,
            category="notes", series=self.series, series_order=1,
        )
        self.assertEqual(self.series.post_count, 1)

    def test_latest_post_date(self):
        self.assertIsNone(self.series.latest_post_date)
        now = timezone.now()
        Post.objects.create(
            title="P", slug="p", content="c", date=now,
            category="notes", series=self.series,
        )
        self.assertEqual(self.series.latest_post_date, now)

    def test_get_absolute_url(self):
        self.assertEqual(self.series.get_absolute_url(), "/techblog/series/test-series/")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)


class PostModelTests(TestCase):
    def test_auto_slug(self):
        now = timezone.now()
        p = Post(title="Hello World", content="body", date=now, category="engineering")
        p.save()
        self.assertEqual(p.slug, "hello-world")

    def test_content_hash_on_save(self):
        now = timezone.now()
        p = Post.objects.create(
            title="H", slug="h", content="abc", date=now, category="engineering",
        )
        self.assertEqual(p.content_hash, compute_content_hash("abc"))

    def test_series_prev_next(self):
        now = timezone.now()
        s = Series.objects.create(title="S", slug="s")
        p1 = Post.objects.create(
            title="A", slug="a", content="a", date=now,
            category="engineering", series=s, series_order=1,
        )
        p2 = Post.objects.create(
            title="B", slug="b", content="b", date=now,
            category="engineering", series=s, series_order=2,
        )
        self.assertIsNone(p1.get_series_prev())
        self.assertEqual(p1.get_series_next(), p2)
        self.assertEqual(p2.get_series_prev(), p1)
        self.assertIsNone(p2.get_series_next())


class HelperFunctionTests(TestCase):
    def test_compute_content_hash(self):
        h = compute_content_hash("test")
        self.assertEqual(len(h), 64)

    def test_generate_unique_slug(self):
        now = timezone.now()
        Post.objects.create(
            title="X", slug="hello", content="c", date=now, category="engineering",
        )
        slug = generate_unique_slug("Hello")
        self.assertEqual(slug, "hello-1")

    def test_generate_series_slug(self):
        Series.objects.create(title="S", slug="test")
        slug = generate_series_slug("Test")
        self.assertEqual(slug, "test-1")

    def test_extract_title_from_markdown(self):
        self.assertEqual(extract_title_from_markdown("# My Title\nBody"), "My Title")
        self.assertEqual(extract_title_from_markdown("No heading"), "")

    def test_is_safe_path(self):
        base = Path("/tmp/test")
        self.assertTrue(is_safe_path(base, base / "file.txt"))
        self.assertFalse(is_safe_path(base, Path("/tmp/test/../../../etc/passwd")))


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class ZipUploadServiceTests(TestCase):
    def _make_zip(self, files: dict[str, bytes]) -> io.BytesIO:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        buf.seek(0)
        return buf

    def test_extract_zip_safely_basic(self):
        zip_buf = self._make_zip({
            "article.md": b"# Hello\nWorld",
            "img.png": b"PNG_DATA",
        })
        with tempfile.TemporaryDirectory() as tmp:
            md, images, warnings = extract_zip_safely(zip_buf, Path(tmp))
            self.assertIsNotNone(md)
            self.assertEqual(len(images), 1)
            self.assertEqual(len(warnings), 0)

    def test_extract_zip_no_md(self):
        zip_buf = self._make_zip({"img.png": b"PNG_DATA"})
        with tempfile.TemporaryDirectory() as tmp:
            md, images, warnings = extract_zip_safely(zip_buf, Path(tmp))
            self.assertIsNone(md)

    def test_extract_zip_skips_unknown_types(self):
        zip_buf = self._make_zip({
            "article.md": b"# Test",
            "data.csv": b"a,b,c",
        })
        with tempfile.TemporaryDirectory() as tmp:
            md, images, warnings = extract_zip_safely(zip_buf, Path(tmp))
            self.assertIsNotNone(md)
            self.assertEqual(len(images), 0)
            self.assertTrue(any("不支持" in w for w in warnings))

    def test_rewrite_image_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            img = tmp_path / "photo.png"
            img.write_bytes(b"PNG")
            content = "![alt](photo.png)"
            new_content, missing = rewrite_image_paths(
                content, "test-slug", tmp_path, [img],
            )
            self.assertIn("/media/posts/test-slug/", new_content)
            self.assertEqual(len(missing), 0)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class ProcessMarkdownTests(TestCase):
    def test_basic_frontmatter(self):
        md = "---\ntitle: Test Post\ncategory: engineering\ntags:\n  - python\n---\nBody here."
        post, created, warnings = process_markdown_content(md, [], None)
        self.assertTrue(created)
        self.assertEqual(post.title, "Test Post")
        self.assertEqual(post.category, "engineering")
        self.assertEqual(post.content, "Body here.")

    def test_no_frontmatter_fallback(self):
        md = "# Fallback Title\n\nSome content."
        post, created, warnings = process_markdown_content(
            md, [], None, overrides={"category": "notes"},
        )
        self.assertTrue(created)
        self.assertEqual(post.title, "Fallback Title")
        self.assertTrue(any("front matter" in w for w in warnings))

    def test_series_auto_creation(self):
        md = "---\ntitle: Series Post\ncategory: engineering\nseries: My Series\nseries_order: 1\n---\nBody."
        post, created, warnings = process_markdown_content(md, [], None)
        self.assertIsNotNone(post.series)
        self.assertEqual(post.series.title, "My Series")
        self.assertEqual(post.series_order, 1)

    def test_duplicate_detection(self):
        md = "---\ntitle: Original\ncategory: engineering\n---\nSame body."
        process_markdown_content(md, [], None, overrides={"slug": "original"})
        md2 = "---\ntitle: Copy\ncategory: engineering\n---\nSame body."
        _, _, warnings = process_markdown_content(md2, [], None, overrides={"slug": "copy"})
        self.assertTrue(any("内容相同" in w for w in warnings))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
