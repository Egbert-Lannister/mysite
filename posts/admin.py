from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.contrib.sites.models import Site
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.text import slugify
from taggit.admin import TagAdmin as BaseTagAdmin
from taggit.models import Tag
from taggit.utils import parse_tags
from unfold.admin import ModelAdmin
from unfold.decorators import display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
import yaml

from .models import Post, Series, compute_content_hash, generate_unique_slug
from .services import ALLOWED_IMAGE_EXTENSIONS, extract_zip_safely, rewrite_image_paths

# ---------------------------------------------------------------------------
# Unregister and re-register with Unfold styling
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Post Admin actions
# ---------------------------------------------------------------------------
@admin.action(description="发布选中的文章")
def make_published(modeladmin, request, queryset):
    count = queryset.update(published=True)
    modeladmin.message_user(request, f"成功发布 {count} 篇文章")


@admin.action(description="设为草稿")
def make_unpublished(modeladmin, request, queryset):
    count = queryset.update(published=False)
    modeladmin.message_user(request, f"已将 {count} 篇文章设为草稿")


# ---------------------------------------------------------------------------
# Series Admin
# ---------------------------------------------------------------------------
class SeriesAdminForm(forms.ModelForm):
    class Meta:
        model = Series
        fields = "__all__"

    class Media:
        js = ()  # paste-upload JS is inlined in the change_form template


@admin.register(Series)
class SeriesAdmin(ModelAdmin):
    form = SeriesAdminForm
    list_display = (
        "display_title",
        "display_post_count",
        "display_order",
        "display_featured",
        "display_updated",
    )
    list_filter = ("is_featured",)
    search_fields = ("title", "slug", "description")
    prepopulated_fields = {"slug": ("title",)}
    ordering = ["order", "-created_at"]
    change_form_template = "admin/posts/series/change_form.html"
    
    fieldsets = (
        ("基本信息", {
            "fields": ("title", "slug", "description"),
            "description": "设置系列的标题、链接别名和简介",
        }),
        ("展示设置", {
            "fields": ("cover_image", "order", "is_featured"),
            "description": "封面图片支持文件选择或剪贴板粘贴上传",
        }),
    )
    
    @display(description="系列标题")
    def display_title(self, obj):
        return obj.title
    
    @display(description="文章数量")
    def display_post_count(self, obj):
        count = obj.post_count
        return format_html(
            '<span class="text-primary-600 font-semibold">{}</span> 篇', count
        )
    
    @display(description="排序")
    def display_order(self, obj):
        return obj.order
    
    @display(description="首页推荐", label={"是": "success", "否": "warning"})
    def display_featured(self, obj):
        return "是" if obj.is_featured else "否"
    
    @display(description="最近更新")
    def display_updated(self, obj):
        latest = obj.latest_post_date
        return latest.strftime("%Y-%m-%d") if latest else "-"


