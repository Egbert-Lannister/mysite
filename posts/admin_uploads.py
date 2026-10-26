from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

from django.contrib import admin as django_admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from PIL import Image
from taggit.utils import parse_tags

from .forms import ArticleEditorForm, HtmlProjectEditorForm, ProjectEditorForm
from .html_import import parse_html_project_upload, rewrite_html_asset_urls, sanitize_project_html
from .models import Post, ProjectMedia, compute_content_hash, generate_unique_slug
from .services import (
    ALLOWED_IMAGE_EXTENSIONS,
    ALLOWED_VIDEO_EXTENSIONS,
    ContentProcessError,
    is_safe_path,
    parse_upload_file,
    process_markdown_content,
)
from .utils import render_markdown_with_toc


ARTICLE_SESSION_KEY = "upload_preview"
PROJECT_SESSION_KEY = "project_upload_preview"


def _require(request, permission):
    if not request.user.has_perm(permission):
        raise PermissionDenied


def _staging_dir(data, *, create=False, project=False):
    value = data.get("staging_dir", "")
    if value:
        path = Path(value).resolve()
        expected_parent = Path(tempfile.gettempdir()).resolve()
        if path.parent != expected_parent or not path.name.startswith(("upload_", "project_")):
            raise ValueError("暂存目录无效，请重新上传文件")
        if path.is_dir():
            return path
        if not create:
            return None
    if not create:
        return None
    path = Path(tempfile.mkdtemp(prefix="project_" if project else "upload_"))
    data["staging_dir"] = str(path)
    return path


def _cleanup_session(request, key):
    data = request.session.get(key) or {}
    value = data.get("staging_dir", "")
    if value:
        path = Path(value).resolve()
        if path.parent == Path(tempfile.gettempdir()).resolve() and path.name.startswith(("upload_", "project_")):
            shutil.rmtree(path, ignore_errors=True)
    request.session.pop(key, None)


def _asset_path(data, relative_path):
    staging = _staging_dir(data)
    if not staging or not relative_path:
        raise ValueError("暂存素材不存在，请重新上传")
    path = staging / relative_path
    if not is_safe_path(staging, path) or not path.is_file():
        raise ValueError("暂存素材不存在，请重新上传")
    return path


def _content_images(data):
    images = []
    for asset in data.get("assets", []):
        if asset.get("kind") == "image":
            try:
                images.append(_asset_path(data, asset["path"]))
            except (KeyError, ValueError):
                continue
    return images


def _write_upload(data, upload, folder):
    staging = _staging_dir(data, create=True, project=True)
    directory = staging / folder / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=True)
    filename = Path(upload.name).name
    target = directory / filename
    try:
        with target.open("xb") as stream:
            for chunk in upload.chunks():
                stream.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target.relative_to(staging).as_posix(), filename


def _media_item(relative_path, filename, kind, *, section=None, group="", include=True, order=0):
    return {
        "id": uuid.uuid4().hex,
        "path": relative_path,
        "filename": filename,
        "kind": kind,
        "section": section or ("results" if kind == "video" else "gallery"),
        "title": Path(filename).stem.replace("_", " ").strip() or "Project media",
        "description": "",
        "group_name": group,
        "order": order,
        "include": include,
        "errors": [],
    }


def _package_media(data):
    items = []
    for index, asset in enumerate(data.get("assets", []), 1):
        kind = asset["kind"]
        items.append(_media_item(
            asset["path"], asset["filename"], kind,
            include=kind == "video", order=index,
        ))
    return items


def _base_context(request, title):
    context = django_admin.site.each_context(request)
    context.update({"title": title, "content_title": title})
    return context


def _sync_fields(data, fields, request):
    for field in fields:
        data[field] = request.POST.get(field, "").strip()
    data["project_featured"] = "project_featured" in request.POST
    request.session.modified = True


def _sync_media(data, request):
    for item in data.get("project_media", []):
        prefix = f"media_{item['id']}_"
        for field in ("title", "description", "group_name", "section", "order"):
            if prefix + field in request.POST:
                item[field] = request.POST[prefix + field].strip()
        item["include"] = prefix + "include" in request.POST
        item["errors"] = []
    request.session.modified = True


