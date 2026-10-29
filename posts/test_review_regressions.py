"""Regression cases found during the whole-project review."""
import io
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone as utc_timezone
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone, translation
from PIL import Image

from .admin_uploads import ARTICLE_SESSION_KEY, PROJECT_SESSION_KEY, _stage_cover, _asset_path
from .models import Post, Series
from scripts.backup_db import backup_database


def picture(color):
    stream = io.BytesIO()
    Image.new('RGB', (2, 2), color).save(stream, format='PNG')
    return stream.getvalue()


def package(title, color, html=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        if html:
            archive.writestr('index.html', f'<html><head><title>{title}</title></head><body><h1>{title}</h1><img src="figure.png"></body></html>')
        else:
            archive.writestr('article.md', f'---\ntitle: {title}\ncategory: notes\n---\n![figure](figure.png)')
        archive.writestr('figure.png', picture(color))
    return SimpleUploadedFile('content.zip', stream.getvalue(), content_type='application/zip')


class DraftIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser('review-admin', 'admin@example.invalid', 'password')

    def setUp(self):
        self.client.force_login(self.user)
        self.temp = tempfile.TemporaryDirectory(prefix='review_media_')
        self.override = override_settings(MEDIA_ROOT=Path(self.temp.name))
        self.override.enable()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.override.disable)
        self.staging = []
        self.addCleanup(self.cleanup_staging)

    def cleanup_staging(self):
        for path in self.staging:
            shutil.rmtree(path, ignore_errors=True)

    def upload(self, title, color, *, html=False):
        key = PROJECT_SESSION_KEY if html else ARTICLE_SESSION_KEY
        url = '/admin/project-upload/' if html else '/admin/upload/'
        response = self.client.post(url, {'upload_file': package(title, color, html), 'import_mode': 'html' if html else 'markdown'})
        self.assertEqual(response.status_code, 302)
        data = self.client.session[key]
        self.staging.append(data['staging_dir'])
        self.assertIn(data['editor_token'], response.url)
        page = self.client.get(response.url)
        self.assertContains(page, f'name="editor_token" value="{data["editor_token"]}"')
        return data

    def payload(self, draft, slug):
        return {'editor_token': draft['editor_token'], 'title': draft['title'], 'slug': slug,
                'category': 'notes', 'date': '2026-05-29 12:00:00', 'content': '![figure](figure.png)',
                'action': 'publish', 'project_status': 'active'}

    def test_two_article_tabs_save_their_own_images_and_keep_other_draft(self):
        first = self.upload('First', 'red')
        second = self.upload('Second', 'blue')
        self.assertTrue(Path(first['staging_dir']).exists())
        response = self.client.post('/admin/upload/preview/', self.payload(first, 'first'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual((Path(self.temp.name) / 'posts/first/figure.png').read_bytes(), picture('red'))
        self.assertEqual(self.client.session[ARTICLE_SESSION_KEY]['editor_token'], second['editor_token'])
        self.assertTrue(Path(second['staging_dir']).exists())
        self.assertEqual(self.client.post('/admin/upload/preview/', self.payload(first, 'replay')).status_code, 409)
        self.assertEqual(self.client.post('/admin/upload/preview/', self.payload(second, 'second')).status_code, 302)
        self.assertEqual((Path(self.temp.name) / 'posts/second/figure.png').read_bytes(), picture('blue'))
        self.assertNotIn(ARTICLE_SESSION_KEY, self.client.session)

    def test_missing_unknown_or_mismatched_tokens_cannot_mutate_current_draft(self):
        draft = self.upload('Current', 'red')
        before = dict(self.client.session[ARTICLE_SESSION_KEY])
        for token in ('', 'not-a-draft'):
            response = self.client.post('/admin/upload/preview/', {**self.payload(draft, 'blocked'), 'editor_token': token})
            self.assertEqual(response.status_code, 409)
        response = self.client.post('/admin/upload/preview/?draft=wrong', self.payload(draft, 'blocked'))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.session[ARTICLE_SESSION_KEY], before)
        self.assertFalse(Post.objects.exists())
        self.assertTrue(Path(draft['staging_dir']).exists())

    def test_two_html_project_tabs_keep_original_body_and_media(self):
        first = self.upload('First HTML', 'red', html=True)
        second = self.upload('Second HTML', 'blue', html=True)
        response = self.client.post('/admin/project-upload/preview/', self.payload(first, 'first-html'))
        self.assertEqual(response.status_code, 302)
        post = Post.objects.get(slug='first-html')
        self.assertIn('First HTML', post.content)
        self.assertNotIn('Second HTML', post.content)
        media = post.project_media.get()
        self.assertEqual(Path(media.file.path).read_bytes(), picture('red'))
        self.assertEqual(self.client.session[PROJECT_SESSION_KEY]['editor_token'], second['editor_token'])
        self.assertTrue(Path(second['staging_dir']).exists())

    def test_two_blank_projects_can_be_saved_independently(self):
        drafts = []
        for title in ('One', 'Two'):
            response = self.client.get('/admin/project-upload/blank/')
            drafts.append(dict(self.client.session[PROJECT_SESSION_KEY], title=title))
            self.assertIn(drafts[-1]['editor_token'], response.url)
        for index, draft in enumerate(drafts):
            response = self.client.post('/admin/project-upload/preview/', {
                **self.payload(draft, f'blank-{index}'), 'content': draft['title'],
            })
            self.assertEqual(response.status_code, 302)
        self.assertEqual(list(Post.objects.order_by('pk').values_list('title', flat=True)), ['One', 'Two'])


class SharedCoverTests(SimpleTestCase):
    def test_replacing_cover_keeps_shared_media_and_inline_assets(self):
        for collection in ('project_media', 'assets'):
            with self.subTest(collection=collection), tempfile.TemporaryDirectory(prefix='project_') as root:
                source = Path(root) / 'shared.png'
                source.write_bytes(picture('red'))
                data = {'staging_dir': root, 'project_cover': {'path': 'shared.png'}, collection: [{'path': 'shared.png', 'include': True}]}
                _stage_cover(data, SimpleUploadedFile('new.png', picture('blue')))
                self.assertEqual(_asset_path(data, 'shared.png').read_bytes(), picture('red'))
                self.assertEqual(_asset_path(data, data['project_cover']['path']).read_bytes(), picture('blue'))

    def test_replacing_exclusive_cover_cleans_only_old_cover(self):
        with tempfile.TemporaryDirectory(prefix='project_') as root:
            source = Path(root) / 'old.png'
            source.write_bytes(picture('red'))
            data = {'staging_dir': root, 'project_cover': {'path': 'old.png'}}
            _stage_cover(data, SimpleUploadedFile('new.png', picture('blue')))
            self.assertFalse(source.exists())
            self.assertTrue(_asset_path(data, data['project_cover']['path']).exists())


class ReadingOrderAndDateTests(TestCase):
    def test_all_series_surfaces_use_explicit_order_then_date_and_pk(self):
        series = Series.objects.create(title='Reading', slug='reading')
        when = timezone.now()
        posts = [Post.objects.create(title=f'Chapter {i}', slug=f'chapter-{i}', content='Body',
                  date=when, category='notes', series=series, series_order=order)
                 for i, order in enumerate((2, 1, None, None))]
        Post.objects.create(title='Draft', slug='draft', content='Body', date=when, category='notes', series=series, published=False)
        Post.objects.create(title='Project', slug='project', content='Body', date=when, category='projects', series=series)
        expected = [posts[1], posts[0], posts[2], posts[3]]
        self.assertEqual(list(series.get_posts()), expected)
        for i, post in enumerate(expected):
            self.assertEqual(post.get_series_prev(), expected[i-1] if i else None)
            self.assertEqual(post.get_series_next(), expected[i+1] if i+1 < len(expected) else None)
            page = self.client.get(post.get_absolute_url())
            context = page.context['series_context']
            self.assertEqual(context['posts'], expected)
            self.assertEqual(context['current_index'], i+1)
            self.assertEqual(context['prev_post'], post.get_series_prev())
            self.assertEqual(context['next_post'], post.get_series_next())
        self.assertEqual(list(self.client.get(series.get_absolute_url()).context['posts']), expected)

    def test_public_dates_match_local_calendar_in_both_languages(self):
        value = datetime(2026, 5, 28, 16, tzinfo=utc_timezone.utc)
        for language, label in [('zh-hans', '2026年5月29日'), ('en', 'May 29, 2026')]:
            with self.subTest(language=language), timezone.override('Asia/Shanghai'), translation.override(language):
                result = Template('{% load public_i18n %}{{ d|date:"Y-m-d" }} / {{ d|display_date }}').render(Context({'d': value}))
                self.assertEqual(result, f'2026-05-29 / {label}')
        with timezone.override('America/New_York'), translation.override('en'):
            result = Template('{% load public_i18n %}{{ d|display_date }}').render(Context({'d': value}))
            self.assertEqual(result, 'May 28, 2026')


class ImportIdentityTests(TestCase):
    def test_different_import_roots_do_not_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ('one', 'two'):
                folder = Path(root) / name
                folder.mkdir()
                (folder / 'article.md').write_text(f'---\ntitle: {name}\n---\n{name}', encoding='utf-8')
                call_command('loadmd', str(folder), stdout=io.StringIO())
            self.assertEqual(set(Post.objects.values_list('title', flat=True)), {'one', 'two'})

    def test_explicit_namespace_keeps_identity_when_import_root_moves(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ('old-location', 'new-location'):
                folder = Path(root) / name
                folder.mkdir()
                (folder / 'article.md').write_text(f'---\ntitle: {name}\n---\nUpdated', encoding='utf-8')
                call_command('loadmd', str(folder), namespace='personal-archive', stdout=io.StringIO())
            self.assertEqual(Post.objects.count(), 1)
            self.assertEqual(Post.objects.get().title, 'new-location')

    def test_no_slug_reimport_updates_same_file_even_when_title_changes(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'article.md'
            for title, body in [('Original', 'Before'), ('Renamed', 'After')]:
                source.write_text(f'---\ntitle: {title}\ndate: 2026-05-29\n---\n{body}', encoding='utf-8')
                call_command('loadmd', root, stdout=io.StringIO())
            self.assertEqual(Post.objects.count(), 1)
            self.assertEqual(Post.objects.get().content, 'After')
            self.assertEqual(Post.objects.get().title, 'Renamed')

    def test_same_filename_in_different_subdirectories_remains_distinct(self):
        with tempfile.TemporaryDirectory() as root:
            for folder in ('a', 'b'):
                source = Path(root) / folder / 'article.md'
                source.parent.mkdir()
                source.write_text('---\ntitle: Shared title\n---\nBody', encoding='utf-8')
            for _ in range(2):
                call_command('loadmd', root, stdout=io.StringIO())
            self.assertEqual(Post.objects.count(), 2)


class OnlineBackupTests(SimpleTestCase):
    def test_backup_includes_wal_and_only_prunes_verified_old_backups(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'live.sqlite3'
            output = Path(root) / 'backups'
            with closing(sqlite3.connect(source)) as connection:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('CREATE TABLE data(value TEXT)'); connection.commit()
                connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                connection.execute("INSERT INTO data VALUES ('committed')"); connection.commit()
                first = backup_database(source, output, keep=1)
                with closing(sqlite3.connect(first)) as backup:
                    self.assertEqual(backup.execute('SELECT * FROM data').fetchall(), [('committed',)])
                unrelated = output / 'db.sqlite3.keep-me'
                unrelated.write_text('Keep')
                second = backup_database(source, output, keep=1)
                self.assertFalse(first.exists())
                self.assertTrue(second.exists())
                self.assertTrue(unrelated.exists())

    def test_failure_does_not_create_empty_db_or_remove_old_backups(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            output = root / 'backups'; output.mkdir()
            old = output / 'db.sqlite3.20200101_010101'; old.write_bytes(b'previous backup')
            missing = root / 'missing.sqlite3'
            with self.assertRaises(FileNotFoundError):
                backup_database(missing, output)
            self.assertFalse(missing.exists())
            corrupt = root / 'corrupt.sqlite3'; corrupt.write_bytes(b'not sqlite')
            with self.assertRaises(sqlite3.DatabaseError):
                backup_database(corrupt, output)
            self.assertEqual(list(output.iterdir()), [old])