# ---------------------------------------------------------------------------
# Post Admin Form
# ---------------------------------------------------------------------------
class PostAdminForm(forms.ModelForm):
    upload_file = forms.FileField(
        required=False,
        label="上传文件",
        help_text="支持 .md 文件或包含 md+图片的 .zip 文件",
        widget=forms.FileInput(attrs={"accept": ".md,.zip"}),
    )

    class Meta:
        model = Post
        fields = "__all__"
        widgets = {
            "content": forms.Textarea(attrs={"rows": 25, "style": "font-family: monospace;"}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = timezone.now()
        for field_name in ("title", "slug", "content", "date", "category"):
            if field_name in self.fields:
                self.fields[field_name].required = False
        self._upload_warnings: list[str] = []
        self._tags_str = ""

    def clean(self):
        cleaned = super().clean()
        upload_file = cleaned.get("upload_file")
        
        if upload_file:
            filename = upload_file.name.lower()
            if filename.endswith(".zip"):
                self._process_zip(upload_file, cleaned)
            elif filename.endswith(".md"):
                self._process_md(upload_file, cleaned)
            else:
                raise forms.ValidationError("不支持的文件格式，请上传 .md 或 .zip 文件")
        
        for field_name in ("title", "content", "date", "category"):
            if not cleaned.get(field_name):
                if field_name == "date":
                    cleaned["date"] = timezone.now()
                elif field_name == "category":
                    cleaned["category"] = "engineering"
                elif not upload_file:
                    self.add_error(field_name, "此字段是必填项")
        if not cleaned.get("slug") and cleaned.get("title"):
            cleaned["slug"] = generate_unique_slug(cleaned["title"])
        return cleaned

    # -- file processors ---------------------------------------------------

    def _process_md(self, file, cleaned):
        try:
            text = file.read().decode("utf-8")
            self._parse_markdown(text, cleaned)
        except Exception as e:
            raise forms.ValidationError(f"解析 Markdown 文件失败: {e}")

    def _process_zip(self, file, cleaned):
        import tempfile
        import zipfile

        try:
            with tempfile.TemporaryDirectory() as tmp:
                temp_path = Path(tmp)
                md_path, images, warnings = extract_zip_safely(file, temp_path)
                self._upload_warnings.extend(warnings)
                
                if not md_path:
                    raise forms.ValidationError("ZIP 文件中未找到 .md 文件")
                
                text = md_path.read_text(encoding="utf-8")
                self._parse_markdown(text, cleaned)
                
                if images:
                    slug = cleaned.get("slug") or generate_unique_slug(
                        cleaned.get("title", "untitled")
                    )
                    new_content, missing = rewrite_image_paths(
                        cleaned["content"], slug, temp_path, images,
                    )
                    cleaned["content"] = new_content
                    if missing:
                        self._upload_warnings.append(
                            f"以下图片引用未在 ZIP 中找到: {', '.join(missing)}"
                        )
        
        except zipfile.BadZipFile:
            raise forms.ValidationError("无效的 ZIP 文件")
        except forms.ValidationError:
            raise
        except Exception as e:
            raise forms.ValidationError(f"处理 ZIP 文件失败: {e}")

    def _parse_markdown(self, text: str, cleaned):
        from .services import clean_notion_filename, extract_title_from_markdown

        FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
        m = FRONT_MATTER_RE.match(text)
        
        if m:
            fm_raw, body = m.groups()
            meta = yaml.safe_load(fm_raw) or {}
            
            if meta.get("title"):
                cleaned["title"] = meta["title"]
            if meta.get("description"):
                cleaned["description"] = meta["description"]
            if meta.get("date"):
                date = datetime.fromisoformat(str(meta["date"]))
                if timezone.is_naive(date):
                    date = timezone.make_aware(date)
                cleaned["date"] = date
            if meta.get("category"):
                raw_cat = meta["category"]
                cat_map = {"tech": "engineering", "paper": "research"}
                cleaned["category"] = cat_map.get(raw_cat, raw_cat)
            
            raw_slug = str(meta.get("slug") or "").strip()
            if raw_slug:
                slug = slugify(raw_slug)
                if not slug:
                    slug = generate_unique_slug(meta.get("title") or cleaned.get("title", ""))
                cleaned["slug"] = slug
            else:
                cleaned["slug"] = generate_unique_slug(
                    meta.get("title") or cleaned.get("title", "")
                )
            
            cleaned["content"] = body.strip()
            
            tags = meta.get("tags", [])
            if isinstance(tags, list):
                self._tags_str = ", ".join(str(t) for t in tags)
            else:
                self._tags_str = str(tags) if tags else ""
        else:
            cleaned["content"] = text.strip()
            title = extract_title_from_markdown(text)
            if title and not cleaned.get("title"):
                cleaned["title"] = title
            if not cleaned.get("date"):
                cleaned["date"] = timezone.now()
            if not cleaned.get("category"):
                cleaned["category"] = "engineering"
            if cleaned.get("title") and not cleaned.get("slug"):
                cleaned["slug"] = generate_unique_slug(cleaned["title"])
            self._upload_warnings.append("未检测到 YAML front matter，已从内容自动提取元数据")
        
        content_hash = compute_content_hash(cleaned["content"])
        existing = (
            Post.objects.filter(content_hash=content_hash)
            .exclude(slug=cleaned.get("slug", ""))
            .first()
        )
        if existing:
            self._upload_warnings.append(
                f"警告: 发现内容相同的文章「{existing.title}」(slug: {existing.slug})"
            )

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save()
            if self._tags_str:
                instance.tags.set(parse_tags(self._tags_str))
            self.save_m2m()
        return instance


# ---------------------------------------------------------------------------
# Post Admin
# ---------------------------------------------------------------------------
@admin.register(Post)
class PostAdmin(ModelAdmin):
    form = PostAdminForm
    list_display = (
        "display_title",
        "display_category",
        "display_date",
        "display_status",
        "display_actions",
    )
    list_filter = ("category", "published", "tags", "date")
    list_filter_submit = True
    search_fields = ("title", "description", "content")
    prepopulated_fields = {"slug": ("title",)}
    actions = [make_published, make_unpublished]
    date_hierarchy = "date"
    list_per_page = 20
    readonly_fields = ("content_hash", "created_at", "updated_at")
    
    change_form_template = "admin/posts/post/change_form.html"

    fieldsets = (
        ("基本信息", {
            "fields": ("title", "slug", "upload_file"),
            "description": "填写文章基本信息，或上传 .md/.zip 文件自动导入",
        }),
        ("文章内容", {
            "fields": ("description", "content"),
            "description": "摘要会显示在文章列表，正文支持 Markdown 与 LaTeX；右侧为与前台一致的实时预览。",
            "classes": ("wide",),
        }),
        ("发布设置", {
            "fields": ("date", "category", "tags", "published"),
            "description": "标签：多个请用英文逗号分隔；单个标签可含空格。",
        }),
        ("系列设置", {
            "fields": ("series", "series_order"),
            "description": "将文章关联到某个系列，并设置系列内排序。",
            "classes": ("collapse",),
        }),
        ("系统信息", {
            "fields": ("content_hash", "created_at", "updated_at"),
            "classes": ("collapse",),
            "description": "系统自动生成的信息",
        }),
    )
    
    @display(description="标题")
    def display_title(self, obj):
        return obj.title
    
    @display(
        description="分类",
        label={
            "Engineering": "info",
            "Research": "warning",
            "Notes": "success",
            "Projects": "info",
        },
    )
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
        url = reverse("posts:post_detail", args=[obj.slug])
        return format_html(
            '<a href="{}" target="_blank" style="color: #8b5cf6;">查看 →</a>', url
        )

    def add_view(self, request, form_url="", extra_context=None):
        from django.shortcuts import redirect
        return redirect("admin_upload")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if hasattr(form, "_upload_warnings") and form._upload_warnings:
            from django.contrib import messages
            for warning in form._upload_warnings:
                messages.warning(request, warning)
