"""
Business-logic helpers extracted from views.py / admin.py.

- parse_upload_file():  Parse an uploaded .md or .zip, return structured metadata.
- process_markdown_content():  Create / update a Post from parsed data.
- rewrite_image_paths():  Copy images to MEDIA_ROOT and fix Markdown references.
- generate_summary():  Ask DeepSeek for a ~30-word English summary when description is empty.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import yaml
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify
from taggit.utils import parse_tags

from .models import Post, Series, compute_content_hash, generate_series_slug, generate_unique_slug, validate_http_url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ALLOWED_IMAGE_EXTENSIONS: set[str] = getattr(
    settings,
    "UPLOAD_ALLOWED_IMAGE_EXTENSIONS",
    {"png", "jpg", "jpeg", "gif", "webp", "svg"},
)
ALLOWED_VIDEO_EXTENSIONS: set[str] = getattr(
    settings, "UPLOAD_ALLOWED_VIDEO_EXTENSIONS", {"mp4", "webm"},
)
MAX_FILE_SIZE: int = getattr(settings, "UPLOAD_MAX_FILE_SIZE", 10 * 1024 * 1024)
MAX_VIDEO_SIZE: int = getattr(settings, "UPLOAD_MAX_VIDEO_SIZE", 50 * 1024 * 1024)
MAX_ZIP_SIZE: int = getattr(settings, "UPLOAD_MAX_ZIP_SIZE", 50 * 1024 * 1024)
MAX_ZIP_UNCOMPRESSED_SIZE: int = getattr(settings, "UPLOAD_MAX_ZIP_UNCOMPRESSED_SIZE", 150 * 1024 * 1024)
MAX_ZIP_ENTRIES: int = getattr(settings, "UPLOAD_MAX_ZIP_ENTRIES", 200)
MAX_NESTED_ARCHIVES: int = getattr(settings, "UPLOAD_MAX_NESTED_ARCHIVES", 8)
AI_REQUEST_TIMEOUT: float = max(
    1.0,
    min(float(getattr(settings, "AI_REQUEST_TIMEOUT", 8)), 20.0),
)

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
IMG_PATTERN = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)')


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------
def is_safe_path(base_path: Path, target_path: Path) -> bool:
    """Guard against zip-slip: ensure *target_path* stays inside *base_path*."""
    try:
        target_path.resolve().relative_to(base_path.resolve())
        return True
    except ValueError:
        return False


def extract_title_from_markdown(text: str) -> str:
    """Return the first ``# heading`` found in *text*, or ``""``."""
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            return line.lstrip("# ").strip()
    return ""


def clean_notion_filename(filename: str) -> str:
    """Strip Notion's 32-char hex UUID suffix from export filenames."""
    stem = Path(filename).stem
    cleaned = re.sub(r"\s+[0-9a-f]{32}$", "", stem)
    return cleaned.strip() if cleaned.strip() else stem


