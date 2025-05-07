from django.urls import path
from . import views

urlpatterns = [
    path('', views.homepage_view, name='home'),
    path('paperlist/', views.paperlist_view, name='paperlist'),
]
