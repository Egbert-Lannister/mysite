"""Public language selection, rendering, and isolation from the admin."""
from datetime import datetime, timezone
import json
import re

from django.conf import settings
from django.test import Client, TestCase
from django.utils import translation

from .models import Post, Series


class PublicLanguageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.series = Series.objects.create(title="Original series", slug="original-series")
        cls.post = Post.objects.create(
            title="Original article", slug="original-article", category="engineering",
            content="Original body.\n\n```python\nprint('hello')\n```",
            description="Original summary", date=datetime(2026, 5, 29, tzinfo=timezone.utc),
            series=cls.series, published=True,
        )
        cls.post.tags.add("example")
        cls.project = Post.objects.create(
            title="Original project", slug="original-project", category="projects",
            content="Original project body", date=cls.post.date, published=True,
        )

    def test_default_and_invalid_cookie_keep_english_public_ui(self):
        for cookie in (None, "unsupported"):
            with self.subTest(cookie=cookie):
                if cookie:
                    self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = cookie
                response = self.client.get("/techblog/", HTTP_ACCEPT_LANGUAGE="zh-CN")
                self.assertContains(response, '<html lang="en">')
                self.assertContains(response, "Latest Article")
                self.assertEqual(response.headers["Content-Language"], "en")

    def test_chinese_pages_preserve_authored_content(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "zh-hans"
        pages = {
            "/techblog/": "最新文章",
            "/techblog/engineering/": "阅读全文",
            "/techblog/research/": "研究",
            "/techblog/notes/": "笔记",
            "/techblog/series/": "2026年5月29日",
            self.series.get_absolute_url(): "Original series",
            "/techblog/search/?q=Original": "搜索",
            "/techblog/tags/example/": "Original article",
            "/techblog/projects/": "Original project",
            "/techblog/projects/original-project/": "概述",
            self.post.get_absolute_url(): "Original body.",
        }
        for url, expected in pages.items():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, '<html lang="zh-hans">')
                self.assertContains(response, expected)
                self.assertEqual(response.headers["Content-Language"], "zh-hans")
                self.assertIn("Cookie", response.headers["Vary"])
        response = self.client.get(self.post.get_absolute_url())
        labels = json.loads(re.search(
            r'<script id="public-ui-labels" type="application/json">(.*?)</script>',
            response.content.decode(), re.S,
        ).group(1))
        self.assertEqual(labels["Copy"], "复制")

    def test_switch_remembers_language_and_preserves_search_and_page(self):
        destination = "/techblog/search/?q=Original&page=1"
        response = self.client.post("/techblog/language/", {
            "language": "zh-hans", "next": destination,
        })
        self.assertRedirects(response, destination)
        cookie = response.cookies[settings.LANGUAGE_COOKIE_NAME]
        self.assertEqual(cookie.value, "zh-hans")
        self.assertEqual(cookie["path"], "/techblog/")
        self.assertGreater(int(cookie["max-age"]), 0)
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertContains(self.client.get("/techblog/series/"), "1 篇文章")
        response = self.client.post("/techblog/language/", {
            "language": "en", "next": "/techblog/series/",
        }, follow=True)
        self.assertContains(response, "May 29, 2026")
        self.assertContains(response, "1 article")
        self.assertNotContains(response, "五月")

    def test_language_endpoint_requires_post_and_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get("/techblog/language/").status_code, 405)
        self.assertEqual(client.post("/techblog/language/", {
            "language": "zh-hans",
        }).status_code, 403)
        client.get("/techblog/")
        response = client.post("/techblog/language/", {
            "language": "zh-hans", "next": "/techblog/",
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "zh-hans")

    def test_unsafe_redirect_and_unknown_language_are_rejected(self):
        response = self.client.post("/techblog/language/", {
            "language": "unsupported", "next": "https://example.com/",
        })
        self.assertEqual(response.headers["Location"], "/")
        self.assertNotIn(settings.LANGUAGE_COOKIE_NAME, response.cookies)

    def test_public_locale_does_not_leak_to_admin_or_other_requests(self):
        with translation.override("zh-hans"):
            self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
            self.assertContains(self.client.get("/techblog/"), '<html lang="en">')
            self.assertEqual(translation.get_language(), "zh-hans")
            self.assertContains(self.client.get("/admin/login/"), 'lang="zh-hans"')
