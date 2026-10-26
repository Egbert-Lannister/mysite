"""Locale-independent English dates for the public English interface."""
from django import template

register = template.Library()

MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
MONTHS_SHORT = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


@register.filter
def english_date(value, style="long"):
    if not value:
        return ""
    month = MONTHS[value.month - 1]
    short_month = MONTHS_SHORT[value.month - 1]
    if style == "short":
        return f"{short_month} {value.day}, {value.year}"
    if style == "month_year":
        return f"{short_month} {value.year}"
    if style == "iso":
        return value.date().isoformat()
    return f"{month} {value.day}, {value.year}"
