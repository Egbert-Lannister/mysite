import re
import os
import zipfile
import shutil
from pathlib import Path
from datetime import datetime
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.text import slugify
from taggit.utils import parse_tags
import yaml

from .models import Post, Series, generate_unique_slug, generate_series_slug, compute_content_hash
from .utils import render_markdown_with_toc


def index(request):
    qs = Post.objects.filter(published=True).order_by("-date")
    paginator = Paginator(qs, 10)
    page = request.GET.get("page")
    posts = paginator.get_page(page)
    
    # 获取首页推荐的系列（最多3个）
    featured_series = Series.objects.filter(is_featured=True).order_by("order")[:3]
    
    return render(request, "index.html", {
        "posts": posts,
        "featured_series": featured_series,
    })


CATEGORY_META = {
    "engineering": {"label": "Engineering", "desc": "Software engineering insights and best practices"},
    "research": {"label": "Research", "desc": "Academic papers and research explorations"},
    "notes": {"label": "Notes", "desc": "Learning notes, reading summaries, and quick references"},
    "projects": {"label": "Projects", "desc": "Project showcases, demos, and build logs"},
}


def category_list(request, category: str):
    qs = Post.objects.filter(published=True, category=category).order_by("-date")
    paginator = Paginator(qs, 10)
    page = request.GET.get("page")
    posts = paginator.get_page(page)
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
                Q(title__icontains=query) | Q(description__icontains=query) | Q(content__icontains=query),
                published=True,
            ).order_by("-date")
    return render(request, "search.html", {"posts": posts, "query": query})


def series_list(request):
    """系列列表页"""
    series = Series.objects.all().order_by("order", "-created_at")
    return render(request, "series_list.html", {"series_list": series})


def series_detail(request, slug: str):
    """系列详情页 — 通过绑定的 tag 聚合文章"""
    series = get_object_or_404(Series, slug=slug)
    posts = series.get_aggregated_posts()
    
    return render(request, "series_detail.html", {
        "series": series,
        "posts": posts,
        "post_count": posts.count(),
    })


def post_detail(request, slug: str):
    post = get_object_or_404(Post, slug=slug, published=True)
    
    # Render markdown with TOC
    html, toc_items = render_markdown_with_toc(post.content)
    
    # Giscus configuration from settings
    giscus_config = {
        'repo': getattr(settings, 'GISCUS_REPO', ''),
        'repo_id': getattr(settings, 'GISCUS_REPO_ID', ''),
        'category': getattr(settings, 'GISCUS_CATEGORY', ''),
        'category_id': getattr(settings, 'GISCUS_CATEGORY_ID', ''),
        'mapping': getattr(settings, 'GISCUS_MAPPING', 'pathname'),
        'reactions_enabled': getattr(settings, 'GISCUS_REACTIONS_ENABLED', '1'),
        'emit_metadata': getattr(settings, 'GISCUS_EMIT_METADATA', '0'),
        'input_position': getattr(settings, 'GISCUS_INPUT_POSITION', 'top'),
        'lang': getattr(settings, 'GISCUS_LANG', 'zh-CN'),
    }
    
    # Check if Giscus is configured
    giscus_enabled = bool(giscus_config['repo'] and giscus_config['repo_id'])
    
    # Series navigation
    series_context = None
    if post.series:
        series_posts = Post.objects.filter(
            series=post.series,
            published=True
        ).order_by('series_order')
        
        total_posts = series_posts.count()
        current_index = None
        
        # 找到当前文章在系列中的位置
        for idx, p in enumerate(series_posts, 1):
            if p.pk == post.pk:
                current_index = idx
                break
        
        series_context = {
            'series': post.series,
            'posts': series_posts,
            'total': total_posts,
            'current_index': current_index,
            'prev_post': post.get_series_prev(),
            'next_post': post.get_series_next(),
        }
    
    return render(request, "detail.html", {
        "post": post,
        "html": html,
        "toc_items": toc_items,
        "giscus": giscus_config,
        "giscus_enabled": giscus_enabled,
        "series_context": series_context,
    })


