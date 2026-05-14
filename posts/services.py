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
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import yaml
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
from taggit.utils import parse_tags

from .models import Post, Series, compute_content_hash, generate_series_slug, generate_unique_slug

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ALLOWED_IMAGE_EXTENSIONS: set[str] = getattr(
    settings,
    "UPLOAD_ALLOWED_IMAGE_EXTENSIONS",
    {"png", "jpg", "jpeg", "gif", "webp", "svg"},
)
MAX_FILE_SIZE: int = getattr(settings, "UPLOAD_MAX_FILE_SIZE", 10 * 1024 * 1024)
MAX_ZIP_SIZE: int = getattr(settings, "UPLOAD_MAX_ZIP_SIZE", 50 * 1024 * 1024)

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
        safe_name = decoded_name.replace(" ", "_")
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
def extract_zip_safely(
    zip_file,
    extract_dir: Path,
) -> tuple[Path | None, list[Path], list[str]]:
    """Safely extract a ZIP, returning ``(md_path, image_paths, warnings)``.

    Protects against zip-slip, oversized files, and disallowed extensions.
    """
    md_file: Path | None = None
    images: list[Path] = []
    warnings: list[str] = []

    with zipfile.ZipFile(zip_file, "r") as zf:
        total_size = sum(info.file_size for info in zf.infolist())
        if total_size > MAX_ZIP_SIZE:
            raise ValueError(
                f"ZIP 文件解压后过大: {total_size / 1024 / 1024:.1f}MB "
                f"> {MAX_ZIP_SIZE / 1024 / 1024:.1f}MB"
            )

        for info in zf.infolist():
            if info.is_dir():
                continue
            target_path = extract_dir / info.filename
            if not is_safe_path(extract_dir, target_path):
                warnings.append(f"跳过危险路径: {info.filename}")
                continue
            if info.file_size > MAX_FILE_SIZE:
                warnings.append(f"文件过大已跳过: {info.filename}")
                continue

            ext = Path(info.filename).suffix.lower().lstrip(".")

            if ext == "md":
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


# ---------------------------------------------------------------------------
# Upload file parsing (step 1 — metadata extraction)
# ---------------------------------------------------------------------------
def _extract_zip_into(zip_path: Path, dest: Path, warnings: list[str]) -> tuple[Path | None, list[Path]]:
    """Extract a single zip's md + images into *dest*. Returns (md_path, images)."""
    md_path: Path | None = None
    images: list[Path] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target_path = dest / info.filename
            if not is_safe_path(dest, target_path):
                warnings.append(f"跳过危险路径: {info.filename}")
                continue
            ext = Path(info.filename).suffix.lower().lstrip(".")
            if ext == "md" and md_path is None:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                zf.extract(info, dest)
                md_path = target_path
            elif ext in ALLOWED_IMAGE_EXTENSIONS:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                zf.extract(info, dest)
                images.append(target_path)
    return md_path, images


