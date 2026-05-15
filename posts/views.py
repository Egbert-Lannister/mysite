from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Post, Series
from .services import (
    ALLOWED_IMAGE_EXTENSIONS,
    generate_tags,
    parse_upload_file,
    process_markdown_content,
)
from .utils import render_markdown_with_toc


# ---------------------------------------------------------------------------
# Public views
# ---------------------------------------------------------------------------
def index(request):
    posts = Post.objects.filter(published=True).order_by("-date")[:6]
    featured_series = Series.objects.filter(is_featured=True).order_by("order")[:3]

    return render(request, "index.html", {
        "posts": posts,
        "featured_series": featured_series,
    })


CATEGORY_META = {
    "engineering": {"label": "Engineering", "desc": "Software engineering insights and best practices"},
    "research":    {"label": "Research",    "desc": "Academic papers and research explorations"},
    "notes":       {"label": "Notes",       "desc": "Learning notes, reading summaries, and quick references"},
    "projects":    {"label": "Projects",    "desc": "Project showcases, demos, and build logs"},
}


def category_list(request, category: str):
    qs = Post.objects.filter(published=True, category=category).order_by("-date")
    paginator = Paginator(qs, 10)
    posts = paginator.get_page(request.GET.get("page"))
    meta = CATEGORY_META.get(category, {"label": category.title(), "desc": ""})
    return render(request, "category.html", {
        "posts": posts,
        "category": category,
        "category_label": meta["label"],
        "category_desc": meta["desc"],
    })


def tag_list(request, tag: str):
    qs = Post.objects.filter(published=True, tags__name__in=[tag]).order_by("-date")
    paginator = Paginator(qs, 10)
    posts = paginator.get_page(request.GET.get("page"))
    return render(request, "tag.html", {"posts": posts, "tag": tag})


def search(request):
    from django.db import connection

    query = request.GET.get("q", "").strip()
    posts = Post.objects.none()
    if query:
        if connection.vendor == "postgresql":
            from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
            vector = SearchVector("title", weight="A") + SearchVector("content", weight="B")
            sq = SearchQuery(query)
            posts = (
                Post.objects.filter(published=True)
                .annotate(rank=SearchRank(vector, sq))
                .filter(rank__gte=0.1)
                .order_by("-rank", "-date")
            )
        else:
            posts = Post.objects.filter(
                Q(title__icontains=query) | Q(description__icontains=query) | Q(content__icontains=query),
                published=True,
            ).order_by("-date")
    return render(request, "search.html", {"posts": posts, "query": query})


def series_list(request):
    series = Series.objects.all().order_by("order", "-created_at")
    return render(request, "series_list.html", {"series_list": series})


def series_detail(request, slug: str):
    series = get_object_or_404(Series, slug=slug)
    posts = series.get_posts()
    return render(request, "series_detail.html", {
        "series": series,
        "posts": posts,
        "post_count": posts.count(),
    })


def post_detail(request, slug: str):
    post = get_object_or_404(Post, slug=slug, published=True)
    html, toc_items = render_markdown_with_toc(post.content)

    giscus_config = {
        "repo": getattr(settings, "GISCUS_REPO", ""),
        "repo_id": getattr(settings, "GISCUS_REPO_ID", ""),
        "category": getattr(settings, "GISCUS_CATEGORY", ""),
        "category_id": getattr(settings, "GISCUS_CATEGORY_ID", ""),
        "mapping": getattr(settings, "GISCUS_MAPPING", "pathname"),
        "reactions_enabled": getattr(settings, "GISCUS_REACTIONS_ENABLED", "1"),
        "emit_metadata": getattr(settings, "GISCUS_EMIT_METADATA", "0"),
        "input_position": getattr(settings, "GISCUS_INPUT_POSITION", "top"),
        "lang": getattr(settings, "GISCUS_LANG", "zh-CN"),
    }
    giscus_enabled = bool(giscus_config["repo"] and giscus_config["repo_id"])

    series_context = None
    if post.series:
        series_posts = Post.objects.filter(
            series=post.series, published=True,
        ).order_by("series_order")
        total_posts = series_posts.count()
        current_index = None
        for idx, p in enumerate(series_posts, 1):
            if p.pk == post.pk:
                current_index = idx
                break

        series_context = {
            "series": post.series,
            "posts": series_posts,
            "total": total_posts,
            "current_index": current_index,
            "prev_post": post.get_series_prev(),
            "next_post": post.get_series_next(),
        }

    return render(request, "detail.html", {
        "post": post,
        "html": html,
        "toc_items": toc_items,
        "giscus": giscus_config,
        "giscus_enabled": giscus_enabled,
        "series_context": series_context,
    })