# =============================================================================
# Admin Upload Views (enhanced with ZIP support)
# =============================================================================
ALLOWED_IMAGE_EXTENSIONS = getattr(settings, 'UPLOAD_ALLOWED_IMAGE_EXTENSIONS', 
                                    {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'})
MAX_FILE_SIZE = getattr(settings, 'UPLOAD_MAX_FILE_SIZE', 10 * 1024 * 1024)
MAX_ZIP_SIZE = getattr(settings, 'UPLOAD_MAX_ZIP_SIZE', 50 * 1024 * 1024)


def is_safe_path(base_path: Path, target_path: Path) -> bool:
    """Check for zip slip vulnerability"""
    try:
        target_path.resolve().relative_to(base_path.resolve())
        return True
    except ValueError:
        return False


def rewrite_image_paths(content: str, slug: str, temp_dir: Path, images: list) -> tuple[str, list]:
    """Rewrite image paths in markdown content to use media URLs.
    Handles Notion exports where paths are URL-encoded (e.g. image%201.png -> image 1.png).
    """
    from urllib.parse import unquote

    media_dir = Path(settings.MEDIA_ROOT) / 'posts' / slug
    media_dir.mkdir(parents=True, exist_ok=True)
    
    image_map = {}
    for img_path in images:
        try:
            rel_path = img_path.relative_to(temp_dir)
        except ValueError:
            rel_path = Path(img_path.name)
        
        from urllib.parse import quote, unquote
        # Decode URL-encoded filenames (Notion exports: %E5%B1%8F... → 屏幕截图...)
        decoded_name = unquote(img_path.name)
        # Sanitize: replace spaces with underscores for web safety
        safe_name = decoded_name.replace(' ', '_')
        base_stem = Path(safe_name).stem
        base_suffix = Path(safe_name).suffix
        target_path = media_dir / safe_name
        counter = 1
        while target_path.exists():
            safe_name = f"{base_stem}_{counter}{base_suffix}"
            target_path = media_dir / safe_name
            counter += 1
        
        shutil.copy2(img_path, target_path)
        media_url = f"{settings.MEDIA_URL}posts/{slug}/{safe_name}"
        
        # Build all reference patterns: original name, decoded name, URL-encoded variants
        name = img_path.name                               # raw ZIP filename (may have spaces or %XX)
        name_decoded = decoded_name                        # URL-decoded
        rel_str = str(rel_path).replace('\\', '/')
        name_encoded = quote(name)
        decoded_encoded = quote(name_decoded)

        for ref in [
            name, name_decoded,
            f"./{name}", f"./{name_decoded}",
            f"assets/{name}", f"assets/{name_decoded}",
            f"./assets/{name}", f"./assets/{name_decoded}",
            name_encoded, decoded_encoded,
            f"./{name_encoded}", f"./{decoded_encoded}",
            rel_str, quote(rel_str),
            str(rel_path), str(rel_path).replace('\\', '/'),
        ]:
            image_map[ref] = media_url
    
    img_pattern = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
    missing = []
    
    def replace_image(match):
        alt, path = match.group(1), match.group(2)
        if path in image_map:
            return f'![{alt}]({image_map[path]})'
        # Try URL-decoded version
        decoded = unquote(path)
        if decoded in image_map:
            return f'![{alt}]({image_map[decoded]})'
        # Try double-decoded (Notion sometimes double-encodes)
        double_decoded = unquote(decoded)
        if double_decoded in image_map:
            return f'![{alt}]({image_map[double_decoded]})'
        # Fallback: match by filename suffix
        normalized = path.lstrip('./').replace('\\', '/')
        decoded_norm = unquote(normalized)
        for orig, url in image_map.items():
            orig_decoded = unquote(orig)
            if decoded_norm == orig_decoded.lstrip('./').replace('\\', '/'):
                return f'![{alt}]({url})'
            if decoded_norm.endswith(Path(orig_decoded).name):
                return f'![{alt}]({url})'
        missing.append(path)
        return match.group(0)
    
    return img_pattern.sub(replace_image, content), missing


def _parse_upload_file(uploaded_file) -> dict:
    """Parse an uploaded .md or .zip file, extract metadata, content, and image count.
    Returns a dict with parsed fields for preview. Images are saved to a staging dir.
    """
    import tempfile
    filename = uploaded_file.name.lower()
    result = {"warnings": [], "image_count": 0, "md_filename": ""}

    FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)

    if filename.endswith('.zip'):
        if uploaded_file.size > MAX_ZIP_SIZE:
            raise ValueError(f"ZIP 文件过大: {uploaded_file.size / 1024 / 1024:.1f}MB")

        staging_dir = Path(tempfile.mkdtemp(prefix="upload_"))
        zip_path = staging_dir / "upload.zip"
        with open(zip_path, 'wb') as f:
            for chunk in uploaded_file.chunks():
                f.write(chunk)

        md_path = None
        images = []
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                target_path = staging_dir / info.filename
                if not is_safe_path(staging_dir, target_path):
                    result["warnings"].append(f"跳过危险路径: {info.filename}")
                    continue
                ext = Path(info.filename).suffix.lower().lstrip('.')
                if ext == 'md' and md_path is None:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    zf.extract(info, staging_dir)
                    md_path = target_path
                elif ext in ALLOWED_IMAGE_EXTENSIONS:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    zf.extract(info, staging_dir)
                    images.append(target_path)

        if not md_path:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise ValueError("ZIP 文件中未找到 .md 文件")

        text = md_path.read_text(encoding='utf-8')
        result["md_filename"] = md_path.name
        result["image_count"] = len(images)
        result["staging_dir"] = str(staging_dir)

    elif filename.endswith('.md'):
        text = uploaded_file.read().decode("utf-8")
        result["md_filename"] = uploaded_file.name
        result["staging_dir"] = ""
    else:
        raise ValueError("不支持的文件格式，请上传 .md 或 .zip 文件")

    # Parse front matter
    m = FRONT_MATTER_RE.match(text)
    if m:
        fm_raw, body = m.groups()
        meta = yaml.safe_load(fm_raw) or {}
        result["has_frontmatter"] = True
    else:
        meta = {}
        body = text
        result["has_frontmatter"] = False
        result["warnings"].append("未检测到 YAML front matter")

    # Extract fields
    title = meta.get("title") or _extract_title_from_markdown(body) or ""
    if not title and result["md_filename"]:
        title = _clean_notion_filename(result["md_filename"])

    raw_category = meta.get("category") or ""
    category_map = {"tech": "engineering", "paper": "research"}
    category = category_map.get(raw_category, raw_category)

    tags = meta.get("tags", [])
    tags_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags or "")

    date_val = meta.get("date")
    date_str = str(date_val) if date_val else ""

    result.update({
        "title": title or "Untitled",
        "slug": str(meta.get("slug") or ""),
        "description": meta.get("description") or "",
        "category": category,
        "tags": tags_str,
        "date": date_str,
        "content": body.strip(),
    })
    return result