def _validate_media(data):
    valid = True
    sections = dict(ProjectMedia.SECTION_CHOICES)
    for item in data.get("project_media", []):
        errors = []
        if item.get("include"):
            if not item.get("title"):
                errors.append("素材标题不能为空。")
            if len(item.get("title", "")) > 200:
                errors.append("素材标题不能超过 200 字符。")
            if item.get("section") not in sections:
                errors.append("展示分组无效。")
            if item.get("section") in {"method", "framework"} and item.get("kind") != "image":
                errors.append("方法图与框架图只能使用图片。")
            if len(item.get("group_name", "")) > 120:
                errors.append("数据集 / 实验组不能超过 120 字符。")
            if len(item.get("description", "")) > 2000:
                errors.append("补充说明不能超过 2000 字符。")
            try:
                item["order"] = int(item.get("order") or 0)
                if item["order"] < 0 or item["order"] > 999999:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append("排序必须是 0–999999 的整数。")
        item["errors"] = errors
        valid = valid and not errors
    return valid


def _stage_cover(data, upload):
    relative, filename = _write_upload(data, upload, "project_cover")
    previous = data.get("project_cover")
    data["project_cover"] = {"path": relative, "filename": filename}
    if previous and previous.get("path") != relative:
        try:
            _asset_path(data, previous["path"]).unlink(missing_ok=True)
        except (KeyError, ValueError, OSError):
            pass


def _stage_new_media(data, uploads, section, group):
    if len(data.get("project_media", [])) + len(uploads) > 50:
        raise ValueError("每个项目最多暂存 50 个媒体文件")
    new_items = []
    try:
        for upload in uploads:
            relative, filename = _write_upload(data, upload, "project_media")
            extension = Path(filename).suffix.lower().lstrip(".")
            kind = "video" if extension in ALLOWED_VIDEO_EXTENSIONS else "image"
            item = _media_item(relative, filename, kind, section=section or None, group=group, order=len(data["project_media"]) + len(new_items) + 1)
            data["project_media"].append(item)
            new_items.append(item)
    except Exception:
        for item in new_items:
            data["project_media"].remove(item)
            try:
                _asset_path(data, item["path"]).unlink(missing_ok=True)
            except (ValueError, OSError):
                pass
        raise


def _persist_media(data, item, post, storage_names):
    with _asset_path(data, item["path"]).open("rb") as stream:
        media = ProjectMedia(
            project=post, file=File(stream, name=item["filename"]), kind=item["kind"],
            section=item["section"], title=item["title"], description=item.get("description", ""),
            group_name=item.get("group_name", ""), order=item.get("order") or 0,
            width=item.get("width") or 0, height=item.get("height") or 0,
            file_size=item.get("file_size") or 0,
        )
        media.full_clean()
        # Persist the exact file first so a later DB failure can remove it.
        media.file.save(Path(item["filename"]).name, media.file.file, save=False)
        storage_names.append(media.file.name)
        media.save()
        return media


def _render_project_editor(request, data, form, *, status=200):
    context = _base_context(request, "HTML 项目预览" if data.get("mode") == "project_html" else "项目预览与编辑")
    context.update({
        "data": data,
        "form": form,
        "media_items": data.get("project_media", []),
        "is_blank_mode": data.get("mode") == "project_blank",
        "status_choices": Post.PROJECT_STATUS_CHOICES,
    })
    if data.get("mode") == "project_html":
        context["html_preview"] = sanitize_project_html(data.get("content", ""))
        return render(request, "admin/project_html_preview.html", context, status=status)

    def field_text(name):
        return ((form[name].value() or "") if form.is_bound else data.get(name, ""))[:100_000]

    for field, key in (("content", "preview_html"), ("project_method", "method_preview_html"), ("project_experiments", "experiments_preview_html"), ("project_conclusion", "conclusion_preview_html")):
        context[key] = render_markdown_with_toc(field_text(field))[0]
    return render(request, "admin/project_upload_preview.html", context, status=status)


def admin_upload(request):
    _require(request, "posts.add_post")
    error = None
    if request.method == "POST":
        uploaded = request.FILES.get("upload_file")
        if not uploaded:
            error = "请选择要上传的 Markdown 或 ZIP 文件。"
        else:
            try:
                parsed = parse_upload_file(uploaded)
                parsed.update({"series": None, "series_order": "", "mode": "upload", "editor_token": uuid.uuid4().hex})
                _cleanup_session(request, ARTICLE_SESSION_KEY)
                request.session[ARTICLE_SESSION_KEY] = parsed
                return redirect("admin_upload_preview")
            except (ValueError, OSError, UnicodeError) as exc:
                error = f"处理文件失败：{exc}"
    context = _base_context(request, "上传文章")
    context.update({"error": error, "has_draft": bool(request.session.get(ARTICLE_SESSION_KEY))})
    return render(request, "admin/upload.html", context, status=400 if error else 200)


