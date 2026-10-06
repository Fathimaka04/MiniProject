"""Live-update endpoints polled by the caregiver pages with fetch().

These are for the signed-in caregiver's browser (session auth), not for the
gaze app. They return JSON with small pre-rendered HTML fragments, so the
page reuses the exact same templates as a normal page load.

Access rules are the same as for the pages: the patient must be linked to
the caregiver or the response is 404.
"""
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views import View

from .access import CaregiverRequiredMixin, PatientAccessMixin, open_emergencies
from .context_processors import SOS_BANNER_LIMIT
from .views import ACTIVITY_FILTERS, activity_filter, build_dashboard

MAX_NEW_ITEMS = 50


class LiveJSONMixin:
    """Answer 401 JSON (instead of a login redirect) when the session has ended."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "signed_out"}, status=401)
        return super().dispatch(request, *args, **kwargs)


def _render(template, request, **context):
    return render_to_string(template, context, request=request).strip()


def sos_payload(caregiver, request):
    """Open SOS alerts for the red banner shown on every page.

    ``sos_ids`` lets the browser spot *new* alerts (to sound the alarm);
    ``sos_html`` is the banner body, rendered from the same template as a page load.
    """
    alerts = open_emergencies(caregiver)
    count = alerts.count()
    shown = list(alerts[:SOS_BANNER_LIMIT]) if count else []
    return {
        "sos_ids": list(alerts.values_list("pk", flat=True)[:50]),
        "sos_count": count,
        "sos_html": _render(
            "partials/_sos_banner_body.html", request, sos_alerts=shown, sos_count=count
        ),
        "nav_open_alerts": count,
    }


class DashboardLiveView(LiveJSONMixin, CaregiverRequiredMixin, View):
    """GET /live/dashboard/ — summary numbers, patient statuses and open alerts."""

    def get(self, request):
        data = build_dashboard(self.caregiver)
        patients = [
            {
                "id": p.pk,
                "online": p.is_online,
                "unack_alerts": p.unack_alerts,
                "status_html": _render("partials/_status_pill.html", request, patient=p),
                "alerts_html": _render("partials/_patient_card_alerts.html", request, patient=p),
            }
            for p in data["patients"]
        ]
        return JsonResponse({
            "server_time": timezone.now().isoformat(),
            "stats": {
                "total_patients": data["total_patients"],
                "online_now": data["online_now"],
                "emergencies_today": data["emergencies_today"],
                "unacknowledged": data["unacknowledged"],
            },
            "patients": patients,
            "alerts_html": _render(
                "partials/_alerts_list.html", request, recent_alerts=data["recent_alerts"]
            ),
            **sos_payload(self.caregiver, request),
        })


class PatientFeedLiveView(LiveJSONMixin, PatientAccessMixin, View):
    """GET /live/patients/<pk>/feed/?after=<id>&since=<iso>&filter=<name>

    Returns:
      items   – requests newer than ``after`` (newest first), rendered as feed items
      updated – older requests acknowledged since ``since`` (to refresh their state)
      status  – online pill and open-alert count for the header
    """

    def get(self, request, pk):
        try:
            after = max(int(request.GET.get("after", 0)), 0)
        except ValueError:
            after = 0
        since = parse_datetime(request.GET.get("since", "") or "")
        now = timezone.now()

        current_filter = activity_filter(request)
        all_requests = self.patient.requests.select_related("acknowledged_by__user")
        filtered = all_requests.filter(ACTIVITY_FILTERS[current_filter][1])

        new_items = list(filtered.filter(pk__gt=after).order_by("-pk")[:MAX_NEW_ITEMS])
        updated = []
        if since is not None:
            # Unfiltered on purpose: an item acknowledged while the "Unacknowledged"
            # filter is shown must still be reported, so the page can remove it.
            updated = list(all_requests.filter(pk__lte=after, acknowledged_at__gte=since))

        def feed_item(r):
            return {
                "id": r.pk,
                "is_emergency": r.is_emergency,
                "acknowledged": r.is_acknowledged,
                "matches_filter": current_filter != "open" or not r.is_acknowledged,
                "html": _render("partials/_feed_item.html", request, item=r),
            }

        latest = self.patient.requests.order_by("-pk").values_list("pk", flat=True).first() or 0
        open_alerts = self.patient.requests.filter(
            is_emergency=True, acknowledged_at__isnull=True
        ).count()

        return JsonResponse({
            "server_time": now.isoformat(),
            "latest_id": max(latest, after),
            "items": [feed_item(r) for r in new_items],
            "updated": [feed_item(r) for r in updated],
            "online": self.patient.is_online,
            "status_html": _render("partials/_status_pill.html", request, patient=self.patient),
            "open_alerts": open_alerts,
            **sos_payload(self.caregiver, request),
        })


class AlertsLiveView(LiveJSONMixin, CaregiverRequiredMixin, View):
    """GET /live/alerts/ — just the SOS banner, for pages without their own live endpoint."""

    def get(self, request):
        return JsonResponse({"server_time": timezone.now().isoformat(), **sos_payload(self.caregiver, request)})
