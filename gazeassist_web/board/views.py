"""Views for the GazeAssist caregiver board.

Phase 1: authentication and dashboard.
Phase 2: patient page tabs (overview, reports, activity, analytics).
Phase 4: SOS acknowledgement and messages to the patient.
"""
from collections import OrderedDict
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count, OuterRef, Q, Subquery
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.formats import date_format
from django.utils.text import get_valid_filename
from django.views import View
from django.views.generic import CreateView, DeleteView, FormView, ListView, TemplateView

from .access import (
    CaregiverRequiredMixin,
    PatientAccessMixin,
    can_delete_report,
    patients_for,
    reports_for,
    requests_for,
)
from .forms import (
    QUICK_REPLIES,
    CaregiverLoginForm,
    CaregiverMessageForm,
    CaregiverRegistrationForm,
    PatientForm,
    PatientReportForm,
)
from .models import Patient, PatientCaregiver, PatientReport, Request, SiteSettings
from .pairing import create_pairing_code, disconnect_device


def _start_of_today():
    """Midnight today in the configured local time zone."""
    return timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)


def _format_duration(seconds):
    """Turn seconds into a short label such as '3 min 20 s'."""
    if seconds is None:
        return "—"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {secs} s" if secs else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------
class CaregiverLoginView(LoginView):
    """Split-layout sign-in page. Unapproved caregivers are refused."""

    template_name = "registration/login.html"
    authentication_form = CaregiverLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        user = form.get_user()
        messages.success(self.request, f"Welcome back, {user.get_short_name() or user.username}.")
        return super().form_valid(form)


class CaregiverLogoutView(LogoutView):
    """Log out (POST only, as required by Django 5) and show a toast."""

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        messages.info(request, "You have been signed out.")
        return response


