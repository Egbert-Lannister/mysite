"""
Django settings for mysite project.
"""
from pathlib import Path
import os
import dj_database_url
from django.templatetags.static import static

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ['SECRET_KEY']  # No fallback — crash loud if missing

DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'

ALLOWED_HOSTS = [
    'egbertlannister.com',
    'www.egbertlannister.com',
    'egbert-lannister.com',
    'www.egbert-lannister.com',
    'localhost',
    '127.0.0.1',
]

CSRF_TRUSTED_ORIGINS = [
    'https://egbertlannister.com',
    'https://www.egbertlannister.com',
    'https://egbert-lannister.com',
    'https://www.egbert-lannister.com',
]

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True

INSTALLED_APPS = [
    'unfold',
    'unfold.contrib.filters',
    'unfold.contrib.forms',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'django.contrib.syndication',
    'taggit',
    'posts',
]

if DEBUG:
    INSTALLED_APPS.append('django_browser_reload')

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'posts.public_language.PublicLanguageMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

if DEBUG:
    MIDDLEWARE.append('django_browser_reload.middleware.BrowserReloadMiddleware')

ROOT_URLCONF = 'mysite.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.template.context_processors.i18n',
                'posts.public_language.public_language',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'mysite.wsgi.application'

DATABASES = {
    'default': dj_database_url.parse(
        os.environ.get('DATABASE_URL', f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=600,
        ssl_require=False,
    )
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    DATABASES["default"].setdefault("OPTIONS", {})["timeout"] = 20

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'zh-hans'
LANGUAGES = [('en', 'English'), ('zh-hans', '简体中文')]
LOCALE_PATHS = [BASE_DIR / 'locale']
LANGUAGE_COOKIE_PATH = '/techblog/'
LANGUAGE_COOKIE_AGE = 60 * 60 * 24 * 365
LANGUAGE_COOKIE_SAMESITE = 'Lax'
LANGUAGE_COOKIE_SECURE = not DEBUG
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files (for uploaded images)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage',
    },
}

SITE_ID = 1
INTERNAL_IPS = ['127.0.0.1', 'localhost']
LOGIN_REDIRECT_URL = '/admin/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# django-taggit: allow spaces inside a single tag; use commas to separate tags
TAGGIT_TAGS_FROM_STRING = 'posts.taggit_helpers.parse_tags_allow_spaces'

# =============================================================================
# Upload Settings
# =============================================================================
UPLOAD_MAX_FILE_SIZE = 10 * 1024 * 1024  # Markdown / image, 10MB per file
UPLOAD_MAX_VIDEO_SIZE = 50 * 1024 * 1024  # Video, 50MB per file
UPLOAD_MAX_ZIP_SIZE = 50 * 1024 * 1024   # Compressed upload, 50MB
UPLOAD_MAX_ZIP_UNCOMPRESSED_SIZE = 150 * 1024 * 1024
UPLOAD_MAX_ZIP_ENTRIES = 200
UPLOAD_MAX_NESTED_ARCHIVES = 8
UPLOAD_ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
UPLOAD_ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'webm'}
MARKDOWN_PREVIEW_MAX_CHARS = 100_000

# =============================================================================
# Giscus Comments Configuration
# =============================================================================
GISCUS_REPO = os.environ.get('GISCUS_REPO', '')
GISCUS_REPO_ID = os.environ.get('GISCUS_REPO_ID', '')
GISCUS_CATEGORY = os.environ.get('GISCUS_CATEGORY', '')
GISCUS_CATEGORY_ID = os.environ.get('GISCUS_CATEGORY_ID', '')
GISCUS_MAPPING = 'pathname'
GISCUS_REACTIONS_ENABLED = '1'
GISCUS_EMIT_METADATA = '0'
GISCUS_INPUT_POSITION = 'bottom'
GISCUS_LANG = 'zh-CN'

# External AI calls are optional admin helpers and must never hold a worker for long.
AI_REQUEST_TIMEOUT = float(os.environ.get("AI_REQUEST_TIMEOUT", "8"))

# =============================================================================
# Django Unfold Admin Configuration
# =============================================================================
UNFOLD = {
    "SITE_TITLE": "Egbert's Blog 管理后台",
    "SITE_HEADER": "Egbert's Blog",
    "SITE_SUBHEADER": "内容管理系统",
    "SITE_DROPDOWN": [
        {"icon": "public", "title": "访问网站", "link": "/techblog/"},
    ],
    "SITE_LOGO": None,
    "SITE_FAVICONS": [
        {"rel": "icon", "type": "image/svg+xml", "href": lambda request: static("images/favicon.svg") + "?v=20261029-folio"},
        {"rel": "icon", "type": "image/png", "sizes": "32x32", "href": lambda request: static("images/favicon-32.png") + "?v=20261029-folio"},
        {"rel": "apple-touch-icon", "type": "image/png", "sizes": "180x180", "href": lambda request: static("images/apple-touch-icon.png") + "?v=20261029-folio"},
    ],
    "SITE_SYMBOL": "article",
    "STYLES": [
        # Refresh the local icon subset even when Unfold's default URL is cached.
        lambda request: static("unfold/fonts/material-symbols/styles.css") + "?v=20261029-icons",
        lambda request: static("css/chinese-font.css"),
        lambda request: static("admin/theme.css") + "?v=20261029-workbench",
    ],
    "DASHBOARD_CALLBACK": "mysite.admin_config.dashboard_callback",
    "BORDER_RADIUS": "10px",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "ENVIRONMENT": "mysite.admin_config.environment_callback",
    "COLORS": {
        # Quiet neutral surfaces with a teal accent shared by the content workbench.
        "base": {
            "50": "246 248 247", "100": "239 243 241", "200": "223 231 227",
            "300": "192 206 198", "400": "137 157 147", "500": "100 119 109",
            "600": "76 94 85", "700": "53 69 61", "800": "36 48 42",
            "900": "24 34 29", "950": "16 24 20",
        },
        "primary": {
            "50": "240 253 250", "100": "204 251 241", "200": "153 246 228",
            "300": "94 234 212", "400": "45 212 191", "500": "20 184 166",
            "600": "15 118 110", "700": "17 94 89", "800": "19 78 74",
            "900": "19 59 56", "950": "8 40 38",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "内容管理",
                "separator": True,
                "collapsible": False,
                "items": [
                    {"title": "文章管理", "icon": "article", "link": "/admin/posts/post/",
                     "badge": "mysite.admin_config.post_count_callback"},
                    {"title": "项目管理", "icon": "collections_bookmark", "link": "/admin/posts/project/"},
                    {"title": "系列管理", "icon": "collections_bookmark", "link": "/admin/posts/series/"},
                    {"title": "标签管理", "icon": "label", "link": "/admin/taggit/tag/"},
                ],
            },
            {
                "title": "快捷操作",
                "separator": True,
                "collapsible": False,
                "items": [
                    {"title": "上传文章", "icon": "upload_file", "link": "/admin/upload/"},
                    {"title": "新建文章", "icon": "edit_note", "link": "/admin/upload/blank/"},
                    {"title": "上传项目", "icon": "upload_file", "link": "/admin/project-upload/"},
                    {"title": "新建项目", "icon": "edit_note", "link": "/admin/project-upload/blank/"},
                    {"title": "访问站点", "icon": "open_in_new", "link": "/techblog/"},
                ],
            },
            {
                "title": "系统设置",
                "separator": True,
                "collapsible": True,
                "items": [
                    {"title": "用户管理", "icon": "person", "link": "/admin/auth/user/"},
                ],
            },
        ],
    },
}
