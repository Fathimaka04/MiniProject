"""Access control for the caregiver board.

The rule: a caregiver may only see or change data of patients they are
linked to through PatientCaregiver. Every view that touches patient data
must build its queryset from ``patients_for(caregiver)`` (or
``reports_for(caregiver)``) so an unlinked patient returns 404, even if
someone types the URL by hand.
"""
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from .models import Caregiver, Patient, PatientCaregiver, PatientReport, Request


def get_caregiver(user):
    """Return the Caregiver profile for `user`, or None."""
    if not user.is_authenticated:
        return None
    try:
        return user.caregiver
    except Caregiver.DoesNotExist:
        return None


def patients_for(caregiver):
    """Queryset of patients the caregiver is allowed to access."""
    return Patient.objects.filter(caregiver_links__caregiver=caregiver)


def reports_for(caregiver):
    """Queryset of reports belonging to the caregiver's patients."""
    return PatientReport.objects.filter(patient__caregiver_links__caregiver=caregiver)


def requests_for(caregiver):
    """Queryset of requests (phrases/SOS) from the caregiver's patients."""
    return Request.objects.filter(patient__caregiver_links__caregiver=caregiver)


def open_emergencies(caregiver):
    """Unacknowledged SOS requests from the caregiver's patients, newest first."""
    return (
        requests_for(caregiver)
        .filter(is_emergency=True, acknowledged_at__isnull=True)
        .select_related("patient")
        .order_by("-timestamp")
    )


def can_delete_report(caregiver, report, link=None):
    """Only the caregiver who uploaded a report, or a primary caregiver, may delete it."""
    if report.added_by_id == caregiver.pk:
        return True
    if link is None:
        link = PatientCaregiver.objects.filter(patient_id=report.patient_id, caregiver=caregiver).first()
    return link is not None and link.role == PatientCaregiver.Role.PRIMARY


class CaregiverRequiredMixin(LoginRequiredMixin):
    """Require a signed-in, approved caregiver. Sets ``self.caregiver``.

    Subclasses can override ``caregiver_ready()`` to run further checks
    (it runs after ``self.caregiver`` is set and before the handler).
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        caregiver = get_caregiver(request.user)
        if caregiver is None:
            if request.user.is_staff:
                messages.info(
                    request,
                    "This admin account has no caregiver profile, so it has no board. "
                    "Use the admin panel, or sign in as a caregiver.",
                )
                return redirect("admin:index")
            logout(request)
            messages.error(request, "Your account is not set up as a caregiver.")
            return redirect("login")

        if not caregiver.is_approved:
            logout(request)
            messages.warning(request, "Your account is waiting for administrator approval.")
            return redirect("login")

        self.caregiver = caregiver
        self.caregiver_ready()
        return super().dispatch(request, *args, **kwargs)

    def caregiver_ready(self):
        """Hook for subclasses; runs once the caregiver is known."""


class PatientAccessMixin(CaregiverRequiredMixin):
    """Load ``self.patient`` from the URL's ``pk`` — 404 unless linked.

    Also provides the header/tab context shared by every patient tab.
    """

    active_tab = "overview"

    def caregiver_ready(self):
        self.patient = get_object_or_404(patients_for(self.caregiver), pk=self.kwargs["pk"])
        self.my_link = self.patient.caregiver_links.get(caregiver=self.caregiver)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            patient=self.patient,
            caregiver=self.caregiver,
            my_link=self.my_link,
            active_tab=self.active_tab,
            reports_count=self.patient.reports.count(),
            open_alerts_count=self.patient.requests.filter(
                is_emergency=True, acknowledged_at__isnull=True
            ).count(),
            # Starting point for live polling (see board/live.py).
            latest_request_id=self.patient.requests.aggregate(m=Max("id"))["m"] or 0,
            live_since=timezone.now().isoformat(),
        )
        return context