def _find_inner_zips(zip_path: Path) -> list[str]:
    """Return relative paths of .zip entries inside *zip_path* (Notion double-wrap)."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        return [
            info.filename for info in zf.infolist()
            if not info.is_dir() and info.filename.lower().endswith(".zip")
        ]


def parse_upload_file(uploaded_file) -> dict[str, Any]:
    """Parse an uploaded ``.md`` / ``.zip`` and return preview-ready metadata.

    Automatically unwraps **Notion double-zip exports**: if the outer ZIP contains
    no ``.md`` but contains one or more inner ``.zip`` files, the inner zips are
    extracted in place so the user no longer needs to manually unzip first.
    """
    import tempfile

    filename = uploaded_file.name.lower()
    result: dict[str, Any] = {"warnings": [], "image_count": 0, "md_filename": ""}

    if filename.endswith(".zip"):
        if uploaded_file.size > MAX_ZIP_SIZE:
            raise ValueError(
                f"ZIP 文件过大: {uploaded_file.size / 1024 / 1024:.1f}MB"
            )

        staging_dir = Path(tempfile.mkdtemp(prefix="upload_"))
        zip_path = staging_dir / "upload.zip"
        with open(zip_path, "wb") as f:
            for chunk in uploaded_file.chunks():
                f.write(chunk)

        md_path, images = _extract_zip_into(zip_path, staging_dir, result["warnings"])

        # Notion double-zip auto-unwrap: outer zip contains only inner zip(s).
        # We keep recursing (cap 3 levels) until we find a real .md.
        if md_path is None:
            inner_zips_found = False
            for _depth in range(3):
                inner_zips = _find_inner_zips(zip_path)
                if not inner_zips:
                    break
                inner_zips_found = True
                for inner_name in inner_zips:
                    inner_extract_root = staging_dir / "inner_unwrap"
                    inner_extract_root.mkdir(parents=True, exist_ok=True)
                    inner_target = inner_extract_root / Path(inner_name).name
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        with zf.open(inner_name) as src, open(inner_target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                    md_path, more_imgs = _extract_zip_into(
                        inner_target, staging_dir, result["warnings"]
                    )
                    images.extend(more_imgs)
                    if md_path is not None:
                        break
                if md_path is not None:
                    break
                # If still no md but there are nested zips inside the inner ones,
                # continue with the latest inner zip as the new "zip_path"
                inner_lvl_zips = list((staging_dir / "inner_unwrap").glob("*.zip"))
                if not inner_lvl_zips:
                    break
                zip_path = inner_lvl_zips[0]
            if md_path is not None and inner_zips_found:
                result["warnings"].append(
                    "检测到 Notion 多层嵌套 ZIP，已自动解开内层压缩包"
                )

        if not md_path:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise ValueError("ZIP 文件中未找到 .md 文件")

        text = md_path.read_text(encoding="utf-8")
        result["md_filename"] = md_path.name
        result["image_count"] = len(images)
        result["staging_dir"] = str(staging_dir)

    elif filename.endswith(".md"):
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

    title = meta.get("title") or extract_title_from_markdown(body) or ""
    if not title and result["md_filename"]:
        title = clean_notion_filename(result["md_filename"])

    raw_category = meta.get("category") or ""
    category_map = {"tech": "engineering", "paper": "research"}
    category = category_map.get(raw_category, raw_category)

    tags = meta.get("tags", [])
    tags_str = (
        ", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags or "")
    )

    date_val = meta.get("date")
    date_str = str(date_val) if date_val else ""

    result.update(
        {
            "title": title or "Untitled",
            "slug": str(meta.get("slug") or ""),
            "description": meta.get("description") or "",
            "category": category,
            "tags": tags_str,
            "date": date_str,
            "content": body.strip(),
        }
    )
    return result


# ---------------------------------------------------------------------------
# Post creation / update (step 2 — publish)
# ---------------------------------------------------------------------------
def process_markdown_content(
    text: str,
    images: list[Path],
    temp_dir: Path | None,
    *,
    md_filename: str = "",
    overrides: dict[str, str] | None = None,
) -> tuple[Post, bool, list[str]]:
    """Process markdown content and create/update a Post.

    Returns ``(post, created, warnings)``.
    """
    m = FRONT_MATTER_RE.match(text)
    warnings: list[str] = []
    overrides = overrides or {}

    if m:
        fm_raw, body = m.groups()
        meta = yaml.safe_load(fm_raw) or {}
    else:
        meta = {}
        body = text
        warnings.append("未检测到 YAML front matter，已使用手动填写的元数据")

    title = overrides.get("title") or meta.get("title") or ""
    if not title:
        title = extract_title_from_markdown(body)
    if not title and md_filename:
        title = clean_notion_filename(md_filename)
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
        tags_str = (
            ", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags or "")
        )

    raw_category = overrides.get("category") or meta.get("category") or "engineering"
    category_map = {"tech": "engineering", "paper": "research"}
    category = category_map.get(raw_category, raw_category)
    description = (overrides.get("description") or meta.get("description") or "").strip()

    if not description:
        try:
            description = generate_summary(title, body)
            if description:
                warnings.append("摘要为空，已使用 AI 自动生成约 30 词摘要")
        except Exception as exc:
            warnings.append(f"自动摘要生成失败（已留空）：{exc}")

    raw_slug = overrides.get("slug") or str(meta.get("slug") or "").strip()
    slug = slugify(raw_slug) if raw_slug else generate_unique_slug(title)
    if not slug:
        slug = generate_unique_slug(title)

    content = body.strip()

    if images and temp_dir:
        content, missing = rewrite_image_paths(content, slug, temp_dir, images)
        if missing:
            warnings.append(f"以下图片引用未找到: {', '.join(missing)}")

    content_hash = compute_content_hash(content)
    existing_by_hash = (
        Post.objects.filter(content_hash=content_hash).exclude(slug=slug).first()
    )
    if existing_by_hash:
        warnings.append(f"警告: 发现内容相同的文章「{existing_by_hash.title}」")

    # Handle series — admin form override (id) takes precedence over front matter (name)
    series_instance: Series | None = None
    series_order: int | None = None

    override_series_id = (overrides.get("series_id") or "").strip()
    if override_series_id:
        try:
            series_instance = Series.objects.get(pk=int(override_series_id))
        except (ValueError, Series.DoesNotExist):
            warnings.append(f"指定的系列 ID 无效，已忽略: {override_series_id}")
    else:
        series_name = meta.get("series")
        if series_name:
            series_name = str(series_name).strip()
            series_slug = slugify(series_name)
            if not series_slug:
                series_slug = generate_series_slug(series_name)

            series_instance, series_created = Series.objects.get_or_create(
                slug=series_slug,
                defaults={
                    "title": series_name,
                    "description": f"自动创建的系列：{series_name}",
                },
            )
            if series_created:
                warnings.append(f"已自动创建系列「{series_name}」")

    override_series_order = (overrides.get("series_order") or "").strip()
    if override_series_order:
        try:
            series_order = int(override_series_order)
        except (ValueError, TypeError):
            warnings.append(f"系列内排序无效，已忽略: {override_series_order}")
    elif series_instance is not None:
        series_order_raw = meta.get("series_order")
        if series_order_raw is not None:
            try:
                series_order = int(series_order_raw)
            except (ValueError, TypeError):
                warnings.append(f"series_order 值无效，已忽略: {series_order_raw}")

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
        with urllib.request.urlopen(req, timeout=20) as resp:
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
