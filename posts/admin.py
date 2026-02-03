import re
import os
import zipfile
import hashlib
import shutil
from pathlib import Path
from datetime import datetime
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import User, Group
from django.contrib.sites.models import Site
from django import forms
from django.conf import settings
from django.utils.html import format_html
from django.utils.text import slugify
from django.utils import timezone
from django.urls import reverse
from unfold.admin import ModelAdmin
from unfold.decorators import display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from taggit.admin import TagAdmin as BaseTagAdmin
from taggit.models import Tag
from taggit.utils import parse_tags
import yaml
from .models import Post, Series, generate_unique_slug, generate_series_slug, compute_content_hash


# =============================================================================
# Security Constants
# =============================================================================
ALLOWED_IMAGE_EXTENSIONS = getattr(settings, 'UPLOAD_ALLOWED_IMAGE_EXTENSIONS', 
                                    {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'})
MAX_FILE_SIZE = getattr(settings, 'UPLOAD_MAX_FILE_SIZE', 10 * 1024 * 1024)
MAX_ZIP_SIZE = getattr(settings, 'UPLOAD_MAX_ZIP_SIZE', 50 * 1024 * 1024)


# =============================================================================
# Unregister and re-register with Unfold styling
# =============================================================================
try:
    admin.site.unregister(Site)
except admin.sites.NotRegistered:
    pass

admin.site.unregister(User)
@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


admin.site.unregister(Group)
@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


admin.site.unregister(Tag)
@admin.register(Tag)
class TagAdmin(BaseTagAdmin, ModelAdmin):
    list_display = ["name", "slug", "post_count"]
    search_fields = ["name", "slug"]
    
    @display(description="文章数量")
    def post_count(self, obj):
        return obj.taggit_taggeditem_items.count()


# =============================================================================
# Post Admin Actions
# =============================================================================
@admin.action(description="✅ 发布选中的文章")
def make_published(modeladmin, request, queryset):
    count = queryset.update(published=True)
    modeladmin.message_user(request, f"成功发布 {count} 篇文章")


@admin.action(description="📝 设为草稿")
def make_unpublished(modeladmin, request, queryset):
    count = queryset.update(published=False)
    modeladmin.message_user(request, f"已将 {count} 篇文章设为草稿")


# =============================================================================
# Series Admin
# =============================================================================
@admin.register(Series)
class SeriesAdmin(ModelAdmin):
    list_display = ("display_title", "display_post_count", "display_order", "display_featured", "display_updated")
    list_filter = ("is_featured",)
    search_fields = ("title", "slug", "description")  # 支持 autocomplete
    prepopulated_fields = {"slug": ("title",)}
    list_editable = ("order", "is_featured") if False else ()  # 通过 display 方法处理
    ordering = ["order", "-created_at"]
    
    fieldsets = (
        ('📚 基本信息', {
            'fields': ('title', 'slug', 'description'),
            'description': '设置系列的标题、链接别名和简介'
        }),
        ('🖼️ 展示设置', {
            'fields': ('cover_image', 'order', 'is_featured'),
            'description': '封面图片和排序设置'
        }),
    )
    
    @display(description="系列标题")
    def display_title(self, obj):
        return obj.title
    
    @display(description="文章数量")
    def display_post_count(self, obj):
        count = obj.post_count
        return format_html('<span class="text-primary-600 font-semibold">{}</span> 篇', count)
    
    @display(description="排序")
    def display_order(self, obj):
        return obj.order
    
    @display(description="首页推荐", label={"是": "success", "否": "warning"})
    def display_featured(self, obj):
        return "是" if obj.is_featured else "否"
    
    @display(description="最近更新")
    def display_updated(self, obj):
        latest = obj.latest_post_date
        if latest:
            return latest.strftime("%Y-%m-%d")
        return "-"


# =============================================================================
# ZIP Upload Utilities
# =============================================================================
def is_safe_path(base_path: Path, target_path: Path) -> bool:
    """Check for zip slip vulnerability - ensure path is within base"""
    try:
        target_path.resolve().relative_to(base_path.resolve())
        return True
    except ValueError:
        return False


def extract_zip_safely(zip_file, extract_dir: Path) -> tuple[Path | None, list[Path], list[str]]:
    """
    Safely extract ZIP file, returns (md_path, image_paths, warnings)
    Implements protection against:
    - Zip slip attacks
    - Oversized files
    - Non-allowed file types
    """
    md_file = None
    images = []
    warnings = []
    
    with zipfile.ZipFile(zip_file, 'r') as zf:
        total_size = sum(info.file_size for info in zf.infolist())
        if total_size > MAX_ZIP_SIZE:
            raise ValueError(f"ZIP 文件解压后过大: {total_size / 1024 / 1024:.1f}MB > {MAX_ZIP_SIZE / 1024 / 1024:.1f}MB")
        
        for info in zf.infolist():
            # Skip directories
            if info.is_dir():
                continue
            
            # Check for zip slip
            target_path = extract_dir / info.filename
            if not is_safe_path(extract_dir, target_path):
                warnings.append(f"跳过危险路径: {info.filename}")
                continue
            
            # Check file size
            if info.file_size > MAX_FILE_SIZE:
                warnings.append(f"文件过大已跳过: {info.filename}")
                continue
            
            # Get file extension
            ext = Path(info.filename).suffix.lower().lstrip('.')
            filename_lower = info.filename.lower()
            
            # Categorize file
            if ext == 'md':
                if md_file is None:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    zf.extract(info, extract_dir)
                    md_file = target_path
                else:
                    warnings.append(f"多个 MD 文件，仅使用第一个: {info.filename}")
            elif ext in ALLOWED_IMAGE_EXTENSIONS:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                zf.extract(info, extract_dir)
                images.append(target_path)
            else:
                warnings.append(f"不支持的文件类型已跳过: {info.filename}")
    
    return md_file, images, warnings


def rewrite_image_paths(content: str, slug: str, temp_dir: Path, images: list[Path]) -> tuple[str, list[str]]:
    """
    Rewrite image paths in markdown content to use media URLs.
    Returns (new_content, missing_images)
    """
    # Create target directory in media
    media_dir = Path(settings.MEDIA_ROOT) / 'posts' / slug
    media_dir.mkdir(parents=True, exist_ok=True)
    
    # Build a map of original paths to new URLs
    image_map = {}
    for img_path in images:
        # Get relative path from temp_dir
        try:
            rel_path = img_path.relative_to(temp_dir)
        except ValueError:
            rel_path = Path(img_path.name)
        
        # Copy to media directory
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
        
        # Map various possible references to the new URL
        media_url = f"{settings.MEDIA_URL}posts/{slug}/{new_name}"
        
        # Add all possible reference patterns
        image_map[str(rel_path)] = media_url
        image_map[str(rel_path).replace('\\', '/')] = media_url
        image_map[img_path.name] = media_url
        image_map[f"./{img_path.name}"] = media_url
        image_map[f"assets/{img_path.name}"] = media_url
        image_map[f"./assets/{img_path.name}"] = media_url
        image_map[f"images/{img_path.name}"] = media_url
        image_map[f"./images/{img_path.name}"] = media_url
    
    # Find all image references in content
    # Patterns: ![alt](path) or ![alt](path "title")
    img_pattern = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
    
    missing = []
    
    def replace_image(match):
        alt = match.group(1)
        path = match.group(2)
        
        # Try to find in our image map
        if path in image_map:
            return f'![{alt}]({image_map[path]})'
        
        # Try normalized path
        normalized = path.lstrip('./').replace('\\', '/')
        for orig, url in image_map.items():
            if normalized == orig.lstrip('./').replace('\\', '/'):
                return f'![{alt}]({url})'
            if normalized.endswith(Path(orig).name):
                return f'![{alt}]({url})'
        
        # Image not found
        missing.append(path)
        return match.group(0)
    
    new_content = img_pattern.sub(replace_image, content)
    return new_content, missing


# =============================================================================
# Post Admin Form with ZIP Support
# =============================================================================
class PostAdminForm(forms.ModelForm):
    upload_file = forms.FileField(
        required=False,
        label="上传文件",
        help_text="支持 .md 文件或包含 md+图片的 .zip 文件",
        widget=forms.FileInput(attrs={'accept': '.md,.zip'})
    )

    class Meta:
        model = Post
        fields = '__all__'
        widgets = {
            'content': forms.Textarea(attrs={'rows': 25, 'style': 'font-family: monospace;'}),
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['date'].initial = timezone.now()
        self._upload_warnings = []
        self._is_update = False

    def clean(self):
        cleaned_data = super().clean()
        upload_file = cleaned_data.get('upload_file')
        
        if upload_file:
            filename = upload_file.name.lower()
            
            if filename.endswith('.zip'):
                self._process_zip(upload_file, cleaned_data)
            elif filename.endswith('.md'):
                self._process_md(upload_file, cleaned_data)
            else:
                raise forms.ValidationError("不支持的文件格式，请上传 .md 或 .zip 文件")
        
        return cleaned_data

    def _process_md(self, file, cleaned_data):
        """Process single markdown file"""
        try:
            text = file.read().decode('utf-8')
            self._parse_markdown(text, cleaned_data)
        except Exception as e:
            raise forms.ValidationError(f"解析 Markdown 文件失败: {str(e)}")

    def _process_zip(self, file, cleaned_data):
        """Process ZIP file with md + images"""
        import tempfile
        
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Extract ZIP
                md_path, images, warnings = extract_zip_safely(file, temp_path)
                self._upload_warnings.extend(warnings)
                
                if not md_path:
                    raise forms.ValidationError("ZIP 文件中未找到 .md 文件")
                
                # Read and parse markdown
                text = md_path.read_text(encoding='utf-8')
                self._parse_markdown(text, cleaned_data)
                
                # Rewrite image paths if there are images
                if images:
                    slug = cleaned_data.get('slug') or generate_unique_slug(
                        cleaned_data.get('title', 'untitled')
                    )
                    new_content, missing = rewrite_image_paths(
                        cleaned_data['content'], slug, temp_path, images
                    )
                    cleaned_data['content'] = new_content
                    
                    if missing:
                        self._upload_warnings.append(
                            f"以下图片引用未在 ZIP 中找到: {', '.join(missing)}"
                        )
        
        except zipfile.BadZipFile:
            raise forms.ValidationError("无效的 ZIP 文件")
        except Exception as e:
            raise forms.ValidationError(f"处理 ZIP 文件失败: {str(e)}")

    def _parse_markdown(self, text: str, cleaned_data):
        """Parse markdown with YAML front matter"""
        FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
        m = FRONT_MATTER_RE.match(text)
        
        if m:
            fm_raw, body = m.groups()
            meta = yaml.safe_load(fm_raw) or {}
            
            if meta.get('title'):
                cleaned_data['title'] = meta['title']
            if meta.get('description'):
                cleaned_data['description'] = meta['description']
            if meta.get('date'):
                date = datetime.fromisoformat(str(meta['date']))
                if timezone.is_naive(date):
                    date = timezone.make_aware(date)
                cleaned_data['date'] = date
            if meta.get('category'):
                cleaned_data['category'] = meta['category']
            
            raw_slug = str(meta.get('slug') or "").strip()
            if raw_slug:
                slug = slugify(raw_slug)
                if not slug:
                    slug = generate_unique_slug(meta.get('title') or cleaned_data.get('title', ''))
                cleaned_data['slug'] = slug
            else:
                cleaned_data['slug'] = generate_unique_slug(
                    meta.get('title') or cleaned_data.get('title', '')
                )
            
            cleaned_data['content'] = body.strip()
            
            # Handle tags
            tags = meta.get('tags', [])
            if isinstance(tags, list):
                self._tags_str = ", ".join(str(t) for t in tags)
            else:
                self._tags_str = str(tags) if tags else ""
        else:
            cleaned_data['content'] = text.strip()
        
        # Check for duplicate content
        content_hash = compute_content_hash(cleaned_data['content'])
        existing = Post.objects.filter(content_hash=content_hash).exclude(
            slug=cleaned_data.get('slug', '')
        ).first()
        
        if existing:
            self._upload_warnings.append(
                f"警告: 发现内容相同的文章「{existing.title}」(slug: {existing.slug})"
            )

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save()
            if hasattr(self, '_tags_str') and self._tags_str:
                instance.tags.set(parse_tags(self._tags_str))
            self.save_m2m()
        return instance


# =============================================================================
# Post Admin
# =============================================================================
@admin.register(Post)
class PostAdmin(ModelAdmin):
    form = PostAdminForm
    list_display = ("display_title", "display_category", "display_date", "display_status", "display_actions")
    list_filter = ("category", "published", "tags", "date")
    list_filter_submit = True
    search_fields = ("title", "description", "content")
    prepopulated_fields = {"slug": ("title",)}
    actions = [make_published, make_unpublished]
    date_hierarchy = "date"
    list_per_page = 20
    readonly_fields = ("content_hash", "created_at", "updated_at")
    
    fieldsets = (
        ('📝 基本信息', {
            'fields': ('title', 'slug', 'upload_file'),
            'description': '填写文章基本信息，或上传 .md/.zip 文件自动导入'
        }),
        ('📄 文章内容', {
            'fields': ('description', 'content'),
            'description': '摘要会显示在文章列表，正文支持 Markdown 语法'
        }),
        ('⚙️ 发布设置', {
            'fields': ('date', 'category', 'tags', 'published'),
            'description': '设置发布日期、分类和标签'
        }),
        ('🔧 系统信息', {
            'fields': ('content_hash', 'created_at', 'updated_at'),
            'classes': ('collapse',),
            'description': '系统自动生成的信息'
        }),
    )
    
    @display(description="标题")
    def display_title(self, obj):
        return obj.title
    
    @display(description="分类", label={"技术文章": "info", "论文笔记": "warning"})
    def display_category(self, obj):
        return obj.get_category_display()
    
    @display(description="发布日期")
    def display_date(self, obj):
        return obj.date.strftime("%Y-%m-%d")
    
    @display(description="状态", label={"已发布": "success", "草稿": "warning"})
    def display_status(self, obj):
        return "已发布" if obj.published else "草稿"
    
    @display(description="操作")
    def display_actions(self, obj):
        url = reverse('posts:post_detail', args=[obj.slug])
        return format_html('<a href="{}" target="_blank" style="color: #8b5cf6;">查看 →</a>', url)
    
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        
        # Show upload warnings
        if hasattr(form, '_upload_warnings') and form._upload_warnings:
            from django.contrib import messages
            for warning in form._upload_warnings:
                messages.warning(request, warning)
