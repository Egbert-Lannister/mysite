"""
Django settings for mysite project.
"""
from pathlib import Path
import os
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-aoep*#($phdkef+f(o7w0g+c-ds$ff!1b0dh@4kis$)^$d4-ff')

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
    'django.contrib.postgres',
    'taggit',
    'tailwind',
    'posts',
    'theme',
]

if DEBUG:
    INSTALLED_APPS.append('django_browser_reload')

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
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

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'zh-hans'
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
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

SITE_ID = 1

TAILWIND_APP_NAME = 'theme'
INTERNAL_IPS = ['127.0.0.1', 'localhost']

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# django-taggit: allow spaces inside a single tag; use commas to separate tags
TAGGIT_TAGS_FROM_STRING = 'posts.taggit_helpers.parse_tags_allow_spaces'

# =============================================================================
# Upload Settings
# =============================================================================
UPLOAD_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB per file
UPLOAD_MAX_ZIP_SIZE = 50 * 1024 * 1024   # 50MB for zip
UPLOAD_ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}

# =============================================================================
# Giscus Comments Configuration
# =============================================================================
GISCUS_REPO = os.environ.get('GISCUS_REPO', 'Egbert-Lannister/mysite')
GISCUS_REPO_ID = os.environ.get('GISCUS_REPO_ID', 'R_kgDOOmFPuw')
GISCUS_CATEGORY = os.environ.get('GISCUS_CATEGORY', 'General')
GISCUS_CATEGORY_ID = os.environ.get('GISCUS_CATEGORY_ID', 'DIC_kwDOOmFPu84C1aVt')
GISCUS_MAPPING = 'pathname'
GISCUS_REACTIONS_ENABLED = '1'
GISCUS_EMIT_METADATA = '0'
GISCUS_INPUT_POSITION = 'bottom'
GISCUS_LANG = 'zh-CN'

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
    "SITE_SYMBOL": "article",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "ENVIRONMENT": "mysite.admin_config.environment_callback",
    "COLORS": {
        "primary": {
            "50": "250 245 255", "100": "243 232 255", "200": "233 213 255",
            "300": "216 180 254", "400": "192 132 252", "500": "168 85 247",
            "600": "147 51 234", "700": "126 34 206", "800": "107 33 168",
            "900": "88 28 135", "950": "59 7 100",
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
