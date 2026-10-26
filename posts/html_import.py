"""Secure HTML project ZIP imports.

The parser stages files without creating model instances.  Callers own the
returned ``staging_dir`` and must remove it when the workflow finishes or is
abandoned.  ``sanitize_project_html`` is deliberately independent of the ZIP
parser so it can also be applied immediately before public rendering.
"""

from __future__ import annotations

import html as html_lib
import posixpath
import re
import shutil
import stat
import tempfile
import uuid
import warnings as python_warnings
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PureWindowsPath
from typing import Any, Iterator, Mapping
from urllib.parse import unquote, urlsplit

import bleach
from django.conf import settings
from PIL import Image, UnidentifiedImageError

from .services import ZipBudget, _consume_zip_budget, _extract_member, is_safe_path


ALLOWED_CLASS_NAMES = {
    "project-html-section",
    "project-html-grid",
    "project-html-media",
    "project-html-wide",
    "project-html-caption",
}

ALLOWED_TAGS = {
    "a", "article", "b", "blockquote", "br", "code", "dd", "del", "div",
    "dl", "dt", "em", "figcaption", "figure", "h1", "h2", "h3", "h4",
    "h5", "h6", "hr", "i", "img", "li", "main", "ol", "p", "pre",
    "section", "source", "span", "strong", "sub", "sup", "table", "tbody",
    "td", "tfoot", "th", "thead", "tr", "ul", "video",
}

_CLASS_TAGS = {
    "a", "article", "blockquote", "code", "div", "figcaption", "figure",
    "h1", "h2", "h3", "h4", "h5", "h6", "img", "main", "p", "pre",
    "section", "span", "table", "video",
}
_DROP_WITH_CONTENTS = {
    "head", "script", "style", "iframe", "object", "embed", "title",
    "template", "noscript",
}
_VOID_TAGS = {"br", "hr", "img", "source"}
_MEDIA_TAGS = {"img", "video", "source"}
_SECTION_NAMES = {"method", "framework", "results", "comparison", "gallery"}
_SECTION_ALIASES = {
    "experiment": "results",
    "experiments": "results",
    "overview": "gallery",
    "conclusion": "gallery",
}
_IMAGE_FORMATS = {
    "png": "PNG",
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "gif": "GIF",
    "webp": "WEBP",
}
_CONTROL_OR_SPACE_RE = re.compile(r"[\x00-\x20\x7f]+")


@dataclass
class _Figure:
    attributes: dict[str, str]
    caption_parts: list[str] = field(default_factory=list)


@dataclass
class _Reference:
    source: str
    path: str
    tag: str
    figure: _Figure | None