def admin_upload(request):
    """Step 1: Upload file → parse → redirect to preview."""
    from .models import Post
    category_choices = Post.CATEGORY_CHOICES

    if request.method == "POST":
        uploaded_file = request.FILES.get("upload_file")
        if not uploaded_file:
            messages.error(request, "请选择要上传的文件")
            return redirect("admin_upload")

        try:
            parsed = _parse_upload_file(uploaded_file)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect("admin_upload")
        except Exception as e:
            messages.error(request, f"处理文件时出错：{str(e)}")
            return redirect("admin_upload")

        # Store parsed data in session for the preview step
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


def admin_upload_preview(request):
    """Step 2: Preview parsed content, let user edit, then publish."""
    from .models import Post
    category_choices = Post.CATEGORY_CHOICES
    preview_data = request.session.get("upload_preview")

    if not preview_data:
        messages.error(request, "没有待预览的上传内容，请先上传文件")
        return redirect("admin_upload")

    if request.method == "POST":
        # Collect final values from the form
        overrides = {}
        for field in ("title", "slug", "description", "category", "tags", "date"):
            val = request.POST.get(field, "").strip()
            if val:
                overrides[field] = val
        content_override = request.POST.get("content", "").strip()

        staging_dir = preview_data.get("staging_dir", "")
        md_filename = preview_data.get("md_filename", "")
        text = content_override or preview_data.get("content", "")

        # Reconstruct full markdown (use content as body directly)
        images = []
        temp_dir = None
        if staging_dir and Path(staging_dir).exists():
            temp_dir = Path(staging_dir)
            for f in temp_dir.rglob("*"):
                if f.is_file() and f.suffix.lower().lstrip('.') in ALLOWED_IMAGE_EXTENSIONS:
                    images.append(f)

        try:
            post, created, warnings = process_markdown_content(
                text, images, temp_dir,
                md_filename=md_filename, overrides=overrides,
            )
            action = "创建" if created else "更新"
            messages.success(request, f"成功{action}文章：{post.title}")
            for w in warnings:
                messages.warning(request, w)
        except Exception as e:
            messages.error(request, f"发布失败：{str(e)}")
            return redirect("admin_upload_preview")
        finally:
            # Clean up staging dir and session
            if staging_dir and Path(staging_dir).exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            request.session.pop("upload_preview", None)

        return redirect("admin:posts_post_changelist")

    # GET: render preview page with parsed data
    # Generate a content preview (first 500 chars rendered)
    from .utils import render_markdown_with_toc
    content_text = preview_data.get("content", "")
    preview_html, _ = render_markdown_with_toc(content_text[:3000])

    from django.contrib import admin as django_admin
    context = django_admin.site.each_context(request)
    context.update({
        "data": preview_data,
        "preview_html": preview_html,
        "category_choices": category_choices,
        "title": "预览与编辑",
        "content_title": "预览与编辑",
    })
    return render(request, "admin/upload_preview.html", context)


