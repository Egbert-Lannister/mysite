"""
URL configuration for mysite project.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from posts.feeds import LatestPostsFeed

urlpatterns = [
    path('admin/', admin.site.urls),
    path('rss.xml', LatestPostsFeed(), name='rss'),
    path('techblog/', include('posts.urls')),
    path('', RedirectView.as_view(url='/techblog/', permanent=True)),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns.insert(0, path('__reload__/', include('django_browser_reload.urls')))

# In production, nginx should serve media files
# But we can also serve them via whitenoise in some cases