# ---------------------------------------------------------------------------
# Image path rewriting
# ---------------------------------------------------------------------------
def rewrite_image_paths(
    content: str,
    slug: str,
    temp_dir: Path,
    images: list[Path],
    copied_files: list[Path] | None = None,
) -> tuple[str, list[str]]:
    """Copy *images* into ``MEDIA_ROOT/posts/<slug>/`` and rewrite Markdown refs.

    Handles URL-encoded filenames typical of Notion exports.
    Returns ``(new_content, missing_refs)``.
    """
    media_dir = Path(settings.MEDIA_ROOT) / "posts" / slug
    media_dir.mkdir(parents=True, exist_ok=True)

    image_map: dict[str, str] = {}
    for img_path in images:
        try:
            rel_path = img_path.relative_to(temp_dir)
        except ValueError:
            rel_path = Path(img_path.name)

        decoded_name = unquote(img_path.name)
        # URL-encoded ZIP names may decode to path separators. Only a basename
        # may ever be used below the per-post media directory.
        decoded_basename = Path(decoded_name.replace("\\", "/")).name
        safe_name = decoded_basename.replace(" ", "_") or "image.png"
        base_stem = Path(safe_name).stem
        base_suffix = Path(safe_name).suffix
        target_path = media_dir / safe_name
        counter = 1
        while target_path.exists():
            safe_name = f"{base_stem}_{counter}{base_suffix}"
            target_path = media_dir / safe_name
            counter += 1

        if copied_files is not None:
            # Track the destination before copying so a partial write is also
            # removed if validation, storage, or the subsequent DB save fails.
            copied_files.append(target_path)
        shutil.copy2(img_path, target_path)
        media_url = f"{settings.MEDIA_URL}posts/{slug}/{quote(safe_name)}"

        name = img_path.name
        rel_str = str(rel_path).replace("\\", "/")
        name_encoded = quote(name)
        decoded_encoded = quote(decoded_name)

        for ref in [
            name, decoded_name,
            f"./{name}", f"./{decoded_name}",
            f"assets/{name}", f"assets/{decoded_name}",
            f"./assets/{name}", f"./assets/{decoded_name}",
            f"images/{name}", f"./images/{name}",
            name_encoded, decoded_encoded,
            f"./{name_encoded}", f"./{decoded_encoded}",
            rel_str, quote(rel_str),
            str(rel_path), str(rel_path).replace("\\", "/"),
        ]:
            image_map[ref] = media_url

    missing: list[str] = []

    def _replace(match: re.Match) -> str:
        alt, path = match.group(1), match.group(2)
        if path in image_map:
            return f"![{alt}]({image_map[path]})"
        decoded = unquote(path)
        if decoded in image_map:
            return f"![{alt}]({image_map[decoded]})"
        double_decoded = unquote(decoded)
        if double_decoded in image_map:
            return f"![{alt}]({image_map[double_decoded]})"
        normalized = path.lstrip("./").replace("\\", "/")
        decoded_norm = unquote(normalized)
        for orig, url in image_map.items():
            orig_decoded = unquote(orig)
            if decoded_norm == orig_decoded.lstrip("./").replace("\\", "/"):
                return f"![{alt}]({url})"
            if decoded_norm.endswith(Path(orig_decoded).name):
                return f"![{alt}]({url})"
        missing.append(path)
        return match.group(0)

    return IMG_PATTERN.sub(_replace, content), missing


# ---------------------------------------------------------------------------
# ZIP extraction
# ---------------------------------------------------------------------------
@dataclass
class ZipBudget:
    remaining_bytes: int = field(default_factory=lambda: MAX_ZIP_UNCOMPRESSED_SIZE)
    remaining_entries: int = field(default_factory=lambda: MAX_ZIP_ENTRIES)
    nested_archives: int = 0


def _consume_zip_budget(zf: zipfile.ZipFile, budget: ZipBudget) -> None:
    infos = zf.infolist()
    total_size = sum(max(0, info.file_size) for info in infos)
    if len(infos) > budget.remaining_entries:
        raise ValueError(f"ZIP 条目过多，最多允许 {MAX_ZIP_ENTRIES} 个（含嵌套包）")
    if total_size > budget.remaining_bytes:
        raise ValueError("ZIP 嵌套解压后的累计大小超过限制")
    budget.remaining_entries -= len(infos)
    budget.remaining_bytes -= total_size


def _extract_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path) -> None:
    mode = (info.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        raise ValueError(f"ZIP 不支持符号链接: {info.filename}")
    if info.flag_bits & 0x1:
        raise ValueError(f"ZIP 不支持加密文件: {info.filename}")
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with zf.open(info, "r") as source, target.open("xb") as destination:
        while chunk := source.read(1024 * 1024):
            written += len(chunk)
            if written > info.file_size or written > MAX_VIDEO_SIZE:
                raise ValueError(f"ZIP 文件实际大小异常: {info.filename}")
            destination.write(chunk)


def extract_zip_safely(
    zip_file,
    extract_dir: Path,
    *,
    allow_videos: bool = False,
) -> tuple[Path | None, list[Path], list[str]]:
    """Extract one Markdown file and allow-listed media with a global ZIP budget."""
    md_file: Path | None = None
    images: list[Path] = []
    warnings: list[str] = []
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_file, "r") as zf:
        _consume_zip_budget(zf, ZipBudget())
        for info in zf.infolist():
            if info.is_dir():
                continue
            target_path = extract_dir / info.filename
            if not is_safe_path(extract_dir, target_path):
                warnings.append(f"跳过危险路径: {info.filename}")
                continue
            ext = Path(info.filename).suffix.lower().lstrip(".")
            allowed_media = ALLOWED_IMAGE_EXTENSIONS | (ALLOWED_VIDEO_EXTENSIONS if allow_videos else set())
            if ext not in {"md", *allowed_media}:
                warnings.append(f"不支持的文件类型已跳过: {info.filename}")
                continue
            limit = MAX_VIDEO_SIZE if ext in ALLOWED_VIDEO_EXTENSIONS else MAX_FILE_SIZE
            if info.file_size > limit:
                warnings.append(f"文件过大已跳过: {info.filename}")
                continue
            if ext == "md" and md_file is not None:
                warnings.append(f"多个 MD 文件，仅使用第一个: {info.filename}")
                continue
            _extract_member(zf, info, target_path)
            if ext == "md":
                md_file = target_path
            else:
                images.append(target_path)
    return md_file, images, warnings


