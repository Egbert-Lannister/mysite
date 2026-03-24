import re
import hashlib
from django.utils.text import slugify
import uuid


def generate_unique_slug(title, instance_pk=None):
    """Generate unique slug from title"""
    from .models import Post
    
    base_slug = slugify(title)
    if not base_slug:
        base_slug = uuid.uuid4().hex[:8]
    
    slug = base_slug
    counter = 1
    while Post.objects.filter(slug=slug).exclude(pk=instance_pk).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def compute_content_hash(content: str) -> str:
    """Compute SHA256 hash of content for deduplication"""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def slugify_header(text: str, sep: str = '-') -> str:
    """Create URL-safe anchor from header text (supports Chinese)
    
    Args:
        text: Header text to slugify
        sep: Separator to use between words (default: '-')
    """
    # Remove special characters but keep Chinese
    text = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', text)
    text = text.strip().lower()
    # Replace spaces with the separator
    text = re.sub(r'\s+', sep, text)
    # If empty, use hash
    if not text:
        text = hashlib.md5(text.encode()).hexdigest()[:8]
    return text


def _normalize_backticks_for_math(content: str) -> str:
    r"""Common paste/Notion confusables that prevent `` `$...$` `` (LaTeX in backticks) from matching."""
    # Fullwidth grave / spacing chars sometimes used instead of ASCII backtick
    return (
        content.replace("\uff40", "`")  # FULLWIDTH GRAVE ACCENT ｀
        .replace("\u00b4", "`")  # ACUTE ACCENT ´ (wrong but seen in paste)
    )


def _unwrap_math_wrapped_in_backticks(content: str) -> str:
    r"""Unwrap backticks that only wrap LaTeX $...$ or $$...$$ (outside fenced code blocks).

    Otherwise Markdown emits <code>…</code> and KaTeX renderMathInElement skips <code>,
    so math stays as raw literal text.
    """
    parts = re.split(r"(```[\s\S]*?```)", content)
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(part)
            continue
        p = part
        p = re.sub(r"`(\$\$[\s\S]*?\$\$)`", r"\1", p)
        p = re.sub(r"`(\$[^`]+?\$)`", r"\1", p)
        out.append(p)
    return "".join(out)


def _unwrap_math_wrapped_in_backticks_repeat(content: str, rounds: int = 4) -> str:
    """Repeat unwrap (occasionally nested / duplicated patterns)."""
    content = _normalize_backticks_for_math(content)
    for _ in range(rounds):
        nxt = _unwrap_math_wrapped_in_backticks(content)
        if nxt == content:
            break
        content = nxt
    return content


def _unwrap_code_tags_pure_math(html: str) -> str:
    """
    Remove <code>…</code> when the entire text content is only $…$ / $$…$$ fragments
    (possibly several, separated by spaces). Markdown+CodeHilite often adds classes;
    KaTeX skips <code>, so these must become plain text for renderMathInElement.

    Avoid obvious shell vars like ``$PATH$`` (no LaTeX command with backslash).
    """
    frag = r"(?:\$\$[\s\S]*?\$\$|\$[^$]*\$)"
    only_math_frags = re.compile(r"^\s*(?:" + frag + r"\s*)+$")

    def _looks_tex(s: str) -> bool:
        if re.search(r"\\[a-zA-Z]", s):
            return True
        if "^" in s or "_" in s:
            return True
        if "\\{" in s or "\\}" in s:
            return True
        return False

    def repl(m: re.Match) -> str:
        full = m.group(0)
        # Fenced / highlighted code blocks — do not unwrap
        if re.search(
            r'<code[^>]*\sclass="[^"]*(?:language-|hljs|highlight)',
            full,
            re.IGNORECASE,
        ):
            return full
        inner = m.group(1)
        s = inner.strip()
        if not only_math_frags.match(s):
            return full
        if not _looks_tex(s):
            return full
        return s

    return re.sub(r"<code[^>]*>(.*?)</code>", repl, html, flags=re.DOTALL | re.IGNORECASE)


def _protect_math(content: str) -> tuple[str, list[tuple[str, str]]]:
    """Replace LaTeX math blocks with placeholders before markdown processing.
    Returns (protected_content, [(placeholder, original), ...])
    """
    placeholders = []
    counter = [0]

    def _make_placeholder(match):
        original = match.group(0)
        tag = f"MATHPLACEHOLDER{counter[0]:04d}END"
        counter[0] += 1
        placeholders.append((tag, original))
        return tag

    # Protect fenced code blocks first so we don't touch math inside them
    code_blocks = []
    def _save_code(m):
        tag = f"CODEPLACEHOLDER{len(code_blocks):04d}END"
        code_blocks.append((tag, m.group(0)))
        return tag

    content = re.sub(r'```[\s\S]*?```', _save_code, content)
    content = re.sub(r'`[^`\n]+`', _save_code, content)

    # $$...$$ block math (may span lines)
    content = re.sub(r'\$\$(.+?)\$\$', _make_placeholder, content, flags=re.DOTALL)
    # $...$ inline math — allow optional spaces after opening / before closing $
    # (many authors write "$ \Sigma $" or "$\Sigma $"; old regex skipped these so
    # markdown could corrupt \command, and KaTeX saw broken text nodes.)
    content = re.sub(
        r'(?<!\$)\$(?!\$)\s*(.+?)\s*\$(?!\$)',
        _make_placeholder,
        content,
    )

    # Restore code blocks
    for tag, code in code_blocks:
        content = content.replace(tag, code)

    return content, placeholders


def _restore_math(html: str, placeholders: list[tuple[str, str]]) -> str:
    """Restore LaTeX math from placeholders after markdown processing,
    wrapping them in elements that KaTeX auto-render can pick up.
    """
    for tag, original in placeholders:
        escaped = original.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        html = html.replace(tag, escaped)
    return html


def render_markdown_with_toc(content: str) -> tuple[str, list[dict]]:
    """
    Render markdown to HTML with syntax highlighting, TOC extraction,
    and LaTeX math preservation (rendered client-side via KaTeX).
    Returns (html, toc_items)
    """
    import markdown
    from markdown.extensions.toc import TocExtension
    from markdown.extensions.codehilite import CodeHiliteExtension
    from markdown.extensions.fenced_code import FencedCodeExtension

    # `` `$\Sigma$` `` → $\Sigma$ so math is not turned into <code> (KaTeX ignores code)
    content = _unwrap_math_wrapped_in_backticks_repeat(content)

    # Protect LaTeX math from markdown processing
    content, math_placeholders = _protect_math(content)
    
    extensions = [
        'tables',
        FencedCodeExtension(),
        CodeHiliteExtension(
            css_class='highlight',
            guess_lang=False,
            linenums=False,
            use_pygments=True,
        ),
        TocExtension(
            slugify=slugify_header,
            toc_depth='2-4',
        ),
    ]
    
    md = markdown.Markdown(extensions=extensions)
    html = md.convert(content)

    # Restore LaTeX math
    html = _restore_math(html, math_placeholders)

    # <code>$...$</code> from backtick-wrapped math KaTeX would skip
    html = _unwrap_code_tags_pure_math(html)
    
    # Extract TOC
    toc_items = []
    toc_tokens = getattr(md, 'toc_tokens', [])
    
    def extract_toc_items(tokens, base_level=2):
        for token in tokens:
            toc_items.append({
                'anchor': token['id'],
                'title': token['name'],
                'level': token['level'],
            })
            if token.get('children'):
                extract_toc_items(token['children'], base_level + 1)
    
    extract_toc_items(toc_tokens)
    
    return html, toc_items
