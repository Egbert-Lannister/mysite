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
    path('admin/upload/preview/', admin.site.admin_view(posts_views.admin_upload_preview), name='admin_upload_preview'),
    path(
        'admin/posts/preview-markdown/',
        admin.site.admin_view(posts_views.admin_preview_markdown),
        name='admin_post_preview_markdown',
    ),
    # Django admin
    path('admin/', admin.site.urls),
    # Public site
    path('rss.xml', LatestPostsFeed(), name='rss'),
    path('techblog/', include('posts.urls')),
    path('', RedirectView.as_view(url='/techblog/', permanent=True)),
]

# Always serve user-uploaded media files (posts images, etc.)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG:
    urlpatterns.insert(0, path('__reload__/', include('django_browser_reload.urls')))
