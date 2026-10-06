"""API views called by the gaze app.

Every view acts on ``self.patient``, which comes from the token's user, so a
gaze app can only ever read or write its own patient's data.
"""
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from ..models import CaregiverMessage, Patient
from ..pairing import redeem_pairing_code
from .permissions import IsGazeApp
from .serializers import (
    CaregiverMessageSerializer,
    PairDeviceSerializer,
    RequestCreateSerializer,
    RequestStatusSerializer,
)


class GazeAppAPIView(APIView):
    """Base view: token must belong to a patient; sets ``self.patient``."""

    permission_classes = [IsAuthenticated, IsGazeApp]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "gaze"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.patient = request.user.patient_profile

    def mark_seen(self, when=None):
        """Record that the gaze app is alive (single UPDATE, no race with other fields)."""
        Patient.objects.filter(pk=self.patient.pk).update(last_seen=when or timezone.now())


class HeartbeatView(GazeAppAPIView):
    """POST /api/heartbeat/ — the gaze app is running. Updates ``last_seen``."""

    def post(self, request):
        now = timezone.now()
        self.mark_seen(now)
        pending = self.patient.caregiver_messages.filter(delivered=False).count()
        return Response({
            "ok": True,
            "patient": {"id": self.patient.pk, "name": self.patient.name},
            "server_time": now,
            "pending_messages": pending,
            "online_window_seconds": settings.GAZEASSIST_ONLINE_WINDOW_SECONDS,
        })


class RequestCreateView(GazeAppAPIView):
    """POST /api/requests/ — the patient selected a phrase (or SOS).

    Body: {"phrase": "I am thirsty", "is_emergency": false, "pain_level": null}
    """

    def post(self, request):
        serializer = RequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        obj = serializer.save(patient=self.patient)
        self.mark_seen(obj.timestamp)
        return Response(RequestStatusSerializer(obj).data, status=status.HTTP_201_CREATED)


class RequestStatusView(GazeAppAPIView):
    """GET /api/requests/<id>/status/ — has a caregiver acknowledged it yet?"""

    def get(self, request, pk):
        obj = get_object_or_404(
            self.patient.requests.select_related("acknowledged_by__user"), pk=pk
        )
        return Response(RequestStatusSerializer(obj).data)


class PendingMessagesView(GazeAppAPIView):
    """GET /api/messages/pending/ — caregiver messages not yet shown to the patient.

    Returned messages are marked as delivered, so each is returned once.
    """

    MAX_BATCH = 20

    def get(self, request):
        now = timezone.now()
        with transaction.atomic():
            messages = list(
                self.patient.caregiver_messages.select_for_update()
                .filter(delivered=False)
                .select_related("caregiver__user")
                .order_by("created_at")[: self.MAX_BATCH]
            )
            if messages:
                CaregiverMessage.objects.filter(pk__in=[m.pk for m in messages]).update(
                    delivered=True, delivered_at=now
                )
        return Response({"messages": CaregiverMessageSerializer(messages, many=True).data})


class PairDeviceView(APIView):
    """POST /api/pair/ — swap a one-time code for this patient's API token.

    Body: {"code": "482913", "device_name": "LIVINGROOM-PC"}
    No token needed (the code is the credential); rate-limited per IP.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "pair"

    def post(self, request):
        serializer = PairDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = redeem_pairing_code(
            serializer.validated_data["code"], serializer.validated_data.get("device_name", "")
        )
        if result is None:
            return Response(
                {"detail": "This code is wrong, already used or expired. Ask the caregiver for a new code."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        patient, token = result
        return Response({
            "token": token,
            "patient": {"id": patient.pk, "name": patient.name, "language": patient.language},
            "server_time": timezone.now(),
        })