# ---------------------------------------------------------------------------
# Upload file parsing (step 1 — metadata extraction)
# ---------------------------------------------------------------------------
def _extract_zip_into(
    zip_path: Path,
    dest: Path,
    warnings: list[str],
    *,
    allow_videos: bool,
    budget: ZipBudget,
) -> tuple[Path | None, list[Path]]:
    """Extract one archive while sharing entry/byte limits across nested ZIPs."""
    md_path: Path | None = None
    media: list[Path] = []
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        _consume_zip_budget(zf, budget)
        for info in zf.infolist():
            if info.is_dir():
                continue
            target_path = dest / info.filename
            if not is_safe_path(dest, target_path):
                warnings.append(f"跳过危险路径: {info.filename}")
                continue
            ext = Path(info.filename).suffix.lower().lstrip(".")
            allowed_media = ALLOWED_IMAGE_EXTENSIONS | (ALLOWED_VIDEO_EXTENSIONS if allow_videos else set())
            if ext not in {"md", *allowed_media}:
                if ext != "zip":
                    warnings.append(f"不支持的文件类型已跳过: {info.filename}")
                continue
            limit = MAX_VIDEO_SIZE if ext in ALLOWED_VIDEO_EXTENSIONS else MAX_FILE_SIZE
            if info.file_size > limit:
                warnings.append(f"文件过大已跳过: {info.filename}")
                continue
            if ext == "md" and md_path is not None:
                warnings.append(f"多个 MD 文件，仅使用第一个: {info.filename}")
                continue
            _extract_member(zf, info, target_path)
            if ext == "md":
                md_path = target_path
            else:
                media.append(target_path)
    return md_path, media


