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
    # Upload routes under /admin/ (before admin.site.urls so they take priority)
    path('admin/upload/', posts_views.admin_upload, name='admin_upload'),
    path('admin/upload/preview/', posts_views.admin_upload_preview, name='admin_upload_preview'),
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
