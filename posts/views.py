from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from markdown import markdown

from .models import Post


def index(request):
    qs = Post.objects.filter(published=True).order_by("-date")
    paginator = Paginator(qs, 10)
    page = request.GET.get("page")
    posts = paginator.get_page(page)
    return render(request, "index.html", {"posts": posts})


def category_list(request, category: str):
    qs = Post.objects.filter(published=True, category=category).order_by("-date")
    paginator = Paginator(qs, 10)
    page = request.GET.get("page")
    posts = paginator.get_page(page)
    return render(request, "category.html", {"posts": posts, "category": category})


def tag_list(request, tag: str):
    qs = Post.objects.filter(published=True, tags__name__in=[tag]).order_by("-date")
    paginator = Paginator(qs, 10)
    page = request.GET.get("page")
    posts = paginator.get_page(page)
    return render(request, "tag.html", {"posts": posts, "tag": tag})


def search(request):
    query = request.GET.get("q", "").strip()
    posts = []
    if query:
        try:
            from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

            vector = SearchVector("title", weight="A") + SearchVector("content", weight="B")
            search_query = SearchQuery(query)
            posts = (
                Post.objects.filter(published=True)
                .annotate(rank=SearchRank(vector, search_query))
                .filter(rank__gte=0.1)
                .order_by("-rank", "-date")
            )
        except Exception:
            posts = Post.objects.filter(
                published=True,
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(content__icontains=query),
            ).order_by("-date")
    return render(request, "search.html", {"posts": posts, "query": query})


def post_detail(request, slug: str):
    post = get_object_or_404(Post, slug=slug, published=True)
    html = markdown(post.content, extensions=["fenced_code", "tables", "toc"])  # Prism handles highlight
    return render(request, "detail.html", {"post": post, "html": html})