def admin_upload_blank(request):
    _require(request, "posts.add_post")
    _cleanup_session(request, ARTICLE_SESSION_KEY)
    request.session[ARTICLE_SESSION_KEY] = {
        "title": "", "slug": "", "description": "", "category": "engineering",
        "tags": "", "date": "", "content": "", "image_count": 0, "assets": [],
        "staging_dir": "", "md_filename": "", "has_frontmatter": False,
        "warnings": [], "series": None, "series_order": "", "mode": "blank",
        "editor_token": uuid.uuid4().hex,
    }
    return redirect("admin_upload_preview")


def admin_upload_preview(request):
    _require(request, "posts.add_post")
    data = request.session.get(ARTICLE_SESSION_KEY)
    if not data:
        messages.error(request, "没有待预览的文章，请先上传文件或新建空白文章。")
        return redirect("admin_upload")

    fields = ("title", "slug", "description", "category", "tags", "date", "content", "series_order")
    if request.method == "POST":
        form = ArticleEditorForm(request.POST)
        _sync_fields(data, fields, request)
        data["series"] = request.POST.get("series", "")
        action = request.POST.get("action", "draft")
        if action not in {"draft", "publish"}:
            form.add_error(None, "保存操作无效。")
        if form.is_valid() and not form.errors:
            overrides = dict(form.cleaned_data)
            overrides["published"] = action == "publish"
            overrides["series_id"] = ""  # Explicit series object below takes precedence.
            try:
                staging = _staging_dir(data)
                post, created, warnings = process_markdown_content(
                    form.cleaned_data["content"], _content_images(data), staging,
                    md_filename=data.get("md_filename", ""), overrides=overrides,
                )
            except ContentProcessError as exc:
                form.add_error(exc.field, str(exc))
            except (ValidationError, ValueError, OSError) as exc:
                form.add_error(None, f"保存失败，已保留填写内容与素材：{exc}")
            else:
                for warning in warnings:
                    messages.warning(request, warning)
                messages.success(request, f"已{'发布' if post.published else '保存草稿'}：{post.title}")
                _cleanup_session(request, ARTICLE_SESSION_KEY)
                return redirect("admin:posts_post_change", post.pk)
    else:
        initial = dict(data)
        if initial.get("date") is None:
            initial["date"] = timezone.now()
        form = ArticleEditorForm(initial=initial)

    content = form["content"].value() or ""
    preview_html, _ = render_markdown_with_toc(content[:100_000])
    context = _base_context(request, "预览与编辑")
    context.update({
        "data": data, "form": form, "preview_html": preview_html,
        "is_blank_mode": data.get("mode") == "blank",
    })
    return render(request, "admin/upload_preview.html", context, status=400 if form.errors else 200)


def admin_project_upload(request):
    _require(request, "posts.add_project")
    error = None
    if request.method == "POST":
        uploaded = request.FILES.get("upload_file")
        import_mode = request.POST.get("import_mode", "markdown")
        if not uploaded:
            error = "请选择要上传的 ZIP 文件。" if import_mode == "html" else "请选择项目 Markdown 或 ZIP 文件。"
        else:
            try:
                if import_mode == "html":
                    parsed = parse_html_project_upload(uploaded)
                    parsed.update({"category": "projects", "project_status": "active", "project_tech_stack": "", "github_url": "", "demo_url": "", "paper_url": "", "project_featured": False, "project_cover": None, "editor_token": uuid.uuid4().hex})
                elif import_mode == "markdown":
                    parsed = parse_upload_file(uploaded, allow_videos=True)
                    parsed.update({"category": "projects", "mode": "project_upload", "project_media": _package_media(parsed), "project_cover": None, "editor_token": uuid.uuid4().hex})
                else:
                    raise ValueError("未知的项目导入方式。")
                _cleanup_session(request, PROJECT_SESSION_KEY)
                request.session[PROJECT_SESSION_KEY] = parsed
                return redirect("admin_project_upload_preview")
            except (ValueError, OSError, UnicodeError) as exc:
                error = f"处理项目文件失败：{exc}"
    context = _base_context(request, "上传项目")
    context.update({"error": error, "has_draft": bool(request.session.get(PROJECT_SESSION_KEY))})
    return render(request, "admin/project_upload.html", context, status=400 if error else 200)


