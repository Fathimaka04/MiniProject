"""Template context shared by every page (sidebar data, SOS banner, polling interval)."""
from django.conf import settings

from .access import get_caregiver, open_emergencies

SOS_BANNER_LIMIT = 3


def caregiver_nav(request):
    """Expose the current caregiver, their open SOS alerts and the poll interval."""
    user = getattr(request, "user", None)
    caregiver = get_caregiver(user) if user is not None else None
    if caregiver is None or not caregiver.is_approved:
        return {}
    alerts = open_emergencies(caregiver)
    count = alerts.count()
    return {
        "nav_caregiver": caregiver,
        "nav_open_alerts": count,
        "sos_alerts": list(alerts[:SOS_BANNER_LIMIT]) if count else [],
        "sos_count": count,
        "poll_interval_ms": settings.GAZEASSIST_POLL_INTERVAL_MS,
    }
