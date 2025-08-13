from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("tech/", views.category_list, {"category": "tech"}, name="tech_list"),
    path("paper/", views.category_list, {"category": "paper"}, name="paper_list"),
    path("tags/<slug:tag>/", views.tag_list, name="tag_list"),
    path("search/", views.search, name="search"),
    path("<slug:slug>/", views.post_detail, name="post_detail"),
]
