"""Deterministic, content-aware layouts for research project media."""
from __future__ import annotations


def _balanced_chunks(items: list) -> list[list]:
    """Return rows of three/two/one without a single orphan in larger sets."""
    if len(items) <= 3:
        return [list(items)]
    rows = []
    remaining = list(items)
    while len(remaining) > 3:
        size = 2 if len(remaining) == 4 else 3
        rows.append(remaining[:size])
        remaining = remaining[size:]
    rows.append(remaining)
    return rows


def layout_media_group(items: list) -> list[dict]:
    """Lay out one logical experiment group.

    Panoramas get their own row. Other items share a canvas ratio within each
    row, so controls and captions remain aligned. No file is cropped.
    """
    rows: list[dict] = []
    buffered = []

    def flush():
        if not buffered:
            return
        orientations = {getattr(item, "orientation", "standard") for item in buffered}
        if orientations == {"portrait"}:
            frame = "portrait"
        elif orientations == {"square"}:
            frame = "square"
        else:
            frame = "standard"
        for chunk in _balanced_chunks(buffered):
            rows.append({"count": len(chunk), "frame": frame, "items": chunk})
        buffered.clear()

    for item in items:
        is_wide_image = not item.is_video and getattr(item, "orientation", "standard") == "wide"
        if is_wide_image:
            flush()
            rows.append({"count": 1, "frame": "wide", "items": [item]})
        else:
            buffered.append(item)
    flush()
    return rows


def build_media_sections(media: list) -> list[dict]:
    sections = []
    for key, label in (("results", "Experiments"), ("comparison", "Comparisons"), ("gallery", "Additional evidence")):
        section_items = [item for item in media if item.section == key]
        groups = []
        for item in section_items:
            title = item.group_name or label
            group = next((entry for entry in groups if entry["title"] == title), None)
            if group is None:
                group = {"title": title, "items": []}
                groups.append(group)
            group["items"].append(item)
        for index, group in enumerate(groups, 1):
            group["number"] = index
            group["rows"] = layout_media_group(group["items"])
        if groups:
            sections.append({"key": key, "label": label, "groups": groups})
    return sections
