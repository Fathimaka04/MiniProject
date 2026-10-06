"""URL routes for the gaze app API (mounted at /api/)."""
from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("pair/", views.PairDeviceView.as_view(), name="pair"),
    path("heartbeat/", views.HeartbeatView.as_view(), name="heartbeat"),
    path("requests/", views.RequestCreateView.as_view(), name="request_create"),
    path("requests/<int:pk>/status/", views.RequestStatusView.as_view(), name="request_status"),
    path("messages/pending/", views.PendingMessagesView.as_view(), name="messages_pending"),
]
