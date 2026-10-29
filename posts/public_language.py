"""Language selection scoped to the public site, independent of the admin."""
from django.conf import settings
from django.utils import translation
from django.utils.cache import patch_vary_headers


class PublicLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path_info.startswith('/techblog/'):
            return self.get_response(request)
        language = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME, 'en')
        if language not in dict(settings.LANGUAGES):
            language = 'en'
        request.LANGUAGE_CODE = language
        with translation.override(language):
            response = self.get_response(request)
        response.headers.setdefault('Content-Language', language)
        patch_vary_headers(response, ('Cookie',))
        return response


def public_language(request):
    if not request.path_info.startswith('/techblog/'):
        return {}
    labels = (
        'Switch to light theme', 'Switch to dark theme',
        'Close navigation menu', 'Open navigation menu',
        'Copy', 'Copy code block', 'Copied', 'Failed', 'Scrollable table',
        'Link copied', 'Unable to copy the link', 'Sharing is unavailable',
        'QR component did not load; use Copy link instead',
        'Unable to create QR code; use Copy link instead',
    )
    return {'public_js_labels': {label: translation.gettext(label) for label in labels}}
