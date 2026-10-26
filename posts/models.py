from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, URLValidator
from django.db import models
from django.utils.text import slugify
from taggit.managers import TaggableManager
from PIL import Image


def generate_unique_slug(title: str, instance_pk: int | None = None) -> str:
    """Generate unique slug from title."""
    base_slug = slugify(title)
    if not base_slug:
        base_slug = uuid.uuid4().hex[:8]

    slug = base_slug
    counter = 1
    while Post.objects.filter(slug=slug).exclude(pk=instance_pk).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def generate_series_slug(title: str, instance_pk: int | None = None) -> str:
    """Generate unique slug for Series from title."""
    base_slug = slugify(title)
    if not base_slug:
        base_slug = uuid.uuid4().hex[:8]

    slug = base_slug
    counter = 1
    while Series.objects.filter(slug=slug).exclude(pk=instance_pk).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def compute_content_hash(content: str) -> str:
    """Compute SHA256 hash of content for deduplication."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def series_cover_upload_path(instance: "Series", filename: str) -> str:
    """Upload path: media/series_covers/<slug>/<filename>"""
    return f"series_covers/{instance.slug or 'tmp'}/{filename}"


def project_cover_upload_path(instance: "Post", filename: str) -> str:
    """Upload path: media/project_covers/<slug>/<filename>."""
    return f"project_covers/{instance.slug or 'tmp'}/{Path(filename).name}"


def project_media_upload_path(instance: "ProjectMedia", filename: str) -> str:
    """Keep every uploaded asset below its owning project's media directory."""
    slug = instance.project.slug if instance.project_id else "unassigned"
    return f"projects/{slug}/{uuid.uuid4().hex[:10]}-{Path(filename).name}"


validate_http_url = URLValidator(
    schemes=("http", "https"),
    message="仅允许有效的 HTTP/HTTPS 地址。",
)


