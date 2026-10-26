"""Isolated tests for posts.html_import; no database or admin workflow is used."""

from __future__ import annotations

import io
import json
import shutil
import stat
import struct
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from PIL import Image

from .html_import import (
    parse_html_project_upload,
    rewrite_html_asset_urls,
    sanitize_project_html,
)


def image_bytes(size=(7, 5), image_format="PNG") -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, "navy").save(stream, image_format)
    return stream.getvalue()


def document(body: str, *, title="Imported project", description="A safe summary") -> bytes:
    return f"""<!doctype html>
<html lang="en"><head><title>{title}</title>
<meta name="description" content="{description}"></head>
<body>{body}</body></html>""".encode("utf-8")


def archive_bytes(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, value in files.items():
            bundle.writestr(name, value)
    return stream.getvalue()


def upload(files: dict[str, bytes]) -> SimpleUploadedFile:
    return SimpleUploadedFile("project.zip", archive_bytes(files), content_type="application/zip")


class HTMLSanitizerTests(SimpleTestCase):
    def test_active_content_external_media_and_event_attributes_are_removed(self):
        source = """<html><head><title>Hidden title</title><style>head bad</style>
<link rel="stylesheet" href="https://evil.invalid/a.css"></head><body>
<script>alert(1)</script><style>body bad</style>
<iframe src="https://evil.invalid">frame fallback</iframe>
<object data="evil.bin">object fallback</object><embed src="evil.bin">
<meta http-equiv="refresh" content="0;url=https://evil.invalid">
<section class="project-html-section attacker" style="color:red" onclick="alert(2)">
<p onmouseover="alert(3)">Safe <strong>content</strong></p>
<img src="assets/ok.png" srcset="https://evil.invalid/a.png 2x" onerror="alert(4)">
<a href="javascript:alert(5)">Bad link</a>
<a href="https://example.com/docs" target="_blank" rel="opener">Good link</a>
</section></body></html>"""
        cleaned = sanitize_project_html(source)
        lowered = cleaned.lower()
        for forbidden in (
            "<script", "<style", "<iframe", "<object", "<embed", "<link", "<meta",
            "onclick", "onmouseover", "onerror", "srcset", "javascript:", "style=",
            "attacker", "alert(1)", "body bad", "hidden title", "frame fallback",
        ):
            self.assertNotIn(forbidden, lowered)
        self.assertIn('class="project-html-section"', cleaned)
        self.assertIn("<strong>content</strong>", cleaned)
        self.assertIn('<img src="assets/ok.png">', cleaned)
        self.assertIn(
            '<a href="https://example.com/docs" target="_blank" rel="noopener noreferrer">Good link</a>',
            cleaned,
        )

    def test_external_and_active_media_urls_are_not_retained(self):
        sources = (
            "https://evil.invalid/a.png", "//evil.invalid/a.png", "/../a.png",
            "data:image/png;base64,AAAA", "blob:https://evil.invalid/id",
            "file:///tmp/a.png", "javascript:alert(1)", "java\nscript:alert(1)",
        )
        for source in sources:
            with self.subTest(source=source):
                cleaned = sanitize_project_html(f'<img alt="pixel" src="{source}">')
                self.assertNotIn("src=", cleaned)
                self.assertEqual(cleaned, '<img alt="pixel">')

    def test_semantic_elements_classes_and_data_attributes(self):
        source = """<main><section class="project-html-section rogue">
<h2>Results</h2><div class="project-html-grid other">
<figure class="project-html-media project-html-wide" data-section="results" data-title="Private metadata">
<img src="assets/result.png" alt="Result"><figcaption class="project-html-caption">Caption</figcaption>
</figure></div><table><thead><tr><th colspan="2">Score</th></tr></thead>
<tbody><tr><td>A</td><td>90</td></tr></tbody></table>
<blockquote>Quote</blockquote><pre><code>safe_code()</code></pre>
</section></main>"""
        cleaned = sanitize_project_html(source)
        for tag in ("main", "section", "h2", "div", "figure", "figcaption", "table", "blockquote", "pre", "code"):
            self.assertIn(f"<{tag}", cleaned)
        self.assertIn('class="project-html-section"', cleaned)
        self.assertIn('class="project-html-grid"', cleaned)
        self.assertIn('class="project-html-media project-html-wide"', cleaned)
        self.assertNotIn("rogue", cleaned)
        self.assertNotIn("other", cleaned)
        self.assertNotIn("data-", cleaned)
        self.assertNotIn("Private metadata", cleaned)

    def test_video_playback_attributes_are_forced_and_sanitizer_is_idempotent(self):
        source = """<video src="assets/demo.mp4" autoplay loop muted controls="false"
playsinline="false" preload="auto" poster="https://evil.invalid/poster.png"
class="project-html-wide arbitrary"></video>"""
        cleaned = sanitize_project_html(source)
        self.assertTrue(cleaned.startswith("<video "))
        self.assertTrue(cleaned.endswith(" controls playsinline preload=\"metadata\"></video>"))
        self.assertIn('src="assets/demo.mp4"', cleaned)
        self.assertIn('class="project-html-wide"', cleaned)
        self.assertEqual(cleaned.count("<video"), 1)
        for forbidden in ("autoplay", "loop", "muted", "poster", "arbitrary", "preload=\"auto\""):
            self.assertNotIn(forbidden, cleaned)
        self.assertEqual(sanitize_project_html(cleaned), cleaned)

    def test_rewrite_maps_package_urls_and_preserves_unmatched_content(self):
        source = """<section class="project-html-section"><p>Before</p>
<img src="./assets/a%20one.png" alt="A">
<video><source src="assets/demo.mp4" type="video/mp4"></video>
<img src="assets/keep.png" alt="Keep"><p>After</p></section>"""
        rewritten = rewrite_html_asset_urls(source, {
            "assets/a one.png": "/media/projects/demo/a%20one.png",
            "./assets/demo.mp4": "/media/projects/demo/demo.mp4",
        })
        self.assertIn('src="/media/projects/demo/a%20one.png"', rewritten)
        self.assertIn('src="/media/projects/demo/demo.mp4"', rewritten)
        self.assertIn('src="assets/keep.png"', rewritten)
        self.assertIn("<p>Before</p>", rewritten)
        self.assertIn("<p>After</p>", rewritten)
        self.assertIn("controls playsinline preload=\"metadata\"", rewritten)
        with self.assertRaisesMessage(ValueError, "本地"):
            rewrite_html_asset_urls('<img src="assets/a.png">', {"assets/a.png": "data:image/png;base64,AAAA"})


class HTMLProjectUploadTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory(prefix="html_import_test_")
        self.addCleanup(self.temporary.cleanup)
        real_mkdtemp = tempfile.mkdtemp

        def isolated_mkdtemp(prefix):
            return real_mkdtemp(prefix=prefix, dir=self.temporary.name)

        patcher = patch("posts.html_import.tempfile.mkdtemp", side_effect=isolated_mkdtemp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_staging_cleaned(self):
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])

    def parse(self, uploaded):
        result = parse_html_project_upload(uploaded)
        self.addCleanup(shutil.rmtree, result["staging_dir"], ignore_errors=True)
        return result

    def test_valid_zip_in_one_top_level_directory_returns_serializable_metadata(self):
        png = image_bytes((11, 6))
        video = b"\x00\x00\x00\x18ftypmp42example-video"
        body = """<main><section class="project-html-section"><h1>Ignored fallback</h1>
<figure data-section="method" data-group="Pipeline" data-title="Method diagram"
 data-description="Encoder and policy"><img src="assets/diagram%20one.png" alt="Diagram">
<figcaption>Fallback caption</figcaption></figure>
<figure data-section="results" data-group="Real robot" data-title="Rollout">
<video autoplay muted><source src="assets/demo.mp4" type="video/mp4"></video>
</figure></section></main>"""
        result = self.parse(upload({
            "bundle/index.html": document(body, title="Spatial memory", description="Project description"),
            "bundle/assets/diagram one.png": png,
            "bundle/assets/demo.mp4": video,
        }))

        self.assertEqual(result["title"], "Spatial memory")
        self.assertEqual(result["description"], "Project description")
        self.assertEqual(result["mode"], "project_html")
        self.assertEqual(result["warnings"], [])
        self.assertTrue(Path(result["staging_dir"]).is_dir())
        self.assertIn('src="assets/diagram%20one.png"', result["content"])
        self.assertNotIn("data-section", result["content"])
        self.assertNotIn("autoplay", result["content"])
        self.assertNotIn("muted", result["content"])
        self.assertIn("controls playsinline preload=\"metadata\"", result["content"])
        json.dumps(result)

        self.assertEqual(len(result["assets"]), 2)
        self.assertEqual(len(result["project_media"]), 2)
        image, movie = result["project_media"]
        self.assertEqual(image["src"], "assets/diagram one.png")
        self.assertEqual(image["filename"], "diagram one.png")
        self.assertEqual(image["kind"], "image")
        self.assertEqual(image["section"], "method")
        self.assertEqual(image["group_name"], "Pipeline")
        self.assertEqual(image["title"], "Method diagram")
        self.assertEqual(image["description"], "Encoder and policy")
        self.assertEqual(image["width"], 11)
        self.assertEqual(image["height"], 6)
        self.assertEqual(image["file_size"], len(png))
        self.assertEqual(image["order"], 1)
        self.assertTrue(image["include"])
        self.assertTrue((Path(result["staging_dir"]) / image["path"]).is_file())
        self.assertEqual(movie["kind"], "video")
        self.assertEqual(movie["section"], "results")
        self.assertEqual(movie["file_size"], len(video))
        self.assertNotIn("width", movie)
        self.assertEqual(result["assets"][0]["path"], image["path"])
        self.assertNotEqual(result["assets"][0]["id"], movie["id"])

    def test_unreferenced_media_warns_and_is_not_staged_for_import(self):
        result = self.parse(upload({
            "index.html": document('<p>Text</p><img src="used.png" alt="Used">'),
            "used.png": image_bytes(),
            "unused.png": image_bytes((2, 2)),
        }))
        self.assertEqual([item["filename"] for item in result["project_media"]], ["used.png"])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("unused.png", result["warnings"][0])
        self.assertFalse((Path(result["staging_dir"]) / "content" / "unused.png").exists())

    def test_missing_and_external_media_references_are_rejected_and_cleaned_up(self):
        sources = (
            "missing.png", "https://evil.invalid/a.png", "//evil.invalid/a.png",
            "/absolute/a.png", "../escape.png", "data:image/png;base64,AAAA",
            "blob:https://evil.invalid/id", "file:///tmp/a.png", "javascript:alert(1)",
        )
        for source in sources:
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    parse_html_project_upload(upload({
                        "index.html": document(f'<img src="{source}" alt="Bad">'),
                        "local.png": image_bytes(),
                    }))
                self.assert_staging_cleaned()

    def test_source_must_be_local_video_and_inline_sources_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_html_project_upload(upload({
                "index.html": document('<video><source src="data:video/mp4;base64,AAAA"></video>'),
            }))
        self.assert_staging_cleaned()
        with self.assertRaisesMessage(ValueError, "只能用于"):
            parse_html_project_upload(upload({
                "index.html": document('<source src="demo.mp4">'), "demo.mp4": b"video",
            }))
        self.assert_staging_cleaned()

    def test_multiple_html_unsupported_files_and_invalid_layout_are_rejected(self):
        valid_image = image_bytes()
        cases = {
            "multiple_html": {
                "index.html": document("<p>One</p>"), "other.html": b"<p>Two</p>",
            },
            "unsupported": {
                "index.html": document("<p>One</p>"), "style.css": b"body {}",
            },
            "wrong_name": {"other.html": b"<p>One</p>"},
            "too_deep": {"one/two/index.html": document("<p>One</p>")},
            "outside_shared_root": {
                "root/index.html": document("<p>One</p>"), "outside.png": valid_image,
            },
        }
        for name, files in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    parse_html_project_upload(upload(files))
                self.assert_staging_cleaned()

    def test_zip_slip_absolute_paths_and_backslash_traversal_are_rejected(self):
        for bad_name in ("../escape.png", "/absolute.png", "C:/escape.png", "..\\escape.png"):
            with self.subTest(name=bad_name):
                with self.assertRaises(ValueError):
                    parse_html_project_upload(upload({
                        "index.html": document("<p>Safe</p>"), bad_name: image_bytes(),
                    }))
                self.assert_staging_cleaned()

    def test_symlink_is_rejected(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as bundle:
            bundle.writestr("index.html", document("<p>Safe</p>"))
            link = zipfile.ZipInfo("assets/link.png")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(link, "target.png")
        with self.assertRaisesMessage(ValueError, "符号链接"):
            parse_html_project_upload(SimpleUploadedFile("project.zip", stream.getvalue()))
        self.assert_staging_cleaned()

    def test_encrypted_entry_is_rejected(self):
        raw = bytearray(archive_bytes({
            "index.html": document("<p>Safe</p>"), "image.png": image_bytes(),
        }))
        signature = b"PK\x01\x02"
        offset = raw.find(signature)
        self.assertGreaterEqual(offset, 0)
        flags = struct.unpack_from("<H", raw, offset + 8)[0]
        struct.pack_into("<H", raw, offset + 8, flags | 1)
        with self.assertRaisesMessage(ValueError, "加密"):
            parse_html_project_upload(SimpleUploadedFile("project.zip", bytes(raw)))
        self.assert_staging_cleaned()

    @override_settings(UPLOAD_MAX_ZIP_SIZE=16)
    def test_compressed_zip_budget_is_enforced(self):
        with self.assertRaisesMessage(ValueError, "ZIP 文件过大"):
            parse_html_project_upload(upload({"index.html": document("<p>Large archive</p>")}))
        self.assert_staging_cleaned()

    @override_settings(UPLOAD_MAX_ZIP_ENTRIES=2)
    def test_zip_entry_budget_is_enforced(self):
        with self.assertRaises(ValueError):
            parse_html_project_upload(upload({
                "index.html": document("<p>Safe</p>"), "a.png": b"a", "b.png": b"b",
            }))
        self.assert_staging_cleaned()

    @override_settings(UPLOAD_MAX_ZIP_UNCOMPRESSED_SIZE=16)
    def test_uncompressed_zip_budget_is_enforced(self):
        with self.assertRaises(ValueError):
            parse_html_project_upload(upload({"index.html": document("<p>This is over budget</p>")}))
        self.assert_staging_cleaned()

    @override_settings(UPLOAD_MAX_FILE_SIZE=1024)
    def test_single_image_size_limit_is_enforced(self):
        with self.assertRaisesMessage(ValueError, "单文件"):
            parse_html_project_upload(upload({
                "index.html": document('<img src="image.png" alt="Image">'),
                "image.png": image_bytes((512, 512)),
            }))
        self.assert_staging_cleaned()

    @override_settings(UPLOAD_MAX_VIDEO_SIZE=8)
    def test_single_video_size_limit_is_enforced(self):
        with self.assertRaisesMessage(ValueError, "单文件"):
            parse_html_project_upload(upload({
                "index.html": document('<video src="demo.mp4"></video>'), "demo.mp4": b"more than eight bytes",
            }))
        self.assert_staging_cleaned()

    @override_settings(UPLOAD_MAX_PROJECT_MEDIA=1)
    def test_media_count_limit_is_enforced(self):
        with self.assertRaisesMessage(ValueError, "媒体文件过多"):
            parse_html_project_upload(upload({
                "index.html": document('<img src="a.png"><img src="b.png">'),
                "a.png": image_bytes(), "b.png": image_bytes(),
            }))
        self.assert_staging_cleaned()

    def test_invalid_image_is_verified_and_failed_staging_is_removed(self):
        with self.assertRaisesMessage(ValueError, "图片校验失败"):
            parse_html_project_upload(upload({
                "index.html": document('<img src="fake.png" alt="Fake">'), "fake.png": b"not an image",
            }))
        self.assert_staging_cleaned()

    def test_image_extension_must_match_verified_format(self):
        with self.assertRaisesMessage(ValueError, "扩展名与内容不符"):
            parse_html_project_upload(upload({
                "index.html": document('<img src="fake.png" alt="Fake">'),
                "fake.png": image_bytes(image_format="GIF"),
            }))
        self.assert_staging_cleaned()
