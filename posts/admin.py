import re
from datetime import datetime
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import User, Group
from django.contrib.sites.models import Site
from django import forms
from django.utils.html import format_html
from django.utils.text import slugify
from django.utils import timezone
from django.urls import reverse
from unfold.admin import ModelAdmin
from unfold.decorators import action, display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from taggit.admin import TagAdmin as BaseTagAdmin
from taggit.models import Tag
from taggit.utils import parse_tags
import yaml
from .models import Post
from .utils import generate_unique_slug


# ----- Unregister default admins and re-register with Unfold -----

# Unregister Site model (we don't need it in the sidebar)
try:
    admin.site.unregister(Site)
except admin.sites.NotRegistered:
    pass

# Re-register User with Unfold styling
admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


# Re-register Group with Unfold styling but hide from main view
admin.site.unregister(Group)


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


# Re-register Tag with Unfold styling
admin.site.unregister(Tag)


@admin.register(Tag)
class TagAdmin(BaseTagAdmin, ModelAdmin):
    list_display = ["name", "slug", "post_count"]
    search_fields = ["name", "slug"]
    
    @display(description="文章数量")
    def post_count(self, obj):
        count = obj.taggit_taggeditem_items.count()
        return count
    
    class Meta:
        verbose_name = "标签"
        verbose_name_plural = "标签管理"


@admin.action(description="✅ 发布选中的文章")
def make_published(modeladmin, request, queryset):
    count = queryset.update(published=True)
    modeladmin.message_user(request, f"成功发布 {count} 篇文章")


@admin.action(description="📝 设为草稿")
def make_unpublished(modeladmin, request, queryset):
    count = queryset.update(published=False)
    modeladmin.message_user(request, f"已将 {count} 篇文章设为草稿")


class PostAdminForm(forms.ModelForm):
    markdown_file = forms.FileField(
        required=False,
        label="上传 Markdown 文件",
        help_text="支持带有 YAML front matter 的 .md 文件，会自动解析标题、日期、标签等信息",
        widget=forms.FileInput(attrs={'accept': '.md'})
    )

    class Meta:
        model = Post
        fields = '__all__'
        widgets = {
            'content': forms.Textarea(attrs={'rows': 25, 'class': 'vLargeTextField', 'style': 'font-family: monospace;'}),
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['date'].initial = timezone.now()

    def clean(self):
        cleaned_data = super().clean()
        markdown_file = cleaned_data.get('markdown_file')
        
        if markdown_file:
            try:
                text = markdown_file.read().decode('utf-8')
                FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
                m = FRONT_MATTER_RE.match(text)
                
                if m:
                    fm_raw, body = m.groups()
                    meta = yaml.safe_load(fm_raw) or {}
                    
                    # 更新字段
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
                    
                    # 处理标签
                    tags = meta.get('tags', [])
                    if isinstance(tags, list):
                        tags_str = ", ".join(str(t) for t in tags)
                    else:
                        tags_str = str(tags) if tags else ""
                    self._tags_str = tags_str
            except Exception as e:
                raise forms.ValidationError(f"解析 Markdown 文件失败: {str(e)}")
        
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save()
            # 保存标签
            if hasattr(self, '_tags_str') and self._tags_str:
                instance.tags.set(parse_tags(self._tags_str))
            self.save_m2m()
        return instance


@admin.register(Post)
class PostAdmin(ModelAdmin):
    form = PostAdminForm
    list_display = ("display_title", "display_category", "display_date", "display_status", "display_actions")
    list_filter = ("category", "published", "tags", "date")
    list_filter_submit = True  # Add filter submit button
    search_fields = ("title", "description", "content")
    prepopulated_fields = {"slug": ("title",)}
    actions = [make_published, make_unpublished]
    date_hierarchy = "date"
    list_per_page = 20
    
    fieldsets = (
        ('📝 基本信息', {
            'fields': ('title', 'slug', 'markdown_file'),
            'description': '填写文章基本信息，或上传 Markdown 文件自动导入'
        }),
        ('📄 文章内容', {
            'fields': ('description', 'content'),
            'description': '摘要会显示在文章列表，正文支持 Markdown 语法'
        }),
        ('⚙️ 发布设置', {
            'fields': ('date', 'category', 'tags', 'published'),
            'description': '设置发布日期、分类和标签'
        }),
    )
    
    @display(description="标题", header=True)
    def display_title(self, obj):
        return obj.title
    
    @display(description="分类", label={
        "技术文章": "info",
        "论文笔记": "warning"
    })
    def display_category(self, obj):
        return obj.get_category_display()
    
    @display(description="发布日期")
    def display_date(self, obj):
        return obj.date.strftime("%Y-%m-%d")
    
    @display(description="状态", label={
        "已发布": "success",
        "草稿": "warning"
    })
    def display_status(self, obj):
        return "已发布" if obj.published else "草稿"
    
    @display(description="操作")
    def display_actions(self, obj):
        view_url = reverse('posts:post_detail', args=[obj.slug])
        return format_html(
            '<a href="{}" target="_blank" style="color: #8b5cf6; text-decoration: none;">查看文章 →</a>',
            view_url
        )
