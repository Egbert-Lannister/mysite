from __future__ import annotations

from pathlib import Path

from django import forms
from django.conf import settings
from PIL import Image

from .models import Post, Project, ProjectMedia, Series


ADMIN_CONTROL_CLASS = (
    "w-full min-w-0 rounded-md border border-base-300 dark:border-base-600 "
    "bg-white dark:bg-base-800 px-3 py-2 text-sm text-base-900 dark:text-base-100 "
    "placeholder-base-400 dark:placeholder-base-500 focus:border-primary-500 "
    "focus:outline-none focus:ring-1 focus:ring-primary-500"
)


class StyledFormMixin:
    def _style_fields(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect)):
                continue
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {ADMIN_CONTROL_CLASS}".strip()
            if isinstance(widget, forms.Textarea) and not widget.attrs.get("rows"):
                widget.attrs["rows"] = 3


class HttpURLFormField(forms.URLField):
    def clean(self, value):
        value = super().clean(value)
        if value and not value.lower().startswith(("http://", "https://")):
            raise forms.ValidationError("仅允许 HTTP/HTTPS 地址。", code="invalid_scheme")
        return value


class MultipleFileInput(forms.FileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(item, initial) for item in data]
        return [single_clean(data, initial)] if data else []


class BaseContentEditorForm(StyledFormMixin, forms.Form):
    title = forms.CharField(label="标题", max_length=255, required=True)
    slug = forms.SlugField(
        label="URL 别名（slug）", max_length=255, required=False,
        help_text="留空自动生成。新建流程遇到重复 slug 会提示，不会覆盖已有内容。",
    )
    description = forms.CharField(
        label="摘要", required=False, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="可选；用于列表、搜索结果与页面摘要。",
    )
    date = forms.DateTimeField(
        label="发布日期", required=False,
        input_formats=("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"),
        help_text="留空使用当前时间；可手动输入 YYYY-MM-DD HH:MM:SS。",
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "YYYY-MM-DD HH:MM:SS", "data-datetime": "true"}),
    )
    tags = forms.CharField(label="标签", required=False, help_text="英文逗号分隔；单个标签内可包含空格。")
    content = forms.CharField(
        label="Markdown 正文", required=True,
        widget=forms.Textarea(attrs={"rows": 24, "spellcheck": "false"}),
        help_text="支持 Markdown 与 $...$ / $$...$$ 数学公式。原始 HTML 会经允许列表清理。",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
        self.fields["content"].widget.attrs["class"] += " font-mono leading-relaxed"

    def clean_slug(self):
        value = (self.cleaned_data.get("slug") or "").strip()
        if value and Post.objects.filter(slug=value).exists():
            raise forms.ValidationError("该 slug 已存在，请换一个；本次保存不会覆盖已有内容。")
        return value


class ArticleEditorForm(BaseContentEditorForm):
    category = forms.ChoiceField(
        label="分类", choices=[choice for choice in Post.CATEGORY_CHOICES if choice[0] != "projects"],
        help_text="项目请使用独立的新建项目流程。",
    )
    series = forms.ModelChoiceField(
        label="所属系列", queryset=Series.objects.none(), required=False,
        help_text="可选；不归属系列时留空。",
    )
    series_order = forms.IntegerField(
        label="系列内排序", min_value=1, required=False,
        help_text="大于 0 的整数，且不能与同系列其他文章重复。",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["series"].queryset = Series.objects.order_by("title")

    def clean(self):
        cleaned = super().clean()
        series = cleaned.get("series")
        order = cleaned.get("series_order")
        if order and not series:
            self.add_error("series", "设置系列内排序前，请先选择系列。")
        if series and order and Post.objects.filter(series=series, series_order=order).exists():
            self.add_error("series_order", "该系列内排序已被使用。")
        return cleaned


class ProjectEditorForm(BaseContentEditorForm):
    project_status = forms.ChoiceField(label="项目状态", choices=Post.PROJECT_STATUS_CHOICES)
    content = forms.CharField(
        label="06 · Overview", required=True,
        help_text="说明问题、目标与项目范围；不要在这里堆叠全部实验素材。",
        widget=forms.Textarea(attrs={"rows": 16}),
    )
    project_method = forms.CharField(
        label="07 · Method", required=False,
        help_text="方法、模型或实现路径；支持 Markdown、代码与数学公式。",
        widget=forms.Textarea(attrs={"rows": 16}),
    )
    project_experiments = forms.CharField(
        label="08 · Experiments", required=False,
        help_text="实验设计、数据集和评估方式；具体素材在 05 中按实验组组织。",
        widget=forms.Textarea(attrs={"rows": 12}),
    )
    project_conclusion = forms.CharField(
        label="Conclusion", required=False,
        help_text="结论、局限和后续工作；不要从图片或文件名推断未经验证的数值。",
        widget=forms.Textarea(attrs={"rows": 10}),
    )
    project_tech_stack = forms.CharField(
        label="技术栈", max_length=500, required=False,
        help_text="英文逗号分隔，例如 Python, PyTorch, Django。",
    )
    github_url = HttpURLFormField(label="GitHub 地址", required=False, help_text="完整 HTTP/HTTPS 地址；无链接时留空。")
    paper_url = HttpURLFormField(label="Paper 地址", required=False, help_text="具体论文页面；不要使用占位地址。")
    demo_url = HttpURLFormField(label="Demo 地址", required=False, help_text="独立部署的演示页面；本地 HTML 请使用安全 ZIP 导入。")
    project_featured = forms.BooleanField(label="设为项目精选", required=False, help_text="精选项目会在 Projects 列表与首页项目区优先展示。")
    project_cover = forms.ImageField(
        label="项目封面", required=False,
        help_text="PNG/JPEG/GIF/WebP，最大 10MB；发布失败时有效文件会保留在暂存区。",
    )
    new_media_files = MultipleFileField(
        label="添加实验图片或视频", required=False,
        help_text="每次最多 10 个文件。图片最大 10MB，MP4/WebM 最大 50MB；添加后可逐项填写说明与排序。",
    )
    new_media_section = forms.ChoiceField(
        label="新素材默认分组", choices=ProjectMedia.SECTION_CHOICES, initial="results", required=False,
    )
    new_media_group = forms.CharField(label="新素材数据集 / 实验组", max_length=120, required=False)

    def clean_paper_url(self):
        value = self.cleaned_data.get("paper_url", "")
        if value and (value.rstrip("/") in {"https://arxiv.org", "http://arxiv.org"} or "your-paper-id" in value):
            raise forms.ValidationError("请填写具体论文地址，或留空。")
        return value

    def clean_project_cover(self):
        cover = self.cleaned_data.get("project_cover")
        if cover and cover.size > int(getattr(settings, "UPLOAD_MAX_FILE_SIZE", 10 * 1024 * 1024)):
            raise forms.ValidationError("封面图片不能超过 10MB。")
        return cover

    def clean_new_media_files(self):
        files = self.cleaned_data.get("new_media_files") or []
        if len(files) > 10:
            raise forms.ValidationError("每次最多添加 10 个媒体文件。")
        image_extensions = set(getattr(settings, "UPLOAD_ALLOWED_IMAGE_EXTENSIONS", {"png", "jpg", "jpeg", "gif", "webp"}))
        video_extensions = set(getattr(settings, "UPLOAD_ALLOWED_VIDEO_EXTENSIONS", {"mp4", "webm"}))
        for upload in files:
            extension = Path(upload.name).suffix.lower().lstrip(".")
            if extension not in image_extensions | video_extensions:
                raise forms.ValidationError(f"{upload.name}：仅支持 PNG/JPEG/GIF/WebP/MP4/WebM。")
            is_video = extension in video_extensions
            limit = int(getattr(settings, "UPLOAD_MAX_VIDEO_SIZE" if is_video else "UPLOAD_MAX_FILE_SIZE", 50 * 1024 * 1024 if is_video else 10 * 1024 * 1024))
            if upload.size > limit:
                raise forms.ValidationError(f"{upload.name}：超过单文件大小限制。")
            if not is_video:
                try:
                    upload.seek(0)
                    with Image.open(upload) as image:
                        image.verify()
                    upload.seek(0)
                except Exception as exc:
                    raise forms.ValidationError(f"{upload.name}：不是有效的图片文件。") from exc
        return files


class HtmlProjectEditorForm(ProjectEditorForm):
    """Metadata-only form; sanitized HTML and its media stay in the staged draft."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("content", "project_method", "project_experiments", "project_conclusion", "project_cover", "new_media_files", "new_media_section", "new_media_group"):
            self.fields.pop(name, None)


class ProjectModelForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = (
            "title", "slug", "description", "project_status", "project_tech_stack", "tags",
            "github_url", "paper_url", "demo_url", "project_cover", "content",
            "project_method", "project_experiments", "project_conclusion", "date",
            "published", "project_featured",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "content": forms.Textarea(attrs={"rows": 16, "class": "font-mono"}),
            "project_method": forms.Textarea(attrs={"rows": 16, "class": "font-mono"}),
            "project_experiments": forms.Textarea(attrs={"rows": 12, "class": "font-mono"}),
            "project_conclusion": forms.Textarea(attrs={"rows": 10, "class": "font-mono"}),
        }

    def clean_paper_url(self):
        value = self.cleaned_data.get("paper_url", "")
        if value and (value.rstrip("/") in {"https://arxiv.org", "http://arxiv.org"} or "your-paper-id" in value):
            raise forms.ValidationError("请填写具体论文地址，或留空。")
        return value


class ProjectMediaModelForm(forms.ModelForm):
    class Meta:
        model = ProjectMedia
        fields = ("file", "kind", "section", "title", "group_name", "description", "order")
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def clean(self):
        cleaned = super().clean()
        upload = cleaned.get("file")
        if cleaned.get("kind") == ProjectMedia.KIND_IMAGE and hasattr(upload, "content_type"):
            try:
                upload.seek(0)
                with Image.open(upload) as image:
                    image.verify()
                upload.seek(0)
            except Exception:
                self.add_error("file", "不是有效的图片文件。")
        return cleaned
