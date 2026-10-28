from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Post, ProjectMedia, Series
from .project_layout import build_media_sections, layout_media_group
from .services import (
    ALLOWED_IMAGE_EXTENSIONS,
    generate_summary,
    generate_tags,
    parse_upload_file,
    process_markdown_content,
)
from .utils import render_markdown_with_toc


# ---------------------------------------------------------------------------
# Public views
# ---------------------------------------------------------------------------
def _require_any_permission(request, *permissions):
    if not any(request.user.has_perm(permission) for permission in permissions):
        raise PermissionDenied


PROJECT_CARD_MEDIA_PRIORITY = {"framework": 0, "method": 1, "results": 2, "comparison": 3, "gallery": 4}


def _project_card_prefetch():
    return Prefetch(
        "project_media",
        queryset=ProjectMedia.objects.filter(kind="image").order_by("order", "id"),
        to_attr="card_images",
    )


def _assign_project_card_images(projects):
    for project in projects:
        images = sorted(project.card_images, key=lambda item: (PROJECT_CARD_MEDIA_PRIORITY.get(item.section, 5), item.order, item.pk))
        project.card_image = images[0] if images else None


def index(request):
    published = Post.objects.filter(published=True).select_related("series").prefetch_related("tags")
    post_list = list(published.exclude(category="projects").order_by("-date")[:7])
    projects = list(
        published.filter(category="projects")
        .prefetch_related(_project_card_prefetch())
        .order_by("-project_featured", "-date")[:3]
    )
    _assign_project_card_images(projects)
    featured_series = Series.objects.filter(is_featured=True).order_by("order")[:3]

    return render(request, "index.html", {
        "lead_post": post_list[0] if post_list else None,
        "posts": post_list[1:],
        "projects": projects,
        "featured_series": featured_series,
    })


CATEGORY_META = {
    "engineering": {"label": "Engineering", "desc": "Software engineering insights and best practices"},
    "research":    {"label": "Research",    "desc": "Academic papers and research explorations"},
    "notes":       {"label": "Notes",       "desc": "Learning notes, reading summaries, and quick references"},
    "projects":    {"label": "Projects",    "desc": "Project showcases, demos, and build logs"},
}


def category_list(request, category: str):
    qs = Post.objects.filter(published=True, category=category).select_related("series").prefetch_related("tags")
    if category == "projects":
        qs = qs.prefetch_related(_project_card_prefetch())
    ordering = ("-project_featured", "-date") if category == "projects" else ("-date",)
    qs = qs.order_by(*ordering)
    paginator = Paginator(qs, 10)
    posts = paginator.get_page(request.GET.get("page"))
    if category == "projects":
        _assign_project_card_images(posts.object_list)
    meta = CATEGORY_META.get(category, {"label": category.title(), "desc": ""})
    return render(request, "category.html", {
        "posts": posts,
        "category": category,
        "category_label": meta["label"],
        "category_desc": meta["desc"],
    })


def tag_list(request, tag: str):
    qs = Post.objects.filter(published=True, tags__name__in=[tag]).select_related("series").prefetch_related("tags").order_by("-date").distinct()
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
    if query:
        posts = posts.select_related("series").prefetch_related("tags")
    posts = Paginator(posts, 10).get_page(request.GET.get("page"))
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
    if post.category == "projects":
        return redirect("posts:project_detail", slug=post.slug)
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
        ).order_by("date")
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
        "category_url_name": f"posts:{post.category}_list",
        "html": html,
        "toc_items": toc_items,
        "giscus": giscus_config,
        "giscus_enabled": giscus_enabled,
        "series_context": series_context,
    })