# ---------------------------------------------------------------------------
# Admin upload views (thin wrappers around services)
# ---------------------------------------------------------------------------
def admin_upload(request):
    """Step 1: Upload file -> parse -> redirect to preview."""
    category_choices = Post.CATEGORY_CHOICES

    if request.method == "POST":
        uploaded_file = request.FILES.get("upload_file")
        if not uploaded_file:
            messages.error(request, "请选择要上传的文件")
            return redirect("admin_upload")

        try:
            parsed = parse_upload_file(uploaded_file)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect("admin_upload")
        except Exception as e:
            messages.error(request, f"处理文件时出错：{e}")
            return redirect("admin_upload")

        request.session["upload_preview"] = {
            "title": parsed["title"],
            "slug": parsed["slug"],
            "description": parsed["description"],
            "category": parsed["category"],
            "tags": parsed["tags"],
            "date": parsed["date"],
            "content": parsed["content"],
            "image_count": parsed["image_count"],
            "staging_dir": parsed["staging_dir"],
            "md_filename": parsed["md_filename"],
            "has_frontmatter": parsed["has_frontmatter"],
            "warnings": parsed["warnings"],
            "series_id": "",
            "series_order": "",
            "mode": "upload",
        }
        return redirect("admin_upload_preview")

    from django.contrib import admin as django_admin
    context = django_admin.site.each_context(request)
    context.update({
        "category_choices": category_choices,
        "title": "上传文章",
        "content_title": "上传文章",
    })
    return render(request, "admin/upload.html", context)


def admin_upload_blank(request):
    """Skip step 1 — seed an empty preview session and jump straight to the editor."""
    request.session["upload_preview"] = {
        "title": "",
        "slug": "",
        "description": "",
        "category": "engineering",
        "tags": "",
        "date": "",
        "content": "",
        "image_count": 0,
        "staging_dir": "",
        "md_filename": "",
        "has_frontmatter": False,
        "warnings": [],
        "series_id": "",
        "series_order": "",
        "mode": "blank",
    }
    return redirect("admin_upload_preview")


