"""URL routes for the caregiver board."""
from django.urls import path

from . import live, views

app_name = "board"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),

    # Patient page tabs
    path("patients/add/", views.PatientCreateView.as_view(), name="patient_add"),
    path("patients/<int:pk>/", views.PatientOverviewView.as_view(), name="patient_detail"),
    path("patients/<int:pk>/reports/", views.PatientReportsView.as_view(), name="patient_reports"),
    path("patients/<int:pk>/reports/add/", views.ReportCreateView.as_view(), name="report_add"),
    path("patients/<int:pk>/activity/", views.PatientActivityView.as_view(), name="patient_activity"),
    path("patients/<int:pk>/analytics/", views.PatientAnalyticsView.as_view(), name="patient_analytics"),

    # Report files (permission-checked, never public)
    path("reports/<int:pk>/view/", views.ReportFileView.as_view(), name="report_view"),
    path(
        "reports/<int:pk>/download/",
        views.ReportFileView.as_view(as_attachment=True),
        name="report_download",
    ),
    path("reports/<int:pk>/delete/", views.ReportDeleteView.as_view(), name="report_delete"),

    # Connecting the patient's gaze app (one-time code)
    path("patients/<int:pk>/gaze-app/connect/", views.DeviceConnectView.as_view(), name="device_connect"),
    path("patients/<int:pk>/gaze-app/status/", views.DeviceStatusView.as_view(), name="device_status"),
    path("patients/<int:pk>/gaze-app/disconnect/", views.DeviceDisconnectView.as_view(), name="device_disconnect"),

    # SOS acknowledgement and messages to the patient
    path("requests/<int:pk>/acknowledge/", views.AcknowledgeRequestView.as_view(), name="request_ack"),
    path("patients/<int:pk>/messages/", views.SendMessageView.as_view(), name="message_send"),

    # Live updates polled by the pages (JSON, session auth)
    path("live/dashboard/", live.DashboardLiveView.as_view(), name="live_dashboard"),
    path("live/patients/<int:pk>/feed/", live.PatientFeedLiveView.as_view(), name="live_patient_feed"),
    path("live/alerts/", live.AlertsLiveView.as_view(), name="live_alerts"),
]
