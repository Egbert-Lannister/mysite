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
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.text import slugify
from taggit.utils import parse_tags
import yaml

from .models import Post, generate_unique_slug, compute_content_hash
from .utils import render_markdown_with_toc


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
                Q(title__icontains=query) | Q(description__icontains=query) | Q(content__icontains=query),
                published=True,
            ).order_by("-date")
    return render(request, "search.html", {"posts": posts, "query": query})


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
    
    return render(request, "detail.html", {
        "post": post,
        "html": html,
        "toc_items": toc_items,
        "giscus": giscus_config,
        "giscus_enabled": giscus_enabled,
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
    """Rewrite image paths in markdown content to use media URLs."""
    media_dir = Path(settings.MEDIA_ROOT) / 'posts' / slug
    media_dir.mkdir(parents=True, exist_ok=True)
    
    image_map = {}
    for img_path in images:
        try:
            rel_path = img_path.relative_to(temp_dir)
        except ValueError:
            rel_path = Path(img_path.name)
        
        new_name = img_path.name
        target_path = media_dir / new_name
        counter = 1
        while target_path.exists():
            stem = img_path.stem
            suffix = img_path.suffix
            new_name = f"{stem}_{counter}{suffix}"
            target_path = media_dir / new_name
            counter += 1
        
        shutil.copy2(img_path, target_path)
        media_url = f"{settings.MEDIA_URL}posts/{slug}/{new_name}"
        
        # Map various possible references
        image_map[str(rel_path)] = media_url
        image_map[str(rel_path).replace('\\', '/')] = media_url
        image_map[img_path.name] = media_url
        image_map[f"./{img_path.name}"] = media_url
        image_map[f"assets/{img_path.name}"] = media_url
        image_map[f"./assets/{img_path.name}"] = media_url
    
    img_pattern = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
    missing = []
    
    def replace_image(match):
        alt, path = match.group(1), match.group(2)
        if path in image_map:
            return f'![{alt}]({image_map[path]})'
        normalized = path.lstrip('./').replace('\\', '/')
        for orig, url in image_map.items():
            if normalized == orig.lstrip('./').replace('\\', '/'):
                return f'![{alt}]({url})'
            if normalized.endswith(Path(orig).name):
                return f'![{alt}]({url})'
        missing.append(path)
        return match.group(0)
    
    return img_pattern.sub(replace_image, content), missing


@login_required
def admin_upload(request):
    """Enhanced Markdown/ZIP upload interface"""
    upload_warnings = []
    
    if request.method == "POST":
        uploaded_file = request.FILES.get("upload_file")
        
        if not uploaded_file:
            messages.error(request, "请选择要上传的文件")
            return redirect("posts:admin_upload")
        
        filename = uploaded_file.name.lower()
        
        try:
            if filename.endswith('.zip'):
                # Process ZIP file
                import tempfile
                
                if uploaded_file.size > MAX_ZIP_SIZE:
                    messages.error(request, f"ZIP 文件过大: {uploaded_file.size / 1024 / 1024:.1f}MB")
                    return redirect("posts:admin_upload")
                
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    
                    # Save and extract ZIP
                    zip_path = temp_path / "upload.zip"
                    with open(zip_path, 'wb') as f:
                        for chunk in uploaded_file.chunks():
                            f.write(chunk)
                    
                    # Extract safely
                    md_path = None
                    images = []
                    
                    with zipfile.ZipFile(zip_path, 'r') as zf:
                        for info in zf.infolist():
                            if info.is_dir():
                                continue
                            
                            target_path = temp_path / info.filename
                            if not is_safe_path(temp_path, target_path):
                                upload_warnings.append(f"跳过危险路径: {info.filename}")
                                continue
                            
                            ext = Path(info.filename).suffix.lower().lstrip('.')
                            
                            if ext == 'md' and md_path is None:
                                target_path.parent.mkdir(parents=True, exist_ok=True)
                                zf.extract(info, temp_path)
                                md_path = target_path
                            elif ext in ALLOWED_IMAGE_EXTENSIONS:
                                target_path.parent.mkdir(parents=True, exist_ok=True)
                                zf.extract(info, temp_path)
                                images.append(target_path)
                    
                    if not md_path:
                        messages.error(request, "ZIP 文件中未找到 .md 文件")
                        return redirect("posts:admin_upload")
                    
                    text = md_path.read_text(encoding='utf-8')
                    post, created, warnings = process_markdown_content(text, images, temp_path)
                    upload_warnings.extend(warnings)
                    
                    action = "创建" if created else "更新"
                    messages.success(request, f"成功{action}文章：{post.title}")
            
            elif filename.endswith('.md'):
                # Process single MD file
                text = uploaded_file.read().decode("utf-8")
                post, created, warnings = process_markdown_content(text, [], None)
                upload_warnings.extend(warnings)
                
                action = "创建" if created else "更新"
                messages.success(request, f"成功{action}文章：{post.title}")
            
            else:
                messages.error(request, "不支持的文件格式，请上传 .md 或 .zip 文件")
                return redirect("posts:admin_upload")
            
            # Show warnings
            for warning in upload_warnings:
                messages.warning(request, warning)
            
            return redirect("posts:admin_posts")
        
        except Exception as e:
            messages.error(request, f"处理文件时出错：{str(e)}")
            return redirect("posts:admin_upload")
    
    return render(request, "admin/upload.html")


def process_markdown_content(text: str, images: list, temp_dir) -> tuple:
    """Process markdown content and create/update post"""
    FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
    m = FRONT_MATTER_RE.match(text)
    warnings = []
    
    if not m:
        raise ValueError("Markdown 文件格式错误：缺少 YAML front matter")
    
    fm_raw, body = m.groups()
    meta = yaml.safe_load(fm_raw) or {}
    
    title = meta.get("title") or "Untitled"
    date_str = meta.get("date")
    if date_str:
        date = datetime.fromisoformat(str(date_str))
        if timezone.is_naive(date):
            date = timezone.make_aware(date)
    else:
        date = timezone.now()
    
    tags = meta.get("tags", [])
    tags_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags or "")
    
    category = meta.get("category") or "tech"
    description = meta.get("description", "")
    
    raw_slug = str(meta.get("slug") or "").strip()
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
        },
    )
    
    if tags_str:
        post.tags.set(parse_tags(tags_str))
    
    return post, created, warnings


@login_required
def admin_posts(request):
    """文章管理列表"""
    posts = Post.objects.all().order_by("-date")
    return render(request, "admin/posts.html", {"posts": posts})