class Series(models.Model):
    """
    独立系列/合集模型 — 通过 Post.series ForeignKey 关联文章。
    """

    title = models.CharField("系列标题", max_length=255, help_text="系列的显示名称")
    slug = models.SlugField("URL 别名", unique=True, help_text="用于生成系列链接")
    description = models.TextField("系列简介", blank=True, help_text="系列的详细介绍")
    cover_image = models.ImageField(
        "封面图片",
        upload_to=series_cover_upload_path,
        blank=True,
        help_text="上传封面图片（支持文件选择或粘贴上传）",
    )
    order = models.PositiveIntegerField(
        "排序权重", default=0,
        help_text="数字越小越靠前，用于首页展示顺序",
    )
    is_featured = models.BooleanField(
        "首页推荐", default=False,
        help_text="是否在首页显示",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["order", "-created_at"]
        verbose_name = "系列"
        verbose_name_plural = "系列管理"

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_series_slug(self.title, instance_pk=self.pk)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        from django.urls import reverse
        return reverse("posts:series_detail", args=[self.slug])

    def get_posts(self):
        """Return published posts in this series, oldest first."""
        return (
            self.posts
            .filter(published=True)
            .exclude(category="projects")
            .order_by("date")
        )

    @property
    def post_count(self) -> int:
        return self.get_posts().count()

    @property
    def latest_post_date(self):
        latest = self.posts.filter(published=True).order_by("-date").first()
        return latest.date if latest else None


class Post(models.Model):
    CATEGORY_CHOICES = (
        ("engineering", "Engineering"),
        ("research", "Research"),
        ("notes", "Notes"),
        ("projects", "Projects"),
    )

    title = models.CharField("标题", max_length=255, help_text="文章标题")
    slug = models.SlugField("URL 别名", unique=True, help_text="用于生成文章链接")
    description = models.TextField("摘要", blank=True, help_text="文章简介")
    content = models.TextField("正文", help_text="Markdown 格式")
    content_hash = models.CharField(
        "内容哈希", max_length=64, blank=True, db_index=True,
        help_text="用于去重检测",
    )
    date = models.DateTimeField("发布日期")
    tags = TaggableManager(
        "标签", blank=True,
        help_text="多个标签请用英文逗号「,」分隔；单个标签内可以包含空格。示例：deep learning, pytorch",
    )
    category = models.CharField("分类", max_length=32, choices=CATEGORY_CHOICES)
    published = models.BooleanField("已发布", default=True)

    PROJECT_STATUS_CHOICES = (
        ("active", "进行中"),
        ("completed", "已完成"),
        ("archived", "已归档"),
    )
    project_status = models.CharField(
        "项目状态",
        max_length=16,
        choices=PROJECT_STATUS_CHOICES,
        default="active",
        help_text="仅用于 Projects 分类页面。",
    )
    project_cover = models.ImageField(
        "项目封面",
        upload_to=project_cover_upload_path,
        blank=True,
        help_text="项目页顶部展示图，可选。",
    )
    github_url = models.URLField("GitHub 地址", blank=True, validators=[validate_http_url])
    demo_url = models.URLField("在线 Demo 地址", blank=True, validators=[validate_http_url])
    paper_url = models.URLField("论文地址", blank=True, validators=[validate_http_url])
    project_tech_stack = models.CharField(
        "技术栈",
        max_length=500,
        blank=True,
        help_text="用英文逗号分隔，例如 Python, PyTorch, Django。",
    )
    project_featured = models.BooleanField(
        "项目精选",
        default=False,
        help_text="是否在 Projects 列表中优先展示。",
    )
    PROJECT_CONTENT_MODE_CHOICES = (
        ("structured", "结构化论文式项目页"),
        ("html", "安全清理的 HTML 项目页"),
    )
    project_content_mode = models.CharField(
        "项目正文模式", max_length=16, choices=PROJECT_CONTENT_MODE_CHOICES,
        default="structured", help_text="HTML 模式仅接受后台安全导入的内容。",
    )
    project_method = models.TextField(
        "Method", blank=True,
        help_text="研究方法；支持 Markdown 与数学公式。",
    )
    project_experiments = models.TextField(
        "Experiments", blank=True,
        help_text="实验设计、数据集与评估说明；具体媒体在下方按实验组组织。",
    )
    project_conclusion = models.TextField(
        "Conclusion", blank=True,
        help_text="结论、局限与后续工作；支持 Markdown。",
    )

    series = models.ForeignKey(
        Series,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posts",
        verbose_name="所属系列",
        help_text="文章所属的系列/合集",
    )
    series_order = models.PositiveIntegerField(
        "系列内排序", null=True, blank=True,
        help_text="在系列中的顺序，数字越小越靠前",
    )

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-date"]
        verbose_name = "文章"
        verbose_name_plural = "文章管理"
        indexes = [
            models.Index(
                fields=["published", "date"],
                name="posts_post_pub_date_idx",
            ),
            models.Index(
                fields=["category", "published", "date"],
                name="posts_post_cat_pub_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["series", "series_order"],
                name="unique_series_order",
                condition=models.Q(series__isnull=False, series_order__isnull=False),
            )
        ]

    def __str__(self) -> str:
        if self.series and self.series_order is not None:
            return f"[{self.series.title} #{self.series_order}] {self.title}"
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(self.title, instance_pk=self.pk)
        if self.content:
            self.content_hash = compute_content_hash(self.content)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        from django.urls import reverse
        if self.category == "projects":
            return reverse("posts:project_detail", args=[self.slug])
        return reverse("posts:post_detail", args=[self.slug])

    @property
    def project_tech_stack_list(self) -> list[str]:
        return [item.strip() for item in self.project_tech_stack.split(",") if item.strip()]

    def clean(self):
        super().clean()
        if self.category == "projects":
            for field_name in ("github_url", "demo_url", "paper_url"):
                value = getattr(self, field_name, "")
                if value:
                    validate_http_url(value)
        try:
            cover_size = self.project_cover.size if self.project_cover else None
        except (FileNotFoundError, ValueError, OSError):
            cover_size = None
        if cover_size is not None:
            max_size = int(getattr(settings, "UPLOAD_MAX_FILE_SIZE", 10 * 1024 * 1024))
            if cover_size > max_size:
                raise ValidationError({"project_cover": "封面图片不能超过 10MB。"})

    def get_series_prev(self) -> "Post | None":
        if not self.series:
            return None
        return (
            Post.objects.filter(
                series=self.series,
                date__lt=self.date,
                published=True,
            )
            .order_by("-date")
            .first()
        )

    def get_series_next(self) -> "Post | None":
        if not self.series:
            return None
        return (
            Post.objects.filter(
                series=self.series,
                date__gt=self.date,
                published=True,
            )
            .order_by("date")
            .first()
        )


class ProjectManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(category="projects")


class Project(Post):
    """A proxy that gives projects a distinct admin workflow and permissions."""

    objects = ProjectManager()

    class Meta:
        proxy = True
        verbose_name = "项目"
        verbose_name_plural = "项目管理"

    def save(self, *args, **kwargs):
        self.category = "projects"
        super().save(*args, **kwargs)


class ProjectMedia(models.Model):
    KIND_IMAGE = "image"
    KIND_VIDEO = "video"
    KIND_CHOICES = ((KIND_IMAGE, "图片"), (KIND_VIDEO, "视频"))
    SECTION_CHOICES = (
        ("method", "方法流程图"),
        ("framework", "模型框架图"),
        ("results", "实验结果"),
        ("comparison", "对比实验"),
        ("gallery", "其他素材"),
    )

    project = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="project_media",
        verbose_name="所属项目",
    )
    file = models.FileField(
        "媒体文件",
        upload_to=project_media_upload_path,
        validators=[FileExtensionValidator(("png", "jpg", "jpeg", "gif", "webp", "mp4", "webm"))],
        help_text="图片：PNG/JPEG/GIF/WebP；视频：MP4/WebM。",
    )
    width = models.PositiveIntegerField("图片宽度", default=0)
    height = models.PositiveIntegerField("图片高度", default=0)
    file_size = models.PositiveBigIntegerField("文件字节数", default=0)
    kind = models.CharField("媒体类型", max_length=8, choices=KIND_CHOICES)
    section = models.CharField("展示分组", max_length=16, choices=SECTION_CHOICES, default="results")
    title = models.CharField("标题 / 说明", max_length=200)
    description = models.TextField("补充说明", blank=True, max_length=2000)
    group_name = models.CharField(
        "数据集 / 实验组",
        max_length=120,
        blank=True,
        help_text="例如 RT-1 Seen、Bridge Unseen 或 w/ VLM；同组素材一起展示。",
    )
    order = models.PositiveIntegerField("组内排序", default=0, help_text="数字越小越靠前。")
    created_at = models.DateTimeField("上传时间", auto_now_add=True)

    class Meta:
        ordering = ("section", "group_name", "order", "id")
        verbose_name = "项目媒体"
        verbose_name_plural = "实验图片与视频"
        indexes = [
            models.Index(fields=("project", "section", "order"), name="project_media_display_idx"),
        ]

    def __str__(self):
        return f"{self.project.title} · {self.title}"

    @property
    def is_video(self):
        return self.kind == self.KIND_VIDEO

    @property
    def aspect_ratio(self):
        return self.width / self.height if self.width and self.height else None

    @property
    def orientation(self):
        ratio = self.aspect_ratio
        if ratio is None:
            return "standard"
        if ratio >= 2:
            return "wide"
        if ratio < 0.85:
            return "portrait"
        if ratio <= 1.1:
            return "square"
        return "landscape"

    def refresh_file_metadata(self):
        """Read delivery metadata without trusting the uploaded filename."""
        try:
            size = self.file.size
            if size is not None:
                self.file_size = int(size)
        except (FileNotFoundError, ValueError, OSError):
            pass
        if not self.file or self.kind != self.KIND_IMAGE:
            self.width = 0
            self.height = 0
            return
        try:
            if getattr(self.file, "_committed", False):
                # Stored files must be closed again on Windows. Unsaved upload
                # streams stay open because Django still needs to persist them.
                with self.file.open("rb") as stream:
                    with Image.open(stream) as image:
                        self.width, self.height = (int(value) for value in image.size)
            else:
                if hasattr(self.file, "seek"):
                    self.file.seek(0)
                with Image.open(self.file) as image:
                    self.width, self.height = (int(value) for value in image.size)
                if hasattr(self.file, "seek"):
                    self.file.seek(0)
        except (FileNotFoundError, ValueError, OSError):
            # Image validity is enforced by the upload form/parser.
            return

    def save(self, *args, **kwargs):
        self.refresh_file_metadata()
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = list(set(kwargs["update_fields"]) | {"width", "height", "file_size"})
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self.refresh_file_metadata()
        errors = {}
        if self.project_id and self.project.category != "projects":
            errors["project"] = "媒体只能关联 Projects 分类的内容。"
        extension = Path(self.file.name or "").suffix.lower().lstrip(".")
        image_extensions = set(getattr(settings, "UPLOAD_ALLOWED_IMAGE_EXTENSIONS", {"png", "jpg", "jpeg", "gif", "webp"}))
        video_extensions = set(getattr(settings, "UPLOAD_ALLOWED_VIDEO_EXTENSIONS", {"mp4", "webm"}))
        expected = image_extensions if self.kind == self.KIND_IMAGE else video_extensions
        if extension not in expected:
            errors["file"] = "文件扩展名与所选媒体类型不一致。"
        if self.section in {"method", "framework"} and self.kind != self.KIND_IMAGE:
            errors["section"] = "方法图和框架图必须使用图片。"
        try:
            size = self.file.size
        except (FileNotFoundError, ValueError, OSError):
            size = None
        if size is not None:
            setting_name = "UPLOAD_MAX_FILE_SIZE" if self.kind == self.KIND_IMAGE else "UPLOAD_MAX_VIDEO_SIZE"
            default = 10 * 1024 * 1024 if self.kind == self.KIND_IMAGE else 50 * 1024 * 1024
            if size > int(getattr(settings, setting_name, default)):
                errors["file"] = "图片或视频超过允许大小。"
        if errors:
            raise ValidationError(errors)