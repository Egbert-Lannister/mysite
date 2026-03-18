from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = "posts"

urlpatterns = [
    path("", views.index, name="index"),
    # Category list pages
    path("engineering/", views.category_list, {"category": "engineering"}, name="engineering_list"),
    path("research/", views.category_list, {"category": "research"}, name="research_list"),
    path("notes/", views.category_list, {"category": "notes"}, name="notes_list"),
    path("projects/", views.category_list, {"category": "projects"}, name="projects_list"),
    # 301 redirects for legacy URLs (SEO-safe)
    path("tech/", RedirectView.as_view(url="/techblog/engineering/", permanent=True), name="tech_list"),
    path("paper/", RedirectView.as_view(url="/techblog/research/", permanent=True), name="paper_list"),
    # Tags & Search
    path("tags/<str:tag>/", views.tag_list, name="tag_list"),
    path("search/", views.search, name="search"),
    # Series URLs
    path("series/", views.series_list, name="series_list"),
    path("series/<slug:slug>/", views.series_detail, name="series_detail"),
    # Post detail (must be last to avoid catching other routes)
    path("<slug:slug>/", views.post_detail, name="post_detail"),
]
