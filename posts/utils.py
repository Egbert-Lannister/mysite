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