def admin_project_upload_blank(request):
    _require(request, "posts.add_project")
    _cleanup_session(request, PROJECT_SESSION_KEY)
    request.session[PROJECT_SESSION_KEY] = {
        "title": "", "slug": "", "description": "", "category": "projects",
        "tags": "", "date": "", "content": "", "project_method": "", "project_experiments": "", "project_conclusion": "", "image_count": 0, "assets": [],
        "staging_dir": "", "md_filename": "", "has_frontmatter": False,
        "warnings": [], "mode": "project_blank", "project_status": "active",
        "project_tech_stack": "", "github_url": "", "demo_url": "", "paper_url": "",
        "project_featured": False, "project_media": [], "project_cover": None,
        "editor_token": uuid.uuid4().hex,
    }
    return redirect("admin_project_upload_preview")


def _admin_html_project_preview(request, data):
    fields = ("title", "slug", "description", "tags", "date", "project_status", "project_tech_stack", "github_url", "demo_url", "paper_url")
    if request.method == "POST":
        form = HtmlProjectEditorForm(request.POST)
        _sync_fields(data, fields, request)
        action = request.POST.get("action", "draft")
        if action not in {"draft", "publish"}:
            form.add_error(None, "保存操作无效。")
        if form.is_valid() and not form.errors:
            storage_names = []
            try:
                with transaction.atomic():
                    title = form.cleaned_data["title"].strip()
                    slug = form.cleaned_data.get("slug") or generate_unique_slug(title)
                    post = Post(
                        title=title, slug=slug, description=form.cleaned_data.get("description", ""),
                        content=data["content"], content_hash=compute_content_hash(data["content"]),
                        date=form.cleaned_data.get("date") or timezone.now(), category="projects",
                        published=action == "publish", project_status=form.cleaned_data["project_status"],
                        project_tech_stack=form.cleaned_data.get("project_tech_stack", ""),
                        github_url=form.cleaned_data.get("github_url", ""), paper_url=form.cleaned_data.get("paper_url", ""),
                        demo_url=form.cleaned_data.get("demo_url", ""), project_featured=bool(form.cleaned_data.get("project_featured")),
                        project_content_mode="html",
                    )
                    post.full_clean()
                    post.save()
                    post.tags.set(parse_tags(form.cleaned_data.get("tags", "")))
                    url_map = {}
                    for item in data.get("project_media", []):
                        if not item.get("include"):
                            continue
                        media = _persist_media(data, item, post, storage_names)
                        url_map[item["src"]] = media.file.url
                    post.content = sanitize_project_html(rewrite_html_asset_urls(data["content"], url_map))
                    post.content_hash = compute_content_hash(post.content)
                    post.save(update_fields=["content", "content_hash", "updated_at"])
            except (ValidationError, ValueError, OSError) as exc:
                form.add_error(None, f"保存失败，已保留 HTML 与媒体：{exc}")
            except Exception:
                form.add_error(None, "保存失败，数据库已回滚；请检查 HTML 包后重试。")
            else:
                messages.success(request, f"已{'发布' if post.published else '保存项目草稿'}：{post.title}。HTML 已在公开前清理。")
                _cleanup_session(request, PROJECT_SESSION_KEY)
                return redirect("admin:posts_project_change", post.pk)
            for name in storage_names:
                default_storage.delete(name)
    else:
        initial = dict(data)
        if not initial.get("date"):
            initial["date"] = timezone.now()
        form = HtmlProjectEditorForm(initial=initial)
    return _render_project_editor(request, data, form, status=400 if form.errors else 200)


