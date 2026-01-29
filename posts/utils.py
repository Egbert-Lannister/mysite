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


def render_markdown_with_toc(content: str) -> tuple[str, list[dict]]:
    """
    Render markdown to HTML with syntax highlighting and TOC extraction.
    Returns (html, toc_items)
    """
    import markdown
    from markdown.extensions.toc import TocExtension
    from markdown.extensions.codehilite import CodeHiliteExtension
    from markdown.extensions.fenced_code import FencedCodeExtension
    
    # Configure extensions
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
            toc_depth='2-4',  # Support h2, h3, h4
        ),
    ]
    
    md = markdown.Markdown(extensions=extensions)
    html = md.convert(content)
    
    # Extract TOC with proper levels from toc_tokens
    toc_items = []
    toc_tokens = getattr(md, 'toc_tokens', [])
    
    def extract_toc_items(tokens, base_level=2):
        """Recursively extract TOC items with correct levels"""
        for token in tokens:
            toc_items.append({
                'anchor': token['id'],
                'title': token['name'],
                'level': token['level'],
            })
            # Process nested children
            if token.get('children'):
                extract_toc_items(token['children'], base_level + 1)
    
    extract_toc_items(toc_tokens)
    
    return html, toc_items
