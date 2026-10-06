"""Small template helpers for the board templates."""
from datetime import timedelta

from django import template
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.timesince import timesince

register = template.Library()


@register.simple_tag(takes_context=True)
def nav_active(context, *url_names):
    """Return 'active' if the current URL name is one of `url_names`."""
    match = getattr(context.get("request"), "resolver_match", None)
    return "active" if match and match.url_name in url_names else ""


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    """Current query string with some parameters replaced (empty value removes it).

    Usage: <a href="{% url_replace page=2 %}">
    """
    query = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            query.pop(key, None)
        else:
            query[key] = value
    encoded = query.urlencode()
    return f"?{encoded}" if encoded else "?"


@register.filter
def message_icon(level_tag):
    """Bootstrap Icon class for a Django message level."""
    return {
        "success": "bi-check-circle-fill",
        "info": "bi-info-circle-fill",
        "warning": "bi-exclamation-triangle-fill",
        "danger": "bi-x-octagon-fill",
    }.get(level_tag, "bi-bell-fill")


@register.filter
def report_badge(report_type):
    """Badge colour class for a PatientReport.report_type value."""
    return {
        "doctor_visit": "badge-soft-blue",
        "lab": "badge-soft-teal",
        "therapy": "badge-soft-purple",
        "discharge": "badge-soft-amber",
    }.get(report_type, "badge-soft-grey")


@register.filter
def ago(value):
    """Short relative time: 'just now', '3 minutes ago', '2 hours ago'."""
    if not value:
        return ""
    if (timezone.now() - value).total_seconds() < 60:
        return "just now"
    return f"{timesince(value, depth=1)} ago"


@register.filter
def day_label(value):
    """'Today', 'Yesterday' or a full date, in local time."""
    day = timezone.localtime(value).date()
    today = timezone.localdate()
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return date_format(day, "l, j F Y")
