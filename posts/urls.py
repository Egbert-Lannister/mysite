from django.urls import path
from . import views

app_name = "posts"

urlpatterns = [
    path("", views.index, name="index"),
    path("tech/", views.category_list, {"category": "tech"}, name="tech_list"),
    path("paper/", views.category_list, {"category": "paper"}, name="paper_list"),
    path("tags/<str:tag>/", views.tag_list, name="tag_list"),
    path("search/", views.search, name="search"),
    # Series URLs
    path("series/", views.series_list, name="series_list"),
    path("series/<slug:slug>/", views.series_detail, name="series_detail"),
    # Admin URLs
    path("admin/upload/", views.admin_upload, name="admin_upload"),
    path("admin/posts/", views.admin_posts, name="admin_posts"),
    # Post detail (must be last to avoid catching other routes)
    path("<slug:slug>/", views.post_detail, name="post_detail"),
]