def _find_inner_zips(zip_path: Path) -> list[str]:
    """Return relative paths of .zip entries inside *zip_path* (Notion double-wrap)."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        return [
            info.filename for info in zf.infolist()
            if not info.is_dir() and info.filename.lower().endswith(".zip")
        ]


def parse_upload_file(uploaded_file, *, allow_videos: bool = False) -> dict[str, Any]:
    """Stage Markdown/ZIP with shared nested-archive limits and safe asset paths."""
    import tempfile

    filename = uploaded_file.name.lower()
    result: dict[str, Any] = {"warnings": [], "image_count": 0, "md_filename": "", "assets": []}
    staging_dir: Path | None = None
    media: list[Path] = []

    try:
        if filename.endswith(".zip"):
            if uploaded_file.size > MAX_ZIP_SIZE:
                raise ValueError(f"ZIP 文件过大，最多 {MAX_ZIP_SIZE // 1024 // 1024}MB")
            staging_dir = Path(tempfile.mkdtemp(prefix="upload_"))
            source_dir = staging_dir / "_source"
            source_dir.mkdir()
            outer_zip = source_dir / "upload.zip"
            with outer_zip.open("xb") as stream:
                for chunk in uploaded_file.chunks():
                    stream.write(chunk)

            budget = ZipBudget()
            queue: list[tuple[Path, int]] = [(outer_zip, 0)]
            md_path = None
            nested_found = False
            while queue and md_path is None:
                zip_path, depth = queue.pop(0)
                dest = staging_dir / ("content" if depth == 0 else f"content-{uuid.uuid4().hex[:8]}")
                md_path, more_media = _extract_zip_into(
                    zip_path, dest, result["warnings"], allow_videos=allow_videos, budget=budget,
                )
                media.extend(more_media)
                if md_path is None and depth < 3:
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        inner_infos = [info for info in zf.infolist() if not info.is_dir() and info.filename.lower().endswith(".zip")]
                        for info in inner_infos:
                            if budget.nested_archives >= MAX_NESTED_ARCHIVES:
                                result["warnings"].append("嵌套 ZIP 数量超过限制，剩余压缩包未展开")
                                break
                            if info.file_size > MAX_ZIP_SIZE:
                                result["warnings"].append(f"嵌套 ZIP 过大已跳过: {info.filename}")
                                continue
                            nested_dir = staging_dir / "_nested"
                            nested_dir.mkdir(exist_ok=True)
                            inner_target = nested_dir / f"{uuid.uuid4().hex[:8]}-{Path(info.filename).name}"
                            with zf.open(info) as src, inner_target.open("xb") as dst:
                                remaining = MAX_ZIP_SIZE
                                while chunk := src.read(1024 * 1024):
                                    remaining -= len(chunk)
                                    if remaining < 0:
                                        raise ValueError(f"嵌套 ZIP 实际大小超过限制: {info.filename}")
                                    dst.write(chunk)
                            budget.nested_archives += 1
                            nested_found = True
                            queue.append((inner_target, depth + 1))

            if not md_path:
                raise ValueError("ZIP 文件中未找到 .md 文件")
            if nested_found:
                result["warnings"].append("检测到 Notion 多层嵌套 ZIP，已自动解开内层压缩包")
            text = md_path.read_text(encoding="utf-8")
            result["md_filename"] = md_path.name

        elif filename.endswith(".md"):
            if uploaded_file.size > MAX_FILE_SIZE:
                raise ValueError("Markdown 文件不能超过 10MB")
            text = uploaded_file.read().decode("utf-8")
            result["md_filename"] = Path(uploaded_file.name).name
        else:
            raise ValueError("不支持的文件格式，请上传 .md 或 .zip 文件")

        result["staging_dir"] = str(staging_dir) if staging_dir else ""
        if staging_dir:
            for asset in media:
                extension = asset.suffix.lower().lstrip(".")
                kind = "video" if extension in ALLOWED_VIDEO_EXTENSIONS else "image"
                result["assets"].append({
                    "path": asset.relative_to(staging_dir).as_posix(),
                    "filename": asset.name,
                    "kind": kind,
                })
            result["image_count"] = sum(a["kind"] == "image" for a in result["assets"])

        m = FRONT_MATTER_RE.match(text)
        if m:
            fm_raw, body = m.groups()
            meta = yaml.safe_load(fm_raw) or {}
            if not isinstance(meta, dict):
                raise ValueError("YAML front matter 必须是键值对")
            result["has_frontmatter"] = True
        else:
            meta = {}
            body = text
            result["has_frontmatter"] = False
            result["warnings"].append("未检测到 YAML front matter")

        title = meta.get("title") or extract_title_from_markdown(body) or ""
        if not title and result["md_filename"]:
            title = clean_notion_filename(result["md_filename"])
        raw_category = str(meta.get("category") or "")
        category = {"tech": "engineering", "paper": "research"}.get(raw_category, raw_category)
        tags = meta.get("tags", [])
        tags_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags or "")
        result.update({
            "title": str(title or "Untitled"),
            "slug": str(meta.get("slug") or ""),
            "description": str(meta.get("description") or ""),
            "category": category,
            "tags": tags_str,
            "date": str(meta.get("date") or ""),
            "content": body.strip(),
            "project_status": str(meta.get("project_status") or "active"),
            "project_tech_stack": str(meta.get("project_tech_stack") or ""),
            "project_method": str(meta.get("project_method") or ""),
            "project_experiments": str(meta.get("project_experiments") or ""),
            "project_conclusion": str(meta.get("project_conclusion") or ""),
            "github_url": str(meta.get("github_url") or ""),
            "demo_url": str(meta.get("demo_url") or ""),
            "paper_url": str(meta.get("paper_url") or ""),
            "project_featured": str(meta.get("project_featured", "")).lower() in {"1", "true", "yes", "on"},
        })
        return result
    except Exception:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise


# ---------------------------------------------------------------------------
# Post creation / update (step 2 — publish)
# ---------------------------------------------------------------------------
class ContentProcessError(ValueError):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


def process_markdown_content(
    text: str,
    images: list[Path],
    temp_dir: Path | None,
    *,
    md_filename: str = "",
    overrides: dict[str, Any] | None = None,
    update_existing: bool = False,
    copied_files: list[Path] | None = None,
) -> tuple[Post, bool, list[str]]:
    """Validate and save content without silently overwriting an existing slug.

    ``update_existing`` is explicit for import tools. Custom create workflows leave
    it false. File copies are tracked and removed if validation or the DB fails.
    """
    warnings: list[str] = []
    overrides = overrides or {}
    copied = copied_files if copied_files is not None else []
    copied_start = len(copied)

    def value(key, fallback=None):
        return overrides[key] if key in overrides else fallback

    try:
        match = FRONT_MATTER_RE.match(text)
        if match:
            fm_raw, body = match.groups()
            meta = yaml.safe_load(fm_raw) or {}
            if not isinstance(meta, dict):
                raise ContentProcessError("YAML front matter 必须是键值对", "content")
        else:
            meta = {}
            body = text
            warnings.append("未检测到 YAML front matter，已使用手动填写的元数据")

        title = str(value("title", meta.get("title")) or "").strip()
        if not title:
            title = extract_title_from_markdown(body)
        if not title and md_filename:
            title = clean_notion_filename(md_filename)
        if not title:
            raise ContentProcessError("标题不能为空", "title")

        raw_date = value("date", meta.get("date"))
        if isinstance(raw_date, datetime):
            date = raw_date
        elif raw_date:
            try:
                date = datetime.fromisoformat(str(raw_date))
            except (TypeError, ValueError) as exc:
                raise ContentProcessError("发布日期格式无效", "date") from exc
        else:
            date = timezone.now()
        if timezone.is_naive(date):
            date = timezone.make_aware(date)

        tags_value = value("tags", meta.get("tags", []))
        if isinstance(tags_value, list):
            tags_str = ", ".join(str(tag) for tag in tags_value)
        else:
            tags_str = str(tags_value or "").strip()
        raw_category = str(value("category", meta.get("category")) or "engineering")
        category = {"tech": "engineering", "paper": "research"}.get(raw_category, raw_category)
        if category not in dict(Post.CATEGORY_CHOICES):
            raise ContentProcessError("分类无效", "category")
        description = str(value("description", meta.get("description")) or "").strip()
        published = bool(value("published", True))

        project_values = {}
        if category == "projects":
            project_status = str(value("project_status", meta.get("project_status")) or "active")
            if project_status not in dict(Post.PROJECT_STATUS_CHOICES):
                raise ContentProcessError("项目状态无效", "project_status")
            project_values["project_status"] = project_status
            content_mode = str(value("project_content_mode", meta.get("project_content_mode")) or "structured")
            if content_mode not in dict(Post.PROJECT_CONTENT_MODE_CHOICES):
                raise ContentProcessError("项目正文模式无效", "content")
            project_values["project_content_mode"] = content_mode
            project_values["project_tech_stack"] = str(value("project_tech_stack", meta.get("project_tech_stack")) or "").strip()
            for field in ("project_method", "project_experiments", "project_conclusion"):
                project_values[field] = str(value(field, meta.get(field)) or "").strip()
            for field in ("github_url", "demo_url", "paper_url"):
                url = str(value(field, meta.get(field)) or "").strip()
                if url:
                    try:
                        validate_http_url(url)
                    except ValidationError as exc:
                        raise ContentProcessError("仅允许有效的 HTTP/HTTPS 地址", field) from exc
                    if field == "paper_url" and (url.rstrip("/") in {"https://arxiv.org", "http://arxiv.org"} or "your-paper-id" in url):
                        raise ContentProcessError("请填写具体论文地址，或留空", field)
                project_values[field] = url
            featured_value = value("project_featured", meta.get("project_featured", False))
            project_values["project_featured"] = featured_value if isinstance(featured_value, bool) else str(featured_value).lower() in {"1", "true", "yes", "on"}

        raw_slug = str(value("slug", meta.get("slug")) or "").strip()
        slug = slugify(raw_slug) if raw_slug else generate_unique_slug(title)
        if not slug:
            raise ContentProcessError("slug 需包含英文字母、数字或连字符", "slug")
        existing = Post.objects.filter(slug=slug).first()
        if existing and not update_existing:
            raise ContentProcessError(f"slug「{slug}」已被「{existing.title}」使用，请修改后再保存；不会覆盖已有内容", "slug")
        if existing and update_existing:
            was_project = existing.category == "projects"
            if was_project != (category == "projects"):
                raise ContentProcessError("文章与项目不能在导入更新时互相转换，请使用对应的新建流程。", "category")

        content = body.strip()
        if not content:
            raise ContentProcessError("正文不能为空", "content")
        if images and temp_dir:
            content, missing = rewrite_image_paths(content, slug, temp_dir, images, copied)
            if missing:
                warnings.append(f"以下图片引用未找到: {', '.join(missing)}")

        existing_by_hash = Post.objects.filter(content_hash=compute_content_hash(content)).exclude(slug=slug).first()
        if existing_by_hash:
            warnings.append(f"警告: 发现内容相同的文章「{existing_by_hash.title}」")

        series_instance = None
        series_order = value("series_order", None)
        if "series" in overrides:
            series_instance = overrides["series"]
        elif "series_id" in overrides:
            series_id = str(overrides["series_id"] or "").strip()
            if series_id:
                try:
                    series_instance = Series.objects.get(pk=int(series_id))
                except (ValueError, Series.DoesNotExist) as exc:
                    raise ContentProcessError("所选系列不存在", "series_id") from exc
        elif meta.get("series"):
            series_name = str(meta["series"]).strip()
            series_slug = slugify(series_name) or generate_series_slug(series_name)
            series_instance, series_created = Series.objects.get_or_create(
                slug=series_slug,
                defaults={"title": series_name, "description": f"自动创建的系列：{series_name}"},
            )
            if series_created:
                warnings.append(f"已自动创建系列「{series_name}」")
        if series_order in ("", None) and series_instance is not None and "series_order" not in overrides:
            series_order = meta.get("series_order")
        if series_order in ("", None):
            series_order = None
        else:
            try:
                series_order = int(series_order)
                if series_order < 1:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ContentProcessError("系列内排序必须是大于 0 的整数", "series_order") from exc

        with transaction.atomic():
            post = existing or Post(slug=slug)
            created = existing is None
            common_values = {
                "title": title, "description": description, "content": content,
                "date": date, "category": category, "published": published,
                "series": series_instance, "series_order": series_order,
            }
            for field, field_value in common_values.items():
                setattr(post, field, field_value)
            # Non-project imports never erase project metadata during an explicit update.
            if category == "projects":
                for field, field_value in project_values.items():
                    setattr(post, field, field_value)
            try:
                post.full_clean()
                post.save()
                post.tags.set(parse_tags(tags_str))
            except ValidationError as exc:
                field = next(iter(exc.error_dict), None) if hasattr(exc, "error_dict") else None
                messages = exc.message_dict.get(field, []) if field else exc.messages
                raise ContentProcessError("；".join(messages) or "内容校验失败", field) from exc
            except IntegrityError as exc:
                field = "series_order" if "unique_series_order" in str(exc) else "slug"
                message = "该系列内排序已被占用" if field == "series_order" else "slug 已存在，请换一个"
                raise ContentProcessError(message, field) from exc
        return post, created, warnings
    except Exception:
        for path in copied[copied_start:]:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not clean copied upload file: %s", path)
        del copied[copied_start:]
        raise


# ---------------------------------------------------------------------------
# AI-assisted summary (DeepSeek, OpenAI-compatible API)
# ---------------------------------------------------------------------------
def _strip_markdown(text: str, max_chars: int = 6000) -> str:
    """Quick best-effort plaintext extraction, capped at *max_chars*."""
    t = re.sub(r"```[\s\S]*?```", " ", text)            # fenced code
    t = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", t)         # images
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)      # links
    t = re.sub(r"`[^`]+`", " ", t)                       # inline code
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE) # heading markers
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.MULTILINE)  # bullets
    t = re.sub(r"[*_>#~]+", "", t)
    t = re.sub(r"\n{2,}", "\n\n", t).strip()
    if len(t) > max_chars:
        t = t[:max_chars]
    return t


def generate_summary(title: str, body: str, *, max_words: int = 30) -> str:
    """Generate ~``max_words`` English summary via DeepSeek chat API.

    Returns empty string if the API key is missing or the request fails.
    Honors ``DEEPSEEK_API_KEY`` / optional ``DEEPSEEK_API_BASE`` / ``DEEPSEEK_MODEL``.
    """
    api_key = (
        getattr(settings, "DEEPSEEK_API_KEY", None)
        or os.environ.get("DEEPSEEK_API_KEY")
        or ""
    ).strip()
    if not api_key:
        return ""

    api_base = (
        getattr(settings, "DEEPSEEK_API_BASE", None)
        or os.environ.get("DEEPSEEK_API_BASE")
        or "https://api.deepseek.com/v1"
    ).rstrip("/")
    model = (
        getattr(settings, "DEEPSEEK_MODEL", None)
        or os.environ.get("DEEPSEEK_MODEL")
        or "deepseek-chat"
    )

    plain = _strip_markdown(body)
    if not plain.strip():
        return ""

    system_prompt = (
        "You write concise, neutral English summaries for technical blog posts. "
        f"Reply with ONE single sentence of about {max_words} words. "
        "No quotes, no markdown, no leading 'This article' / 'In this post'. "
        "Just the summary itself."
    )
    user_prompt = f"Title: {title}\n\nContent:\n{plain}\n\nWrite the summary now."

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 160,
        "stream": False,
    }

    req = urllib.request.Request(
        f"{api_base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=AI_REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("DeepSeek summary call failed: %s", exc)
        return ""

    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""
    # tidy up: drop wrapping quotes / trailing whitespace
    text = text.strip().strip('"').strip("'").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def generate_tags(title: str, body: str, *, count: int = 3) -> list[str]:
    """Generate ~``count`` keyword tags via DeepSeek chat API.

    Returns a list of short, lowercase, hyphen-friendly tag strings. Empty list
    if the API key is missing, the request fails, or the model returns nothing
    usable. Honors the same env vars as :func:`generate_summary`.
    """
    api_key = (
        getattr(settings, "DEEPSEEK_API_KEY", None)
        or os.environ.get("DEEPSEEK_API_KEY")
        or ""
    ).strip()
    if not api_key:
        return []

    api_base = (
        getattr(settings, "DEEPSEEK_API_BASE", None)
        or os.environ.get("DEEPSEEK_API_BASE")
        or "https://api.deepseek.com/v1"
    ).rstrip("/")
    model = (
        getattr(settings, "DEEPSEEK_MODEL", None)
        or os.environ.get("DEEPSEEK_MODEL")
        or "deepseek-chat"
    )

    plain = _strip_markdown(body)
    if not plain.strip():
        return []

    system_prompt = (
        "You extract concise topical tags for technical blog posts. "
        f"Return EXACTLY {count} tags as a comma-separated list, no numbering, "
        "no explanations, no quotes. Each tag must be 1-3 lowercase words "
        "(letters, digits, hyphens or single spaces only — no punctuation). "
        "Prefer well-known technical keywords readers might search for."
    )
    user_prompt = (
        f"Title: {title}\n\nContent:\n{plain}\n\n"
        f"Output exactly {count} comma-separated tags now."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 60,
        "stream": False,
    }

    req = urllib.request.Request(
        f"{api_base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=AI_REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("DeepSeek tag call failed: %s", exc)
        return []

    try:
        raw = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return []

    parts = re.split(r"[,\n;]+", raw)
    tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in parts:
        # Strip enumerators like "1.", "1)", "-", surrounding quotes/brackets.
        t = re.sub(r"^\s*(?:\d+[\.\)]\s*|[-*•·]\s*)", "", raw_tag).strip()
        t = t.strip(' "\'`*_[](){}<>—–')
        t = re.sub(r"\s+", " ", t).lower()
        # Allow only letters, digits, single hyphens/spaces.
        t = re.sub(r"[^a-z0-9\u4e00-\u9fff\- ]+", "", t).strip()
        if not t or len(t) > 32:
            continue
        if t in seen:
            continue
        seen.add(t)
        tags.append(t)
        if len(tags) >= count:
            break
    return tags
