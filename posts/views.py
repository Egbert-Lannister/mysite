import re
from datetime import datetime
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.text import slugify
from taggit.utils import parse_tags
from markdown import markdown
import yaml

from .models import Post
from .utils import generate_unique_slug


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
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(content__icontains=query),
                published=True,
            ).order_by("-date")
    return render(request, "search.html", {"posts": posts, "query": query})


def post_detail(request, slug: str):
    post = get_object_or_404(Post, slug=slug, published=True)
    html = markdown(post.content, extensions=["fenced_code", "tables", "toc"])  # Prism handles highlight
    return render(request, "detail.html", {"post": post, "html": html})


@login_required
def admin_upload(request):
    """自定义 Markdown 上传界面"""
    if request.method == "POST":
        if "markdown_file" in request.FILES:
            file = request.FILES["markdown_file"]
            try:
                text = file.read().decode("utf-8")
                FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
                m = FRONT_MATTER_RE.match(text)
                
                if not m:
                    messages.error(request, "Markdown 文件格式错误：缺少 YAML front matter")
                    return redirect("admin_upload")
                
                fm_raw, body = m.groups()
                meta = yaml.safe_load(fm_raw) or {}
                
                title = meta.get("title") or file.name.replace(".md", "")
                date_str = meta.get("date")
                if date_str:
                    date = datetime.fromisoformat(str(date_str))
                    if timezone.is_naive(date):
                        date = timezone.make_aware(date)
                else:
                    date = timezone.now()
                
                tags = meta.get("tags", [])
                if isinstance(tags, list):
                    tags_str = ", ".join(str(t) for t in tags)
                else:
                    tags_str = str(tags) if tags else ""
                
                category = meta.get("category") or ("tech" if "tech" in file.name.lower() else "paper")
                description = meta.get("description", "")
                raw_slug = str(meta.get("slug") or "").strip()
                if raw_slug:
                    slug = slugify(raw_slug)
                    if not slug:
                        slug = generate_unique_slug(title)
                else:
                    slug = generate_unique_slug(title)
                
                post, created = Post.objects.update_or_create(
                    slug=slug,
                    defaults={
                        "title": title,
                        "description": description,
                        "content": body.strip(),
                        "date": date,
                        "category": category,
                        "published": True,
                    },
                )
                
                if tags_str:
                    post.tags.set(parse_tags(tags_str))
                
                if created:
                    messages.success(request, f"成功创建文章：{title}")
                else:
                    messages.success(request, f"成功更新文章：{title}")
                
                return redirect("admin_posts")
            except Exception as e:
                messages.error(request, f"处理文件时出错：{str(e)}")
                return redirect("admin_upload")
    
    return render(request, "admin/upload.html")


@login_required
def admin_posts(request):
    """文章管理列表"""
    posts = Post.objects.all().order_by("-date")
    return render(request, "admin/posts.html", {"posts": posts})
