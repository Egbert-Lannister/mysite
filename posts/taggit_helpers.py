"""
Custom taggit string parsing: allow tag names that contain spaces.

Default django-taggit `_parse_tags` splits on spaces when the input has no
commas or quotes, so "deep learning" becomes two tags.

Rules:
- If the string contains no comma and no double quote, treat the whole string
  as **one** tag (spaces allowed).
- If it contains commas or quotes, use taggit's built-in parser (comma-separated
  tags; double quotes for commas inside a tag name).

Examples:
- ``deep learning``  →  ["deep learning"]
- ``pytorch, nlp``     →  ["nlp", "pytorch"]  (sorted, unique — taggit default)
- ``"foo, bar", baz``  →  handled by taggit
"""

from __future__ import annotations

from taggit.utils import _parse_tags


def parse_tags_allow_spaces(tagstring) -> list[str]:
    if tagstring is None:
        return []
    s = str(tagstring).strip()
    if not s:
        return []
    if "," not in s and '"' not in s:
        return [s]
    return _parse_tags(s)