def _extract_title_from_markdown(text: str) -> str:
    """Extract title from the first # heading in markdown content."""
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('# ') and not line.startswith('## '):
            return line.lstrip('# ').strip()
    return ""


def _clean_notion_filename(filename: str) -> str:
    """Extract a usable title from Notion export filenames like 'CS231n c1b9c48acce34dfe93316414d16ae003.md'."""
    from pathlib import Path
    stem = Path(filename).stem
    # Notion appends a 32-char hex UUID at the end
    cleaned = re.sub(r'\s+[0-9a-f]{32}$', '', stem)
    return cleaned.strip() if cleaned.strip() else stem


def process_markdown_content(text: str, images: list, temp_dir,
                             md_filename: str = "", overrides: dict | None = None) -> tuple:
    """Process markdown content and create/update post.
    Supports both YAML-front-matter and plain markdown (e.g. Notion exports).
    ``overrides`` contains manually filled form fields that take priority when
    YAML front matter is absent.
    """
    FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
    m = FRONT_MATTER_RE.match(text)
    warnings = []
    overrides = overrides or {}

    if m:
        fm_raw, body = m.groups()
        meta = yaml.safe_load(fm_raw) or {}
    else:
        meta = {}
        body = text
        warnings.append("未检测到 YAML front matter，已使用手动填写的元数据")

    # Priority: manual override > front-matter > auto-extract > default
    title = overrides.get("title") or meta.get("title") or ""
    if not title:
        title = _extract_title_from_markdown(body)
    if not title and md_filename:
        title = _clean_notion_filename(md_filename)
    if not title:
        title = "Untitled"

    date_str = overrides.get("date") or meta.get("date")
    if date_str:
        date = datetime.fromisoformat(str(date_str))
        if timezone.is_naive(date):
            date = timezone.make_aware(date)
    else:
        date = timezone.now()
    
    override_tags = overrides.get("tags", "").strip()
    if override_tags:
        tags_str = override_tags
    else:
        tags = meta.get("tags", [])
        tags_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags or "")
    
    raw_category = overrides.get("category") or meta.get("category") or "engineering"
    category_map = {"tech": "engineering", "paper": "research"}
    category = category_map.get(raw_category, raw_category)
    description = overrides.get("description") or meta.get("description", "")
    
    raw_slug = overrides.get("slug") or str(meta.get("slug") or "").strip()
    slug = slugify(raw_slug) if raw_slug else generate_unique_slug(title)
    if not slug:
        slug = generate_unique_slug(title)
    
    content = body.strip()
    
    # Rewrite image paths if we have images
    if images and temp_dir:
        content, missing = rewrite_image_paths(content, slug, temp_dir, images)
        if missing:
            warnings.append(f"以下图片引用未找到: {', '.join(missing)}")
    
    # Check for duplicate content
    content_hash = compute_content_hash(content)
    existing_by_hash = Post.objects.filter(content_hash=content_hash).exclude(slug=slug).first()
    if existing_by_hash:
        warnings.append(f"警告: 发现内容相同的文章「{existing_by_hash.title}」")
    
    # Handle series from front matter
    series_instance = None
    series_order = None
    series_name = meta.get("series")
    if series_name:
        series_name = str(series_name).strip()
        series_slug = slugify(series_name)
        if not series_slug:
            series_slug = generate_series_slug(series_name)
        
        # 获取或创建系列
        series_instance, series_created = Series.objects.get_or_create(
            slug=series_slug,
            defaults={
                "title": series_name,
                "description": f"自动创建的系列：{series_name}",
            }
        )
        if series_created:
            warnings.append(f"已自动创建系列「{series_name}」")
        
        # 处理 series_order
        series_order_raw = meta.get("series_order")
        if series_order_raw is not None:
            try:
                series_order = int(series_order_raw)
            except (ValueError, TypeError):
                warnings.append(f"series_order 值无效，已忽略: {series_order_raw}")
    
    # Create or update post
    post, created = Post.objects.update_or_create(
        slug=slug,
        defaults={
            "title": title,
            "description": description,
            "content": content,
            "date": date,
            "category": category,
            "published": True,
            "series": series_instance,
            "series_order": series_order,
        },
    )
    
    if tags_str:
        post.tags.set(parse_tags(tags_str))
    
    return post, created, warnings


@require_POST
def admin_preview_markdown(request):
    """
    Staff-only (via admin_view): render Markdown + math placeholders the same
    way as the public site; KaTeX runs in the admin preview pane.
    """
    content = request.POST.get("content", "")
    try:
        html, _ = render_markdown_with_toc(content)
    except Exception as e:
        return JsonResponse(
            {"ok": False, "html": "", "error": str(e)},
            status=400,
        )
    return JsonResponse({"ok": True, "html": html, "error": None})

