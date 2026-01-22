from django.urls import path
from . import views

app_name = "posts"

urlpatterns = [
    path("", views.index, name="index"),
    path("tech/", views.category_list, {"category": "tech"}, name="tech_list"),
    path("paper/", views.category_list, {"category": "paper"}, name="paper_list"),
    path("tags/<str:tag>/", views.tag_list, name="tag_list"),
    path("search/", views.search, name="search"),
    path("admin/upload/", views.admin_upload, name="admin_upload"),
    path("admin/posts/", views.admin_posts, name="admin_posts"),
    path("<slug:slug>/", views.post_detail, name="post_detail"),
]