def admin_upload_preview(request):
    """Step 2: Preview parsed content, let user edit, then publish."""
    category_choices = Post.CATEGORY_CHOICES
    preview_data = request.session.get("upload_preview")

    if not preview_data:
        messages.error(request, "没有待预览的内容，请先上传文件或新建空白文章")
        return redirect("admin_upload")

    if request.method == "POST":
        overrides: dict[str, str] = {}
        for field in ("title", "slug", "description", "category", "tags", "date"):
            val = request.POST.get(field, "").strip()
            if val:
                overrides[field] = val
        # Series fields are allowed to be empty (= no series)
        overrides["series_id"] = request.POST.get("series_id", "").strip()
        overrides["series_order"] = request.POST.get("series_order", "").strip()

        content_override = request.POST.get("content", "").strip()

        staging_dir = preview_data.get("staging_dir", "")
        md_filename = preview_data.get("md_filename", "")
        text = content_override or preview_data.get("content", "")

        if not text.strip():
            messages.error(request, "正文不能为空")
            return redirect("admin_upload_preview")

        images: list[Path] = []
        temp_dir: Path | None = None
        if staging_dir and Path(staging_dir).exists():
            temp_dir = Path(staging_dir)
            for f in temp_dir.rglob("*"):
                if f.is_file() and f.suffix.lower().lstrip(".") in ALLOWED_IMAGE_EXTENSIONS:
                    images.append(f)

        try:
            post, created, warnings = process_markdown_content(
                text, images, temp_dir,
                md_filename=md_filename,
                overrides=overrides,
            )
            action = "创建" if created else "更新"
            messages.success(request, f"成功{action}文章：{post.title}")
            for w in warnings:
                messages.warning(request, w)
        except Exception as e:
            messages.error(request, f"发布失败：{e}")
            return redirect("admin_upload_preview")
        finally:
            if staging_dir and Path(staging_dir).exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            request.session.pop("upload_preview", None)

        return redirect("admin:posts_post_changelist")

    preview_html, _ = render_markdown_with_toc(preview_data.get("content", "")[:3000])

    from django.contrib import admin as django_admin
    context = django_admin.site.each_context(request)
    context.update({
        "data": preview_data,
        "preview_html": preview_html,
        "category_choices": category_choices,
        "series_options": Series.objects.all().order_by("title"),
        "is_blank_mode": preview_data.get("mode") == "blank",
        "title": "预览与编辑",
        "content_title": "预览与编辑",
    })
    return render(request, "admin/upload_preview.html", context)


@require_POST
def admin_preview_markdown(request):
    """Staff-only: render Markdown + math placeholders the same way as the public site."""
    content = request.POST.get("content", "")
    try:
        html, _ = render_markdown_with_toc(content)
    except Exception as e:
        return JsonResponse({"ok": False, "html": "", "error": str(e)}, status=400)
    return JsonResponse({"ok": True, "html": html, "error": None})


@require_POST
def admin_generate_tags(request):
    """Staff-only: ask DeepSeek for 3 topical tags based on title + content."""
    title = request.POST.get("title", "").strip()
    content = request.POST.get("content", "").strip()
    try:
        count = int(request.POST.get("count", "3"))
    except (TypeError, ValueError):
        count = 3
    count = max(1, min(count, 8))

    if not content:
        return JsonResponse(
            {"ok": False, "tags": [], "error": "正文为空，无法生成标签"},
            status=400,
        )

    try:
        tags = generate_tags(title, content, count=count)
    except Exception as exc:
        return JsonResponse(
            {"ok": False, "tags": [], "error": f"调用 DeepSeek 失败: {exc}"},
            status=502,
        )

    if not tags:
        return JsonResponse(
            {
                "ok": False,
                "tags": [],
                "error": "未能从 DeepSeek 获取到标签（可能未配置 DEEPSEEK_API_KEY，或请求失败）",
            },
            status=502,
        )

    return JsonResponse({"ok": True, "tags": tags, "error": None})


# ---------------------------------------------------------------------------
# Cover image upload endpoint (AJAX, used by admin paste-upload)
# ---------------------------------------------------------------------------
@require_POST
def upload_cover_image(request):
    """Accept an image upload (file or pasted blob) and return its URL.

    Used by the Series admin form JS to handle clipboard paste uploads.
    """
    image = request.FILES.get("image")
    if not image:
        return JsonResponse({"ok": False, "error": "No image provided"}, status=400)

    ext = Path(image.name).suffix.lower().lstrip(".")
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return JsonResponse({"ok": False, "error": f"不支持的图片格式: .{ext}"}, status=400)

    if image.size > int(getattr(settings, "UPLOAD_MAX_FILE_SIZE", 10 * 1024 * 1024)):
        return JsonResponse({"ok": False, "error": "图片文件过大"}, status=400)

    from django.core.files.storage import default_storage
    path = default_storage.save(f"series_covers/uploads/{image.name}", image)
    url = default_storage.url(path)
    return JsonResponse({"ok": True, "url": url})
