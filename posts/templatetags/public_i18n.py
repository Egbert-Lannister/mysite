from django import template
from django.utils import translation

from .english_dates import english_date

register = template.Library()


@register.filter
def display_date(value, style='long'):
    if not value:
        return ''
    if translation.get_language().startswith('zh'):
        return f'{value.year}年{value.month}月{value.day}日'
    return english_date(value, style)


@register.simple_tag
def count_label(count, kind):
    units = {
        'article': ('article', 'articles'),
        'entry': ('entry', 'entries'),
        'project': ('project', 'projects'),
        'result': ('result', 'results'),
        'collection': ('collection', 'collections'),
        'asset': ('asset', 'assets'),
        'tagged_entry': ('tagged entry', 'tagged entries'),
    }
    singular, plural = units[kind]
    return translation.ngettext(singular, plural, count)