class _ActiveContentStripper(HTMLParser):
    """Drop non-body/active contents before Bleach parses the fragment.

    This is not the security boundary (Bleach and the final attribute pass are);
    it prevents script/style/head fallback text becoming visible page content.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.dropped_tag: str | None = None
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.dropped_tag:
            if tag == self.dropped_tag:
                self.depth += 1
            return
        if tag == "embed":
            return
        if tag in _DROP_WITH_CONTENTS:
            self.dropped_tag = tag
            self.depth = 1
            return
        self.parts.append(self.get_starttag_text() or "")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.dropped_tag or tag in _DROP_WITH_CONTENTS:
            return
        self.parts.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag: str) -> None:
        if self.dropped_tag:
            if tag == self.dropped_tag:
                self.depth -= 1
                if self.depth <= 0:
                    self.dropped_tag = None
                    self.depth = 0
            return
        if tag not in _DROP_WITH_CONTENTS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.dropped_tag:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if not self.dropped_tag:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.dropped_tag:
            self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return


class _ProjectHTMLParser(HTMLParser):
    """Read import metadata and strictly validate original media references."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.description = ""
        self.references: list[_Reference] = []
        self.figure_stack: list[_Figure] = []
        self.caption_figure: _Figure | None = None
        self.video_depth = 0
        self._in_title = False
        self._in_heading = False

    @staticmethod
    def _one_attribute(
        tag: str, attrs: list[tuple[str, str | None]], name: str, *, required: bool = False,
    ) -> str | None:
        values = [value for attr_name, value in attrs if attr_name == name]
        if len(values) > 1:
            raise ValueError(f"<{tag}> 包含重复 {name} 属性")
        if not values:
            if required:
                raise ValueError(f"<{tag}> 缺少 {name} 属性")
            return None
        return values[0]

    def _read_media(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        source = self._one_attribute(tag, attrs, "src", required=tag in {"img", "source"})
        if tag == "source" and self.video_depth < 1:
            raise ValueError("<source> 只能用于 <video> 内")
        if source is None:
            return
        self.references.append(_Reference(
            source=source,
            path=_resolve_package_reference(source),
            tag=tag,
            figure=self.figure_stack[-1] if self.figure_stack else None,
        ))

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool) -> None:
        attributes = {name: value or "" for name, value in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "h1" and not self.heading_parts:
            self._in_heading = True
        elif tag == "meta" and not self.description:
            if attributes.get("name", "").strip().lower() == "description":
                self.description = attributes.get("content", "").strip()
        elif tag == "figure":
            figure = _Figure({
                key: attributes.get(f"data-{key}", "").strip()
                for key in ("section", "group", "title", "description")
            })
            if not self_closing:
                self.figure_stack.append(figure)
        elif tag == "figcaption" and self.figure_stack and not self_closing:
            self.caption_figure = self.figure_stack[-1]

        if tag in _MEDIA_TAGS:
            self._read_media(tag, attrs)
        if tag == "video" and not self_closing:
            self.video_depth += 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_heading = False
        elif tag == "figcaption":
            self.caption_figure = None
        elif tag == "figure" and self.figure_stack:
            if self.caption_figure is self.figure_stack[-1]:
                self.caption_figure = None
            self.figure_stack.pop()
        if tag == "video" and self.video_depth:
            self.video_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._in_heading:
            self.heading_parts.append(data)
        if self.caption_figure is not None:
            self.caption_figure.caption_parts.append(data)

    @property
    def title(self) -> str:
        raw = "".join(self.title_parts) or "".join(self.heading_parts)
        return re.sub(r"\s+", " ", raw).strip() or "Untitled Project"


class _SafeHTMLRewriter(HTMLParser):
    """Serialize sanitized HTML, enforce playback/rel, and optionally map src."""

    def __init__(self, url_map: Mapping[str, str] | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.direct_urls: dict[str, str] = {}
        self.normalized_urls: dict[str, str] = {}
        for key, value in (url_map or {}).items():
            source = str(key)
            if not isinstance(value, str) or not _is_local_media_url(value):
                raise ValueError("最终媒体 URL 必须是本地路径或 MEDIA URL")
            self.direct_urls[source] = value
            try:
                normalized = _resolve_package_reference(source)
            except (TypeError, ValueError, UnicodeError):
                continue
            self.normalized_urls[normalized] = value

    def _mapped_source(self, value: str) -> str:
        if value in self.direct_urls:
            return self.direct_urls[value]
        try:
            normalized = _resolve_package_reference(value)
        except (TypeError, ValueError, UnicodeError):
            return value
        return self.normalized_urls.get(normalized, value)

    def _attributes(self, tag: str, attrs: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
        output: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        for name, raw_value in attrs:
            if name in seen:
                continue
            seen.add(name)
            value = "" if raw_value is None else str(raw_value)
            if tag == "video" and name in {
                "autoplay", "controls", "loop", "muted", "playsinline", "poster", "preload",
            }:
                continue
            if tag == "a" and name == "rel":
                continue
            if name == "class":
                tokens = list(dict.fromkeys(
                    token for token in value.split() if token in ALLOWED_CLASS_NAMES
                ))
                if tokens and tag in _CLASS_TAGS:
                    output.append(("class", " ".join(tokens)))
                continue
            if name == "src" and tag in _MEDIA_TAGS:
                value = self._mapped_source(value)
            if not _allowed_attribute(tag, name, value):
                continue
            output.append((name, value))
        if tag == "video":
            output.extend((("controls", None), ("playsinline", None), ("preload", "metadata")))
        if tag == "a" and any(name == "target" and value == "_blank" for name, value in output):
            output.append(("rel", "noopener noreferrer"))
        return output

    def _append_start(
        self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool = False,
    ) -> None:
        if tag not in ALLOWED_TAGS:
            return
        attributes = self._attributes(tag, attrs)
        rendered = [f"<{tag}"]
        for name, value in attributes:
            if value is None:
                rendered.append(f" {name}")
            else:
                rendered.append(f' {name}="{html_lib.escape(value, quote=True)}"')
        rendered.append(">")
        if self_closing and tag not in _VOID_TAGS:
            rendered.append(f"</{tag}>")
        self.parts.append("".join(rendered))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._append_start(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._append_start(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        if tag in ALLOWED_TAGS and tag not in _VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(html_lib.escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return


@dataclass
class _ArchiveRecord:
    info: zipfile.ZipInfo
    name: str
    parts: tuple[str, ...]
    target: Path
    kind: str


def _setting_int(name: str, default: int) -> int:
    value = int(getattr(settings, name, default))
    if value < 1:
        raise ValueError(f"{name} 必须是正整数")
    return value


def _allowed_extensions(kind: str) -> set[str]:
    if kind == "image":
        configured = getattr(settings, "UPLOAD_ALLOWED_IMAGE_EXTENSIONS", {"png", "jpg", "jpeg", "gif", "webp"})
    else:
        configured = getattr(settings, "UPLOAD_ALLOWED_VIDEO_EXTENSIONS", {"mp4", "webm"})
    return {str(extension).lower().lstrip(".") for extension in configured}


def _contains_controls(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _is_local_media_url(value: str) -> bool:
    """Allow relative/root MEDIA paths, never external or active URL schemes."""
    if not isinstance(value, str):
        return False
    value = html_lib.unescape(value).strip()
    if not value or "\\" in value or _contains_controls(value):
        return False
    compact = _CONTROL_OR_SPACE_RE.sub("", value)
    if compact.startswith("//"):
        return False
    try:
        parsed = urlsplit(compact)
        decoded_path = unquote(urlsplit(value).path, errors="strict")
    except (ValueError, UnicodeError):
        return False
    if parsed.scheme or parsed.netloc or _contains_controls(decoded_path) or "\\" in decoded_path:
        return False
    return all(part != ".." for part in decoded_path.split("/"))


def _is_http_url(value: str) -> bool:
    if not isinstance(value, str):
        return False
    value = html_lib.unescape(value).strip()
    if not value or "\\" in value or _contains_controls(value):
        return False
    try:
        parsed = urlsplit(_CONTROL_OR_SPACE_RE.sub("", value))
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _resolve_package_reference(raw_source: str) -> str:
    """Return a normalized path relative to index.html, or raise ValueError."""
    if not isinstance(raw_source, str):
        raise ValueError("媒体 src 必须是字符串")
    source = html_lib.unescape(raw_source).strip()
    if not source or "\\" in source or _contains_controls(source):
        raise ValueError(f"媒体引用无效: {raw_source!r}")
    compact = _CONTROL_OR_SPACE_RE.sub("", source)
    try:
        parsed = urlsplit(source)
        scheme_check = urlsplit(compact)
    except ValueError as exc:
        raise ValueError(f"媒体引用无效: {raw_source!r}") from exc
    if (
        compact.startswith("//") or source.startswith("/") or parsed.netloc
        or parsed.scheme or scheme_check.scheme or scheme_check.netloc
    ):
        raise ValueError(f"媒体必须引用 ZIP 包内相对路径: {raw_source!r}")
    if parsed.query or parsed.fragment:
        raise ValueError(f"媒体引用不允许 query 或 fragment: {raw_source!r}")
    try:
        decoded = unquote(parsed.path, errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"媒体引用编码无效: {raw_source!r}") from exc
    if not decoded or "\\" in decoded or _contains_controls(decoded) or decoded.startswith("/"):
        raise ValueError(f"媒体引用无效: {raw_source!r}")
    parts = decoded.split("/")
    if any(part == ".." for part in parts):
        raise ValueError(f"媒体引用不能包含 '..': {raw_source!r}")
    if any(part == "" for part in parts):
        raise ValueError(f"媒体引用包含空路径段: {raw_source!r}")
    normalized = posixpath.normpath(decoded)
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise ValueError(f"媒体引用无效: {raw_source!r}")
    return normalized


def _allowed_attribute(tag: str, name: str, value: str) -> bool:
    if name == "class":
        return tag in _CLASS_TAGS
    if tag == "a":
        if name == "href":
            return _is_http_url(value)
        if name == "title":
            return True
        if name == "target":
            return value == "_blank"
    if tag == "img":
        if name == "src":
            return _is_local_media_url(value)
        if name in {"alt", "title"}:
            return True
        if name in {"width", "height"}:
            return value.isdigit() and 1 <= int(value) <= 200_000
        if name == "loading":
            return value in {"lazy", "eager"}
    if tag in {"video", "source"} and name == "src":
        return _is_local_media_url(value)
    if tag == "source" and name == "type":
        return value.lower() in {"video/mp4", "video/webm"}
    if tag in {"th", "td"} and name in {"colspan", "rowspan"}:
        return value.isdigit() and 1 <= int(value) <= 1000
    return False


def _rewrite_fragment(html: str, url_map: Mapping[str, str] | None = None) -> str:
    rewriter = _SafeHTMLRewriter(url_map)
    rewriter.feed(html)
    rewriter.close()
    return "".join(rewriter.parts).strip()


def sanitize_project_html(html: str) -> str:
    """Return a repeatable safe HTML fragment for import and public rendering.

    Active elements/attributes, external media, arbitrary classes, ``srcset``,
    and playback defaults are removed.  Videos always end with exactly the
    safe playback attributes ``controls playsinline preload="metadata"``.
    ``data-*`` metadata is never retained in the public fragment.
    """
    if not isinstance(html, str):
        raise TypeError("html 必须是字符串")
    stripper = _ActiveContentStripper()
    stripper.feed(html)
    stripper.close()
    cleaned = bleach.clean(
        "".join(stripper.parts),
        tags=ALLOWED_TAGS,
        attributes=_allowed_attribute,
        protocols={"http", "https"},
        strip=True,
        strip_comments=True,
    )
    return _rewrite_fragment(cleaned)


def rewrite_html_asset_urls(html: str, url_map: Mapping[str, str]) -> str:
    """Sanitize *html* and replace matching package ``src`` values with MEDIA URLs.

    Keys may be literal references (including ``./`` and percent encoding) or
    normalized paths relative to ``index.html``.  Unmatched references and all
    other sanitized content are preserved.  Values must remain local URLs.
    """
    if not isinstance(url_map, Mapping):
        raise TypeError("url_map 必须是映射")
    return _rewrite_fragment(sanitize_project_html(html), url_map)


def _archive_parts(filename: str) -> tuple[str, ...]:
    if not isinstance(filename, str) or not filename or "\x00" in filename:
        raise ValueError("ZIP 包含无效文件名")
    if _contains_controls(filename):
        raise ValueError(f"ZIP 文件名包含控制字符: {filename!r}")
    portable = filename.replace("\\", "/")
    windows_path = PureWindowsPath(filename)
    if portable.startswith("/") or windows_path.drive or windows_path.root:
        raise ValueError(f"ZIP 不支持绝对路径: {filename}")
    raw_parts = portable.split("/")
    if any(part == ".." for part in raw_parts):
        raise ValueError(f"ZIP 路径越界: {filename}")
    parts = tuple(part for part in raw_parts if part not in {"", "."})
    if any(":" in part for part in parts):
        raise ValueError(f"ZIP 文件名包含不支持的分隔符: {filename}")
    if not parts:
        raise ValueError(f"ZIP 包含空文件路径: {filename}")
    return parts


def _copy_upload(uploaded_file: Any, target: Path, maximum: int) -> None:
    def chunks() -> Iterator[bytes]:
        chunk_method = getattr(uploaded_file, "chunks", None)
        if callable(chunk_method):
            yield from chunk_method()
            return
        while True:
            chunk = uploaded_file.read(1024 * 1024)
            if not chunk:
                break
            yield chunk

    written = 0
    with target.open("xb") as destination:
        for chunk in chunks():
            if not isinstance(chunk, bytes):
                raise TypeError("上传文件必须返回 bytes")
            written += len(chunk)
            if written > maximum:
                raise ValueError(f"ZIP 文件过大，最多 {maximum} bytes")
            destination.write(chunk)


def _verify_image(path: Path, extension: str) -> dict[str, int]:
    expected_format = _IMAGE_FORMATS.get(extension)
    if expected_format is None:
        raise ValueError(f"不支持的图片类型: {path.name}")
    try:
        with python_warnings.catch_warnings():
            python_warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                actual_format = (image.format or "").upper()
                width, height = image.size
                if actual_format != expected_format:
                    raise ValueError(
                        f"图片扩展名与内容不符: {path.name} ({actual_format or 'unknown'})"
                    )
                image.verify()
    except (
        UnidentifiedImageError, OSError, SyntaxError,
        Image.DecompressionBombWarning, Image.DecompressionBombError,
    ) as exc:
        raise ValueError(f"图片校验失败: {path.name}") from exc
    if width < 1 or height < 1:
        raise ValueError(f"图片尺寸无效: {path.name}")
    return {"width": int(width), "height": int(height), "file_size": path.stat().st_size}


def _metadata_text(value: str, *, collapse: bool = False) -> str:
    value = html_lib.unescape(value or "").strip()
    return re.sub(r"\s+", " ", value) if collapse else value


def _media_item(
    reference: _Reference,
    record: _ArchiveRecord,
    staging_dir: Path,
    order: int,
    image_metadata: Mapping[str, int],
) -> dict[str, Any]:
    figure = reference.figure
    attributes = figure.attributes if figure else {}
    caption = "".join(figure.caption_parts) if figure else ""
    section = _metadata_text(attributes.get("section", ""), collapse=True).lower()
    section = _SECTION_ALIASES.get(section, section)
    if not section:
        section = "results" if record.kind == "video" else "gallery"
    if section not in _SECTION_NAMES:
        raise ValueError(f"未知 figure data-section: {section}")
    if section in {"method", "framework"} and record.kind != "image":
        raise ValueError("method/framework 分组只能使用图片")

    title = _metadata_text(
        attributes.get("title", "") or caption or Path(record.name).stem.replace("_", " "),
        collapse=True,
    ) or "Project media"
    description = _metadata_text(attributes.get("description", ""))
    group_name = _metadata_text(attributes.get("group", ""), collapse=True)
    if len(title) > 200:
        raise ValueError("figure data-title/figcaption 不能超过 200 字符")
    if len(description) > 2000:
        raise ValueError("figure data-description 不能超过 2000 字符")
    if len(group_name) > 120:
        raise ValueError("figure data-group 不能超过 120 字符")

    item: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "path": record.target.relative_to(staging_dir).as_posix(),
        "src": reference.path,
        "filename": record.target.name,
        "kind": record.kind,
        "section": section,
        "group_name": group_name,
        "title": title,
        "description": description,
        "order": order,
        "include": True,
        "errors": [],
        "file_size": record.target.stat().st_size,
    }
    if record.kind == "image":
        item.update(image_metadata)
    return item


def parse_html_project_upload(uploaded_file: Any) -> dict[str, Any]:
    """Stage and validate one HTML project ZIP.

    The archive must contain exactly one ``index.html`` at its root or inside
    one shared top-level directory.  Only referenced, allow-listed local media
    are returned.  The result contains only JSON/session-serializable values.

    Raises ``ValueError`` for invalid archives, references, media, or limits.
    Any staging directory created by a failed parse is removed first.
    """
    filename = str(getattr(uploaded_file, "name", ""))
    if Path(filename).suffix.lower() != ".zip":
        raise ValueError("HTML 项目导入仅支持 .zip 文件")

    max_zip_size = _setting_int("UPLOAD_MAX_ZIP_SIZE", 50 * 1024 * 1024)
    declared_size = getattr(uploaded_file, "size", None)
    if declared_size is not None and int(declared_size) > max_zip_size:
        raise ValueError(f"ZIP 文件过大，最多 {max_zip_size} bytes")

    max_uncompressed = _setting_int("UPLOAD_MAX_ZIP_UNCOMPRESSED_SIZE", 150 * 1024 * 1024)
    max_entries = _setting_int("UPLOAD_MAX_ZIP_ENTRIES", 200)
    max_file_size = _setting_int("UPLOAD_MAX_FILE_SIZE", 10 * 1024 * 1024)
    max_video_size = _setting_int("UPLOAD_MAX_VIDEO_SIZE", 50 * 1024 * 1024)
    max_media = _setting_int("UPLOAD_MAX_PROJECT_MEDIA", 50)
    # Raster formats only: never let a settings expansion re-enable active SVG.
    image_extensions = _allowed_extensions("image") & set(_IMAGE_FORMATS)
    video_extensions = _allowed_extensions("video") & {"mp4", "webm"}

    staging_dir: Path | None = None
    try:
        staging_dir = Path(tempfile.mkdtemp(prefix="project_"))
        source_dir = staging_dir / "_source"
        source_dir.mkdir()
        outer_zip = source_dir / "upload.zip"
        _copy_upload(uploaded_file, outer_zip, max_zip_size)
        content_dir = staging_dir / "content"

        with zipfile.ZipFile(outer_zip, "r") as bundle:
            budget = ZipBudget(remaining_bytes=max_uncompressed, remaining_entries=max_entries)
            _consume_zip_budget(bundle, budget)
            infos = bundle.infolist()
            records: list[_ArchiveRecord] = []
            html_records: list[_ArchiveRecord] = []
            all_parts: list[tuple[str, ...]] = []
            seen_names: set[str] = set()

            for info in infos:
                parts = _archive_parts(info.filename)
                all_parts.append(parts)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise ValueError(f"ZIP 不支持符号链接: {info.filename}")
                if info.flag_bits & 0x1:
                    raise ValueError(f"ZIP 不支持加密文件: {info.filename}")
                if info.is_dir():
                    continue
                if info.file_size < 0:
                    raise ValueError(f"ZIP 条目大小无效: {info.filename}")

                extension = parts[-1].rsplit(".", 1)[-1].lower() if "." in parts[-1] else ""
                if extension in {"html", "htm"}:
                    kind = "html"
                elif extension in image_extensions:
                    kind = "image"
                elif extension in video_extensions:
                    kind = "video"
                else:
                    raise ValueError(f"ZIP 包含不支持的文件: {info.filename}")

                normalized_name = "/".join(parts)
                collision_key = normalized_name.casefold()
                if collision_key in seen_names:
                    raise ValueError(f"ZIP 包含重复或冲突路径: {info.filename}")
                seen_names.add(collision_key)
                target = content_dir.joinpath(*parts)
                if not is_safe_path(content_dir, target):
                    raise ValueError(f"ZIP 路径越界: {info.filename}")
                limit = max_video_size if kind == "video" else max_file_size
                if info.file_size > limit:
                    raise ValueError(f"文件超过单文件大小限制: {info.filename}")
                record = _ArchiveRecord(info, normalized_name, parts, target, kind)
                records.append(record)
                if kind == "html":
                    html_records.append(record)

            if len(html_records) != 1:
                raise ValueError("ZIP 必须且只能包含一个 HTML 文件")
            index_record = html_records[0]
            if index_record.parts[-1].lower() != "index.html" or len(index_record.parts) > 2:
                raise ValueError("HTML 文件必须是根目录或一个顶层目录内的 index.html")
            root_parts = index_record.parts[:-1]
            if root_parts:
                for parts in all_parts:
                    if parts[:len(root_parts)] != root_parts:
                        raise ValueError("index.html 在顶层目录内时，所有文件必须位于该目录")

            media_records = [record for record in records if record.kind != "html"]
            if len(media_records) > max_media:
                raise ValueError(f"媒体文件过多，最多允许 {max_media} 个")

            for record in records:
                _extract_member(bundle, record.info, record.target)
                actual_size = record.target.stat().st_size
                limit = max_video_size if record.kind == "video" else max_file_size
                if actual_size > limit or actual_size != record.info.file_size:
                    raise ValueError(f"ZIP 文件实际大小异常: {record.info.filename}")

        try:
            source_html = index_record.target.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("index.html 必须使用 UTF-8 编码") from exc
        metadata = _ProjectHTMLParser()
        metadata.feed(source_html)
        metadata.close()
        cleaned_html = sanitize_project_html(source_html)
        if not cleaned_html:
            raise ValueError("index.html 清理后没有可展示内容")
        title = metadata.title
        if len(title) > 255:
            raise ValueError("HTML title 不能超过 255 字符")

        records_by_name = {record.name: record for record in media_records}
        used: dict[str, tuple[_Reference, _ArchiveRecord]] = {}
        for reference in metadata.references:
            package_name = "/".join((*root_parts, *reference.path.split("/")))
            record = records_by_name.get(package_name)
            if record is None:
                raise ValueError(f"HTML 引用了不存在的包内媒体: {reference.source}")
            expected_kind = "image" if reference.tag == "img" else "video"
            if record.kind != expected_kind:
                raise ValueError(f"媒体引用类型与文件扩展名不符: {reference.source}")
            if package_name in used:
                continue
            used[package_name] = (reference, record)

        warnings: list[str] = []
        for record in media_records:
            if record.name not in used:
                warnings.append(f"未引用媒体未导入: {record.name}")
                record.target.unlink(missing_ok=True)

        items: list[dict[str, Any]] = []
        for reference, record in used.values():
            image_metadata: Mapping[str, int] = {}
            if record.kind == "image":
                image_metadata = _verify_image(record.target, record.target.suffix.lower().lstrip("."))
            items.append(_media_item(reference, record, staging_dir, len(items) + 1, image_metadata))

        assets = [{**item, "errors": list(item["errors"])} for item in items]
        project_media = [{**item, "errors": list(item["errors"])} for item in items]
        return {
            "staging_dir": str(staging_dir),
            "content": cleaned_html,
            "title": title,
            "description": _metadata_text(metadata.description),
            "mode": "project_html",
            "assets": assets,
            "project_media": project_media,
            "warnings": warnings,
        }
    except zipfile.BadZipFile as exc:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise ValueError("无效的 ZIP 文件") from exc
    except Exception:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise
