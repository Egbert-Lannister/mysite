#!/usr/bin/env python
"""Build slim admin webfonts into ``static/unfold/fonts/``.

Unfold ships the full Material Symbols set (285 KB / 3882 glyphs) and the full
Inter family (~114 KB per weight). On a low-bandwidth link the icon font never
finished downloading, so every icon rendered as its raw ligature name. The
generated subsets live in ``STATICFILES_DIRS`` and therefore shadow the files
from the installed ``unfold`` package.

Re-run this after adding icons to ``UNFOLD`` or upgrading django-unfold:

    python scripts/subset_admin_fonts.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from fontTools.subset import Options, Subsetter, parse_unicodes
from fontTools.ttLib import TTFont

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "static" / "unfold" / "fonts"

# Source files come from the installed package, never from staticfiles/.
try:
    import unfold

    UNFOLD_DIR = Path(unfold.__file__).resolve().parent
except ImportError:  # pragma: no cover
    sys.exit("django-unfold is not installed")

UNFOLD_FONTS = UNFOLD_DIR / "static" / "unfold" / "fonts"

# Places that can reference an icon name.
SCAN_ROOTS = [UNFOLD_DIR, BASE_DIR / "templates", BASE_DIR / "mysite", BASE_DIR / "posts"]
SCAN_SUFFIXES = {".html", ".py"}

ICON_SPAN_RE = re.compile(r"material-symbols-outlined[^>]*>(.*?)</(?:span|div)>", re.S)
ICON_CONFIG_RE = re.compile(r'(?:"icon"|"site_symbol"|"SITE_SYMBOL"|icon)\s*[:=]\s*"([a-z0-9_]+)"')
WORD_RE = re.compile(r"[a-z0-9_]+")

# Icons that templates may pick at runtime, plus room to add sidebar entries
# without regenerating the font.
EXTRA_ICONS = {
    "arrow_drop_down", "arrow_drop_up", "bar_chart", "bookmark", "calendar_month",
    "check", "check_circle", "chevron_left", "close_fullscreen", "content_copy",
    "dashboard", "description", "done", "error", "expand_less", "folder",
    "functions", "group", "home", "image", "info", "key", "keyboard_arrow_down",
    "keyboard_arrow_left", "keyboard_arrow_right", "keyboard_arrow_up", "language",
    "list", "mail", "menu", "more_vert", "navigate_before", "navigate_next",
    "notifications", "remove", "save", "schedule", "sell", "star", "sync",
    "translate", "trending_up", "warning",
}

# The "latin" range Google Fonts serves by default. Anything outside it (CJK,
# latin-ext) already falls back to a system font in this admin.
INTER_UNICODES = (
    "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,"
    "U+0308,U+0329,U+2000-206F,U+2074,U+20AC,U+2122,U+2191,U+2193,U+2212,"
    "U+2215,U+FEFF,U+FFFD"
)

INTER_FACES = [("Inter-Regular", 400), ("Inter-Medium", 500), ("Inter-SemiBold", 600), ("Inter-Bold", 700)]


def ligatures(font: TTFont) -> list[tuple[list[str], str]]:
    """Every ligature rule as ``(input glyphs, output glyph)``."""
    result = []
    for lookup in font["GSUB"].table.LookupList.Lookup:
        if lookup.LookupType != 4:
            continue
        for subtable in lookup.SubTable:
            for first, rules in (getattr(subtable, "ligatures", None) or {}).items():
                for rule in rules:
                    result.append(([first, *rule.Component], rule.LigGlyph))
    return result


def glyph_to_char(font: TTFont) -> dict[str, str]:
    """Reverse the cmap, preferring the codepoint an icon name would use."""
    def rank(codepoint: int) -> tuple[int, int]:
        if 0x61 <= codepoint <= 0x7A:  # a-z
            return (0, codepoint)
        if 0x30 <= codepoint <= 0x39:  # 0-9
            return (1, codepoint)
        return (2, codepoint)

    best: dict[str, int] = {}
    for codepoint, glyph in font.getBestCmap().items():
        if glyph not in best or rank(codepoint) < rank(best[glyph]):
            best[glyph] = codepoint
    return {glyph: chr(codepoint) for glyph, codepoint in best.items()}


def icon_map(font: TTFont) -> dict[str, tuple[list[str], str]]:
    """Map every icon name to the glyphs its ligature needs.

    Names are rebuilt from the cmap rather than read off the ligature glyph,
    because Material Symbols aliases several names onto one glyph and because
    ``pyftsubset`` drops the ``post`` glyph names from its output.
    """
    chars = glyph_to_char(font)
    result = {}
    for inputs, output in ligatures(font):
        if all(glyph in chars for glyph in inputs):
            result["".join(chars[glyph] for glyph in inputs)] = (inputs, output)
    return result


def used_icons(known: set[str]) -> set[str]:
    found = set()
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in SCAN_SUFFIXES or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for body in ICON_SPAN_RE.findall(text):
                found |= {word for word in WORD_RE.findall(body) if word in known}
            found |= {name for name in ICON_CONFIG_RE.findall(text) if name in known}
    return found


def subset_material_symbols() -> None:
    source = UNFOLD_FONTS / "material-symbols" / "Material-Symbols-Outlined.woff2"
    font = TTFont(source)
    available = icon_map(font)

    icons = used_icons(set(available)) | (EXTRA_ICONS & set(available))
    missing = EXTRA_ICONS - set(available)
    if missing:
        print(f"  warning: not in font, skipped: {sorted(missing)}")

    keep_glyphs = set()
    for icon in icons:
        inputs, output = available[icon]
        keep_glyphs.update(inputs)
        keep_glyphs.add(output)

    reverse_cmap: dict[str, set[int]] = {}
    for codepoint, glyph in font.getBestCmap().items():
        reverse_cmap.setdefault(glyph, set()).add(codepoint)
    keep_codepoints = {cp for glyph in keep_glyphs for cp in reverse_cmap.get(glyph, ())}

    options = Options()
    options.flavor = "woff2"
    # Material Symbols maps icon names through required ligatures, not `liga`.
    options.layout_features = ["rlig", "liga", "dlig"]
    options.layout_closure = False
    options.notdef_outline = True
    options.drop_tables += ["FFTM"]

    subsetter = Subsetter(options=options)
    subsetter.populate(glyphs=sorted(keep_glyphs), unicodes=sorted(keep_codepoints))
    subsetter.subset(font)

    target = OUT_DIR / "material-symbols"
    target.mkdir(parents=True, exist_ok=True)
    out_file = target / "Material-Symbols-Outlined.woff2"
    font.flavor = "woff2"
    font.save(out_file)

    # font-display: block keeps the ligature text hidden instead of flashing it.
    (target / "styles.css").write_text(
        '@font-face {\n'
        '  font-family: "Material Symbols Outlined";\n'
        "  font-style: normal;\n"
        "  font-weight: 400;\n"
        "  font-display: block;\n"
        '  src: url(Material-Symbols-Outlined.woff2) format("woff2");\n'
        "}\n",
        encoding="utf-8",
    )

    lost = icons - set(icon_map(TTFont(out_file)))
    if lost:
        sys.exit(f"subset dropped icons: {sorted(lost)}")

    print(
        f"  material-symbols: {len(icons)} icons, "
        f"{source.stat().st_size / 1024:.0f} KB -> {out_file.stat().st_size / 1024:.0f} KB"
    )


def subset_inter() -> None:
    target = OUT_DIR / "inter"
    target.mkdir(parents=True, exist_ok=True)
    faces = []
    for name, weight in INTER_FACES:
        source = UNFOLD_FONTS / "inter" / f"{name}.woff2"
        options = Options()
        options.flavor = "woff2"
        options.layout_features = ["*"]

        font = TTFont(source)
        subsetter = Subsetter(options=options)
        subsetter.populate(unicodes=parse_unicodes(INTER_UNICODES))
        subsetter.subset(font)

        out_file = target / f"{name}.woff2"
        font.flavor = "woff2"
        font.save(out_file)
        faces.append((name, weight))
        print(
            f"  {name}: {source.stat().st_size / 1024:.0f} KB -> "
            f"{out_file.stat().st_size / 1024:.0f} KB"
        )

    (target / "styles.css").write_text(
        "\n".join(
            "@font-face {\n"
            '  font-family: "Inter";\n'
            "  font-style: normal;\n"
            f"  font-weight: {weight};\n"
            "  font-display: swap;\n"
            f'  src: url({name}.woff2) format("woff2");\n'
            "}\n"
            for name, weight in faces
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    print("Subsetting admin fonts...")
    subset_material_symbols()
    subset_inter()
    print("Done. Run `python manage.py collectstatic --noinput` next.")
