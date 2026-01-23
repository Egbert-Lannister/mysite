"""
URL configuration for mysite project.
"""
from django.conf import settings
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

# Only include browser reload URLs in development
if settings.DEBUG:
    urlpatterns.insert(0, path('__reload__/', include('django_browser_reload.urls')))
