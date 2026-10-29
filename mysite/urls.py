"""
URL configuration for mysite project.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from django.views.i18n import set_language
from django.views.decorators.http import require_POST
from posts.feeds import LatestPostsFeed
from posts import views as posts_views
from posts import admin_uploads

urlpatterns = [
    # Upload routes under /admin/ — wrapped with admin_view for full Unfold context
    path('admin/upload/', admin.site.admin_view(admin_uploads.admin_upload), name='admin_upload'),
    path('admin/project-upload/', admin.site.admin_view(admin_uploads.admin_project_upload), name='admin_project_upload'),
    path('admin/project-upload/blank/', admin.site.admin_view(admin_uploads.admin_project_upload_blank), name='admin_project_upload_blank'),
    path('admin/project-upload/preview/', admin.site.admin_view(admin_uploads.admin_project_upload_preview), name='admin_project_upload_preview'),
    path('admin/upload/blank/', admin.site.admin_view(admin_uploads.admin_upload_blank), name='admin_upload_blank'),
    path('admin/upload/preview/', admin.site.admin_view(admin_uploads.admin_upload_preview), name='admin_upload_preview'),
    path(
        'admin/posts/preview-markdown/',
        admin.site.admin_view(posts_views.admin_preview_markdown),
        name='admin_post_preview_markdown',
    ),
    path(
        'admin/posts/generate-tags/',
        admin.site.admin_view(posts_views.admin_generate_tags),
        name='admin_post_generate_tags',
    ),
    path(
        'admin/posts/generate-summary/',
        admin.site.admin_view(posts_views.admin_generate_summary),
        name='admin_post_generate_summary',
    ),
    path(
        'admin/upload-cover-image/',
        admin.site.admin_view(posts_views.upload_cover_image),
        name='admin_upload_cover_image',
    ),
    # Django admin
    path('admin/', admin.site.urls),
    # Public site
    path('techblog/language/', require_POST(set_language), name='set_language'),
    path('rss.xml', LatestPostsFeed(), name='rss'),
    path('techblog/', include('posts.urls')),
    path('', RedirectView.as_view(url='/techblog/', permanent=True)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns.insert(0, path('__reload__/', include('django_browser_reload.urls')))