def admin_project_upload_preview(request):
    _require(request, "posts.add_project")
    data = request.session.get(PROJECT_SESSION_KEY)
    if not data:
        messages.error(request, "没有待预览的项目，请先上传项目文件或新建空白项目。")
        return redirect("admin_project_upload")
    if data.get("mode") == "project_html":
        return _admin_html_project_preview(request, data)
    data.setdefault("project_media", [])
    data.setdefault("project_cover", None)
    fields = (
        "title", "slug", "description", "tags", "date", "content", "project_method", "project_experiments", "project_conclusion", "project_status",
        "project_tech_stack", "github_url", "demo_url", "paper_url",
    )

    if request.method == "POST":
        form = ProjectEditorForm(request.POST, request.FILES)
        _sync_fields(data, fields, request)
        _sync_media(data, request)
        action = request.POST.get("action", "draft")
        form_valid = form.is_valid()

        # A valid newly selected cover is staged even when another field fails.
        cover = form.cleaned_data.get("project_cover")
        if cover:
            try:
                _stage_cover(data, cover)
            except (ValueError, OSError) as exc:
                form.add_error("project_cover", f"封面暂存失败：{exc}")
                form_valid = False

        if action.startswith("remove_media:"):
            media_id = action.split(":", 1)[1]
            data["project_media"] = [item for item in data["project_media"] if item["id"] != media_id]
            request.session.modified = True
            return redirect("admin_project_upload_preview")
        if action.startswith("cover_media:"):
            media_id = action.split(":", 1)[1]
            item = next((item for item in data["project_media"] if item["id"] == media_id), None)
            if not item or item["kind"] != "image":
                form.add_error(None, "只有已暂存的图片可以设为封面。")
            else:
                try:
                    path = _asset_path(data, item["path"])
                    with Image.open(path) as image:
                        image.verify()
                    data["project_cover"] = {"path": item["path"], "filename": item["filename"]}
                    return redirect("admin_project_upload_preview")
                except (ValueError, OSError) as exc:
                    form.add_error(None, f"无法使用该封面图片：{exc}")
        if action == "clear_cover":
            data["project_cover"] = None
            request.session.modified = True
            return redirect("admin_project_upload_preview")

        new_files = form.cleaned_data.get("new_media_files") or []
        if action in {"add_media", "draft", "publish"} and new_files:
            try:
                _stage_new_media(data, new_files, form.cleaned_data.get("new_media_section"), form.cleaned_data.get("new_media_group", ""))
                request.session.modified = True
            except (ValueError, OSError) as exc:
                form.add_error("new_media_files", str(exc))
                form_valid = False
        if action == "add_media":
            if not new_files and not cover and "new_media_files" not in form.errors:
                form.add_error("new_media_files", "请选择要添加的图片或视频。")
            elif "new_media_files" not in form.errors:
                return redirect("admin_project_upload_preview")

        if action in {"draft", "publish"} and form_valid and not form.errors:
            if not _validate_media(data):
                form.add_error(None, "请修正素材列表中的错误后再保存。")
            else:
                copied_files = []
                storage_names = []
                try:
                    staging = _staging_dir(data)
                    with transaction.atomic():
                        overrides = dict(form.cleaned_data)
                        overrides.update({"category": "projects", "project_content_mode": "structured", "published": action == "publish", "series": None, "series_order": None})
                        post, created, warnings = process_markdown_content(
                            form.cleaned_data["content"], _content_images(data), staging,
                            md_filename=data.get("md_filename", ""), overrides=overrides,
                            copied_files=copied_files,
                        )
                        if data.get("project_cover"):
                            cover_data = data["project_cover"]
                            with _asset_path(data, cover_data["path"]).open("rb") as stream:
                                post.project_cover.save(Path(cover_data["filename"]).name, File(stream), save=False)
                            storage_names.append(post.project_cover.name)
                            post.save(update_fields=["project_cover", "updated_at"])
                        for item in data["project_media"]:
                            if item.get("include"):
                                _persist_media(data, item, post, storage_names)
                except ContentProcessError as exc:
                    form.add_error(exc.field, str(exc))
                except (ValidationError, ValueError, OSError) as exc:
                    form.add_error(None, f"保存失败，已保留填写内容与上传素材：{exc}")
                except Exception as exc:
                    form.add_error(None, f"保存失败，数据库已回滚；请检查文件后重试。{exc}")
                else:
                    for warning in warnings:
                        messages.warning(request, warning)
                    messages.success(request, f"已{'发布' if post.published else '保存项目草稿'}：{post.title}。可继续编辑媒体或查看前台。")
                    _cleanup_session(request, PROJECT_SESSION_KEY)
                    return redirect("admin:posts_project_change", post.pk)
                # File storage is not transactional; only remove files written by this failed attempt.
                for name in storage_names:
                    default_storage.delete(name)
                for path in copied_files:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
        elif action not in {"draft", "publish", "add_media"} and not action.startswith(("remove_media:", "cover_media:")) and action != "clear_cover":
            form.add_error(None, "保存操作无效。")
    else:
        form = ProjectEditorForm(initial=data)

    return _render_project_editor(request, data, form, status=400 if form.errors else 200)