class RegisterView(FormView):
    """Caregiver self-registration.

    If the admin switch ``SiteSettings.require_caregiver_approval`` is on, the
    account is created unapproved and the caregiver must wait for an admin.
    Otherwise the caregiver is signed in straight away.
    """

    template_name = "registration/register.html"
    form_class = CaregiverRegistrationForm
    success_url = reverse_lazy("board:dashboard")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("board:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["require_approval"] = SiteSettings.load().require_caregiver_approval
        return context

    def form_valid(self, form):
        require_approval = SiteSettings.load().require_caregiver_approval
        with transaction.atomic():
            user = form.save(require_approval=require_approval)

        if require_approval:
            messages.info(
                self.request,
                "Account created. An administrator needs to approve it before you can sign in.",
            )
            return redirect("login")

        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(self.request, f"Welcome to GazeAssist, {user.first_name}!")
        return super().form_valid(form)


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
def build_dashboard(caregiver):
    """Data for the dashboard page and its live-update endpoint."""
    today = _start_of_today()
    my_role = PatientCaregiver.objects.filter(
        patient=OuterRef("pk"), caregiver=caregiver
    ).values("role")[:1]

    patients = list(
        patients_for(caregiver)
        .annotate(
            my_role=Subquery(my_role),
            unack_alerts=Count(
                "requests",
                filter=Q(requests__is_emergency=True, requests__acknowledged_at__isnull=True),
                distinct=True,
            ),
            requests_today=Count(
                "requests", filter=Q(requests__timestamp__gte=today), distinct=True
            ),
        )
        .order_by("name")
    )
    alerts = Request.objects.filter(patient_id__in=[p.pk for p in patients], is_emergency=True)
    return {
        "patients": patients,
        "total_patients": len(patients),
        "online_now": sum(1 for p in patients if p.is_online),
        "emergencies_today": alerts.filter(timestamp__gte=today).count(),
        "unacknowledged": sum(p.unack_alerts for p in patients),
        "recent_alerts": alerts.filter(acknowledged_at__isnull=True)
        .select_related("patient")
        .order_by("-timestamp")[:6],
    }


class DashboardView(CaregiverRequiredMixin, TemplateView):
    """Home page: summary cards, the caregiver's patients and open alerts.

    The page then refreshes itself every few seconds from ``DashboardLiveView``.
    """

    template_name = "board/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        hour = timezone.localtime().hour
        context.update(build_dashboard(self.caregiver))
        context.update(
            caregiver=self.caregiver,
            greeting="morning" if hour < 12 else "afternoon" if hour < 17 else "evening",
        )
        return context


# --------------------------------------------------------------------------
# Patient page tabs
# --------------------------------------------------------------------------
class PatientOverviewView(PatientAccessMixin, TemplateView):
    """Overview tab: profile, condition, care team, latest reports and activity."""

    template_name = "board/patient_overview.html"
    active_tab = "overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        patient = self.patient
        context.update(
            requests_today=patient.requests.filter(timestamp__gte=_start_of_today()).count(),
            care_team=patient.caregiver_links.select_related("caregiver__user").order_by("role"),
            latest_reports=patient.reports.select_related("added_by__user")[:3],
            recent_requests=patient.requests.select_related("acknowledged_by__user")[:5],
            message_form=CaregiverMessageForm(),
            quick_replies=QUICK_REPLIES,
            recent_messages=patient.caregiver_messages.select_related("caregiver__user")[:5],
        )
        return context


class PatientReportsView(PatientAccessMixin, ListView):
    """Reports tab: searchable table, filterable by report type."""

    template_name = "board/patient_reports.html"
    context_object_name = "reports"
    active_tab = "reports"

    def get_type_filter(self):
        value = self.request.GET.get("type", "")
        return value if value in PatientReport.ReportType.values else ""

    def get_queryset(self):
        reports = self.patient.reports.select_related("added_by__user")
        type_filter = self.get_type_filter()
        if type_filter:
            reports = reports.filter(report_type=type_filter)
        return reports

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        counts = dict(
            self.patient.reports.values_list("report_type").annotate(n=Count("id")).order_by()
        )
        context["type_filters"] = [
            {"value": value, "label": label, "count": counts.get(value, 0)}
            for value, label in PatientReport.ReportType.choices
        ]
        context["type_filter"] = self.get_type_filter()
        context["is_primary"] = self.my_link.role == PatientCaregiver.Role.PRIMARY
        return context


class ReportCreateView(PatientAccessMixin, CreateView):
    """Upload a new report for the patient."""

    model = PatientReport
    form_class = PatientReportForm
    template_name = "board/report_form.html"
    active_tab = "reports"

    def form_valid(self, form):
        uploaded = form.cleaned_data["file"]
        form.instance.patient = self.patient
        form.instance.added_by = self.caregiver
        form.instance.original_filename = get_valid_filename(Path(uploaded.name).name)[:255]
        response = super().form_valid(form)
        messages.success(self.request, f"Report “{self.object.title}” uploaded.")
        return response

    def form_invalid(self, form):
        messages.error(self.request, "Please fix the highlighted fields.")
        return super().form_invalid(form)

    def get_success_url(self):
        return reverse("board:patient_reports", args=[self.patient.pk])


class ReportFileView(CaregiverRequiredMixin, View):
    """Stream a report file after checking the caregiver is linked to its patient.

    Files are never served from a public URL; this view is the only way in.
    """

    as_attachment = False

    def get(self, request, pk):
        report = get_object_or_404(reports_for(self.caregiver), pk=pk)
        try:
            handle = report.file.open("rb")
        except OSError:
            raise Http404("The file for this report is missing.")
        response = FileResponse(
            handle,
            as_attachment=self.as_attachment,
            filename=report.download_name,
            content_type=report.content_type,
        )
        response["Cache-Control"] = "private, no-store"
        return response


class ReportDeleteView(CaregiverRequiredMixin, DeleteView):
    """Delete a report (uploader or primary caregiver only). The file is removed too."""

    template_name = "board/report_confirm_delete.html"
    context_object_name = "report"

    def get_queryset(self):
        return reports_for(self.caregiver).select_related("patient")

    def get_object(self, queryset=None):
        report = super().get_object(queryset)
        if not can_delete_report(self.caregiver, report):
            raise PermissionDenied("Only the uploader or a primary caregiver can delete this report.")
        return report

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["patient"] = self.object.patient
        return context

    def form_valid(self, form):
        title = self.object.title
        response = super().form_valid(form)
        messages.success(self.request, f"Report “{title}” deleted.")
        return response

    def get_success_url(self):
        return reverse("board:patient_reports", args=[self.object.patient_id])


# Activity filters, shared by the Activity tab and the live feed endpoint.
ACTIVITY_FILTERS = OrderedDict(
    [
        ("all", ("All", Q())),
        ("emergency", ("Emergencies", Q(is_emergency=True))),
        ("open", ("Unacknowledged", Q(is_emergency=True, acknowledged_at__isnull=True))),
        ("pain", ("Pain scale", Q(pain_level__isnull=False))),
    ]
)


def activity_filter(request):
    """The ?filter= value if valid, else 'all'."""
    value = request.GET.get("filter", "all")
    return value if value in ACTIVITY_FILTERS else "all"


class PatientActivityView(PatientAccessMixin, ListView):
    """Activity tab: timeline of requests, filterable and paginated.

    Page 1 updates live (new requests appear at the top without reloading).
    """

    template_name = "board/patient_activity.html"
    context_object_name = "requests"
    paginate_by = 20
    active_tab = "activity"
    FILTERS = ACTIVITY_FILTERS

    def get_filter(self):
        return activity_filter(self.request)

    def get_queryset(self):
        return self.patient.requests.filter(self.FILTERS[self.get_filter()][1]).select_related(
            "acknowledged_by__user"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        counts = self.patient.requests.aggregate(
            **{key: Count("id", filter=q) for key, (_, q) in self.FILTERS.items()}
        )
        context["filters"] = [
            {"value": key, "label": label, "count": counts[key]}
            for key, (label, _) in self.FILTERS.items()
        ]
        context["current_filter"] = self.get_filter()
        return context


class PatientAnalyticsView(PatientAccessMixin, TemplateView):
    """Analytics tab: key numbers plus Chart.js charts for the chosen period.

    Charts: requests per day, emergencies per day, most-used phrases and the
    daily average pain level. All chart data is built here, one entry per
    calendar day in local time (days with no activity are 0, or null for pain),
    and passed to the page with ``json_script``.
    """

    template_name = "board/patient_analytics.html"
    active_tab = "analytics"
    PERIODS = (7, 30, 90)
    TOP_PHRASES = 8

    def get_days(self):
        try:
            days = int(self.request.GET.get("days", 30))
        except ValueError:
            return 30
        return days if days in self.PERIODS else 30

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        days = self.get_days()
        # The period is whole calendar days: today plus the previous days-1 days.
        first_day = timezone.localdate() - timedelta(days=days - 1)
        since = _start_of_today() - timedelta(days=days - 1)
        requests = self.patient.requests.filter(timestamp__gte=since)

        stats = requests.aggregate(
            total=Count("id"),
            emergencies=Count("id", filter=Q(is_emergency=True)),
            avg_pain=Avg("pain_level"),
            pain_reports=Count("pain_level"),
        )

        # Average time to acknowledge an emergency (computed in Python for SQLite).
        ack_pairs = requests.filter(
            is_emergency=True, acknowledged_at__isnull=False
        ).values_list("timestamp", "acknowledged_at")
        waits = [(ack - sent).total_seconds() for sent, ack in ack_pairs]
        avg_response = sum(waits) / len(waits) if waits else None

        # One pass over the period's requests fills every per-day series.
        day_list = [first_day + timedelta(days=i) for i in range(days)]
        index = {day: i for i, day in enumerate(day_list)}
        per_day = [0] * days
        emergencies = [0] * days
        pain_sum = [0] * days
        pain_n = [0] * days
        tod = OrderedDict([("Morning", 0), ("Afternoon", 0), ("Evening", 0), ("Night", 0)])

        for ts, is_emergency, pain in requests.values_list("timestamp", "is_emergency", "pain_level"):
            local = timezone.localtime(ts)
            i = index.get(local.date())
            if i is None:
                continue
            per_day[i] += 1
            if is_emergency:
                emergencies[i] += 1
            if pain is not None:
                pain_sum[i] += pain
                pain_n[i] += 1
            hour = local.hour
            if 6 <= hour < 12:
                tod["Morning"] += 1
            elif 12 <= hour < 18:
                tod["Afternoon"] += 1
            elif hour >= 18:
                tod["Evening"] += 1
            else:
                tod["Night"] += 1

        pain_avg = [round(pain_sum[i] / pain_n[i], 1) if pain_n[i] else None for i in range(days)]

        top_phrases = list(
            requests.values("phrase")
            .annotate(count=Count("id"))
            .order_by("-count", "phrase")[: self.TOP_PHRASES]
        )

        tod_max = max(tod.values()) or 1
        time_of_day = [
            {"label": label, "count": count, "pct": round(count * 100 / tod_max)}
            for label, count in tod.items()
        ]

        chart_data = {
            "days": [d.isoformat() for d in day_list],
            "labels": [date_format(d, "j M") for d in day_list],
            "longLabels": [date_format(d, "D j M Y") for d in day_list],
            "requests": per_day,
            "emergencies": emergencies,
            "painAvg": pain_avg,
            "painCount": pain_n,
            "phrases": [row["phrase"] for row in top_phrases],
            "phraseCounts": [row["count"] for row in top_phrases],
        }

        context.update(
            days=days,
            periods=self.PERIODS,
            stats=stats,
            per_day=round(stats["total"] / days, 1),
            avg_response=_format_duration(avg_response),
            acknowledged_count=len(waits),
            chart_data=chart_data,
            # Rows for the "Show as table" views (same numbers as the charts).
            daily_rows=[
                {
                    "day": d,
                    "requests": per_day[i],
                    "emergencies": emergencies[i],
                    "pain_avg": pain_avg[i],
                    "pain_n": pain_n[i],
                }
                for i, d in reversed(list(enumerate(day_list)))
            ],
            top_phrases=top_phrases,
            busiest_day=max(zip(per_day, day_list)) if stats["total"] else None,
            emergency_days=sum(1 for n in emergencies if n),
            time_of_day=time_of_day,
        )
        return context


# --------------------------------------------------------------------------
# SOS acknowledgement and caregiver messages (Phase 4)
# --------------------------------------------------------------------------
def _wants_json(request):
    return "application/json" in request.headers.get("Accept", "")


def _safe_back_url(request, fallback):
    """The page the form was posted from, if it is on this site; otherwise `fallback`."""
    referer = request.headers.get("Referer", "")
    if referer and url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return referer
    return fallback


class AcknowledgeRequestView(CaregiverRequiredMixin, View):
    """POST /requests/<pk>/acknowledge/ — "I'm handling this".

    Sets acknowledged_by/at so the gaze app's status call returns
    "Help is on the way". Only for requests of linked patients (404 otherwise).
    Answers JSON for fetch() calls, or redirects back for plain form posts.
    """

    def post(self, request, pk):
        obj = get_object_or_404(requests_for(self.caregiver).select_related("patient"), pk=pk)
        did_it = obj.acknowledge(self.caregiver)

        if did_it and obj.is_emergency:
            text = f"Acknowledged. {obj.patient.name} will see “Help is on the way”."
        elif did_it:
            text = f"Marked as seen. {obj.patient.name} will be told you saw it."
        else:
            by = obj.acknowledged_by.display_name if obj.acknowledged_by else "another caregiver"
            text = f"Already acknowledged by {by}."

        if _wants_json(request):
            return JsonResponse({
                "ok": True,
                "id": obj.pk,
                "already": not did_it,
                "acknowledged_by": obj.acknowledged_by.display_name if obj.acknowledged_by else None,
                "message": text,
            })
        (messages.success if did_it else messages.info)(request, text)
        return redirect(_safe_back_url(request, reverse("board:patient_activity", args=[obj.patient_id])))


class SendMessageView(PatientAccessMixin, View):
    """POST /patients/<pk>/messages/ — send a short message to the patient's gaze app."""

    def post(self, request, pk):
        form = CaregiverMessageForm(request.POST)
        back = reverse("board:patient_detail", args=[self.patient.pk]) + "#message"

        if not form.is_valid():
            error = " ".join(form.errors.get("text", ["Please check the message."]))
            if _wants_json(request):
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.error(request, error)
            return redirect(back)

        message = form.save(commit=False)
        message.patient = self.patient
        message.caregiver = self.caregiver
        message.save()
        text = f"Message sent to {self.patient.name}. It appears on their screen within a few seconds."

        if _wants_json(request):
            return JsonResponse({
                "ok": True,
                "message": text,
                "html": render_to_string("partials/_message_item.html", {"message": message}, request=request),
            })
        messages.success(request, text)
        return redirect(back)


# --------------------------------------------------------------------------
# Adding patients and connecting their gaze app (pairing)
# --------------------------------------------------------------------------
class PatientCreateView(CaregiverRequiredMixin, CreateView):
    """Add a patient. The caregiver who adds them becomes their primary caregiver."""

    model = Patient
    form_class = PatientForm
    template_name = "board/patient_form.html"

    @transaction.atomic
    def form_valid(self, form):
        response = super().form_valid(form)
        PatientCaregiver.objects.create(
            patient=self.object, caregiver=self.caregiver, role=PatientCaregiver.Role.PRIMARY
        )
        messages.success(
            self.request,
            f"{self.object.name} added. Next, connect their gaze app with a one-time code.",
        )
        return response

    def form_invalid(self, form):
        messages.error(self.request, "Please fix the highlighted fields.")
        return super().form_invalid(form)

    def get_success_url(self):
        return reverse("board:device_connect", args=[self.object.pk])


class PrimaryCaregiverMixin(PatientAccessMixin):
    """Only a primary caregiver may connect or disconnect the patient's gaze app."""

    def caregiver_ready(self):
        super().caregiver_ready()
        if self.my_link.role != PatientCaregiver.Role.PRIMARY:
            raise PermissionDenied("Only a primary caregiver can connect or disconnect the gaze app.")


class DeviceConnectView(PrimaryCaregiverMixin, TemplateView):
    """GET: explain the steps. POST: create a one-time code and show it."""

    template_name = "board/device_connect.html"
    active_tab = "overview"

    def post(self, request, pk):
        code, pairing = create_pairing_code(self.patient, self.caregiver)
        context = self.get_context_data(
            code=f"{code[:3]} {code[3:]}",
            expires_at=pairing.expires_at,
            ttl_minutes=settings.PAIRING_CODE_TTL_MINUTES,
        )
        response = self.render_to_response(context)
        response["Cache-Control"] = "no-store"   # never cache a page with a live code
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["dashboard_url"] = self.request.build_absolute_uri("/").rstrip("/")
        context["paired_at"] = self.patient.device_paired_at
        return context


class DeviceStatusView(PatientAccessMixin, View):
    """GET JSON: is a gaze app connected? Polled by the code page."""

    def get(self, request, pk):
        patient = self.patient
        return JsonResponse({
            "connected": patient.has_gaze_app,
            "device_name": patient.device_name,
            "paired_at": patient.device_paired_at.isoformat() if patient.device_paired_at else None,
            "online": patient.is_online,
        })


class DeviceDisconnectView(PrimaryCaregiverMixin, View):
    """POST: revoke the gaze app's token (lost or replaced computer)."""

    def post(self, request, pk):
        was_connected = disconnect_device(self.patient)
        if was_connected:
            messages.success(
                request,
                f"{self.patient.name}'s gaze app was disconnected. It can no longer send or receive anything.",
            )
        else:
            messages.info(request, "No gaze app was connected.")
        return redirect(reverse("board:patient_detail", args=[self.patient.pk]) + "#gaze-app")