def project_detail(request, slug: str):
    project = get_object_or_404(Post, slug=slug, category="projects", published=True)
    is_html_project = project.project_content_mode == "html"
    media = list(project.project_media.all())
    method_media = []
    framework_media = []
    sections = []
    method_html = experiments_html = conclusion_html = ""
    toc_items = []

    if is_html_project:
        from .html_import import sanitize_project_html
        html = sanitize_project_html(project.content)
        hero_media = None
        if project.project_cover:
            hero_media = {"url": project.project_cover.url, "title": f"{project.title} cover", "description": project.description}
    else:
        html, toc_items = render_markdown_with_toc(project.content)
        method_html = render_markdown_with_toc(project.project_method)[0] if project.project_method.strip() else ""
        experiments_html = render_markdown_with_toc(project.project_experiments)[0] if project.project_experiments.strip() else ""
        conclusion_html = render_markdown_with_toc(project.project_conclusion)[0] if project.project_conclusion.strip() else ""
        method_media = [item for item in media if item.section == "method"]
        framework_media = [item for item in media if item.section == "framework"]
        sections = build_media_sections(media)
        hero_media = None
        if project.project_cover:
            hero_media = {"url": project.project_cover.url, "title": f"{project.title} cover", "description": project.description}
        else:
            candidates = framework_media or method_media
            if candidates:
                item = candidates.pop(0)
                hero_media = {"url": item.file.url, "title": item.title, "description": item.description}

    citation_url = project.paper_url or project.demo_url or project.github_url
    citation = None
    if citation_url:
        safe_title = project.title.replace("{", "").replace("}", "")
        citation = f"@misc{{{project.slug},\n  title = {{{safe_title}}},\n  year = {{{project.date.year}}},\n  url = {{{citation_url}}}\n}}"
    return render(request, "project_detail.html", {
        "project": project,
        "html": html,
        "toc_items": toc_items,
        "is_html_project": is_html_project,
        "hero_media": hero_media,
        "method_html": method_html,
        "experiments_html": experiments_html,
        "conclusion_html": conclusion_html,
        "method_media_rows": layout_media_group(method_media),
        "framework_media_rows": layout_media_group(framework_media),
        "media_sections": sections,
        "citation": citation,
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


def admin_project_upload(request):
    """Project-specific upload entry point."""
    if request.method == "POST":
        uploaded_file = request.FILES.get("upload_file")
        if not uploaded_file:
            messages.error(request, "请选择项目 Markdown 或 ZIP 文件")
            return redirect("admin_project_upload")
        try:
            parsed = parse_upload_file(uploaded_file)
        except Exception as exc:
            messages.error(request, f"处理项目文件失败：{exc}")
            return redirect("admin_project_upload")
        parsed["category"] = "projects"
        parsed["mode"] = "project_upload"
        parsed["project_status"] = parsed.get("project_status", "active")
        request.session["project_upload_preview"] = parsed
        return redirect("admin_project_upload_preview")

    from django.contrib import admin as django_admin
    context = django_admin.site.each_context(request)
    context.update({"title": "上传项目", "content_title": "上传项目"})
    return render(request, "admin/project_upload.html", context)


def admin_project_upload_blank(request):
    """Start an empty project editor without an upload file."""
    request.session["project_upload_preview"] = {
        "title": "", "slug": "", "description": "", "category": "projects",
        "tags": "", "date": "", "content": "", "image_count": 0,
        "staging_dir": "", "md_filename": "", "has_frontmatter": False,
        "warnings": [], "mode": "project_blank", "project_status": "active",
        "project_tech_stack": "", "github_url": "", "demo_url": "",
        "paper_url": "", "project_featured": False,
    }
    return redirect("admin_project_upload_preview")


def admin_project_upload_preview(request):
    """Preview and publish a project with project-specific metadata."""
    preview_data = request.session.get("project_upload_preview")
    if not preview_data:
        messages.error(request, "没有待预览的项目，请先上传项目文件")
        return redirect("admin_project_upload")

    if request.method == "POST":
        overrides = {
            "title": request.POST.get("title", "").strip(),
            "slug": request.POST.get("slug", "").strip(),
            "description": request.POST.get("description", "").strip(),
            "tags": request.POST.get("tags", "").strip(),
            "date": request.POST.get("date", "").strip(),
            "category": "projects",
            "project_status": request.POST.get("project_status", "active").strip(),
            "project_tech_stack": request.POST.get("project_tech_stack", "").strip(),
            "github_url": request.POST.get("github_url", "").strip(),
            "demo_url": request.POST.get("demo_url", "").strip(),
            "paper_url": request.POST.get("paper_url", "").strip(),
            "project_featured": request.POST.get("project_featured", ""),
        }
        text = request.POST.get("content", "").strip() or preview_data.get("content", "")
        if not text:
            messages.error(request, "项目正文不能为空")
            return redirect("admin_project_upload_preview")

        staging_dir = preview_data.get("staging_dir", "")
        temp_dir = Path(staging_dir) if staging_dir and Path(staging_dir).exists() else None
        images = [f for f in temp_dir.rglob("*") if f.is_file() and f.suffix.lower().lstrip(".") in ALLOWED_IMAGE_EXTENSIONS] if temp_dir else []
        try:
            post, created, warnings = process_markdown_content(
                text, images, temp_dir, md_filename=preview_data.get("md_filename", ""), overrides=overrides,
            )
            messages.success(request, f"成功{'创建' if created else '更新'}项目：{post.title}")
            for warning in warnings:
                messages.warning(request, warning)
        except Exception as exc:
            messages.error(request, f"发布项目失败：{exc}")
            return redirect("admin_project_upload_preview")
        finally:
            if staging_dir and Path(staging_dir).exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            request.session.pop("project_upload_preview", None)
        return redirect("admin:posts_post_changelist")

    preview_html, _ = render_markdown_with_toc(preview_data.get("content", "")[:3000])
    from django.contrib import admin as django_admin
    context = django_admin.site.each_context(request)
    context.update({
        "data": preview_data, "preview_html": preview_html,
        "is_blank_mode": preview_data.get("mode") == "project_blank",
        "title": "项目预览与编辑", "content_title": "项目预览与编辑",
    })
    return render(request, "admin/project_upload_preview.html", context)


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
    """Render sanitized Markdown only for users with content add/change permission."""
    _require_any_permission(request, "posts.add_post", "posts.change_post", "posts.add_project", "posts.change_project")
    content = request.POST.get("content", "")
    max_chars = int(getattr(settings, "MARKDOWN_PREVIEW_MAX_CHARS", 100_000))
    if len(content) > max_chars:
        return JsonResponse(
            {
                "ok": False,
                "html": "",
                "error": f"正文过长，预览上限为 {max_chars} 字符",
            },
            status=413,
        )
    try:
        html, _ = render_markdown_with_toc(content)
    except Exception as e:
        return JsonResponse({"ok": False, "html": "", "error": str(e)}, status=400)
    return JsonResponse({"ok": True, "html": html, "error": None})


@require_POST
def admin_generate_tags(request):
    """Staff-only: ask DeepSeek for 3 topical tags based on title + content."""
    _require_any_permission(request, "posts.add_post", "posts.change_post", "posts.add_project", "posts.change_project")
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


@require_POST
def admin_generate_summary(request):
    """Staff-only: generate an optional summary without blocking publication."""
    _require_any_permission(request, "posts.add_post", "posts.change_post", "posts.add_project", "posts.change_project")
    title = request.POST.get("title", "").strip()
    content = request.POST.get("content", "").strip()

    if not content:
        return JsonResponse(
            {"ok": False, "summary": "", "error": "正文为空，无法生成摘要"},
            status=400,
        )

    summary = generate_summary(title, content)
    if not summary:
        return JsonResponse(
            {
                "ok": False,
                "summary": "",
                "error": "未能生成摘要，请稍后重试或手动填写",
            },
            status=502,
        )

    return JsonResponse({"ok": True, "summary": summary, "error": None})


# ---------------------------------------------------------------------------
# Cover image upload endpoint (AJAX, used by admin paste-upload)
# ---------------------------------------------------------------------------
@require_POST
def upload_cover_image(request):
    """Accept a verified image only with series add/change permission."""
    _require_any_permission(request, "posts.add_series", "posts.change_series")
    image = request.FILES.get("image")
    if not image:
        return JsonResponse({"ok": False, "error": "No image provided"}, status=400)

    ext = Path(image.name).suffix.lower().lstrip(".")
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return JsonResponse({"ok": False, "error": f"不支持的图片格式: .{ext}"}, status=400)

    if image.size > int(getattr(settings, "UPLOAD_MAX_FILE_SIZE", 10 * 1024 * 1024)):
        return JsonResponse({"ok": False, "error": "图片文件过大"}, status=400)

    try:
        from PIL import Image
        image.seek(0)
        with Image.open(image) as uploaded_image:
            uploaded_image.verify()
        image.seek(0)
    except Exception:
        return JsonResponse({"ok": False, "error": "不是有效的图片文件"}, status=400)

    from django.core.files.storage import default_storage
    safe_name = Path(image.name).name
    path = default_storage.save(f"series_covers/uploads/{safe_name}", image)
    url = default_storage.url(path)
    return JsonResponse({"ok": True, "url": url})
