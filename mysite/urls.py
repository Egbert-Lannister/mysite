"""
URL configuration for mysite project.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from posts.feeds import LatestPostsFeed
from posts import views as posts_views

urlpatterns = [
    # Upload routes under /admin/ — wrapped with admin_view for full Unfold context
    path('admin/upload/', admin.site.admin_view(posts_views.admin_upload), name='admin_upload'),
    path('admin/upload/blank/', admin.site.admin_view(posts_views.admin_upload_blank), name='admin_upload_blank'),
    path('admin/upload/preview/', admin.site.admin_view(posts_views.admin_upload_preview), name='admin_upload_preview'),
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
    path('rss.xml', LatestPostsFeed(), name='rss'),
    path('techblog/', include('posts.urls')),
    path('', RedirectView.as_view(url='/techblog/', permanent=True)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns.insert(0, path('__reload__/', include('django_browser_reload.urls')))
