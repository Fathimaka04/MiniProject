"""Phase 3 tests: gaze app REST API and the dashboard's live-update endpoints."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .api.tokens import ensure_gaze_token
from .models import Caregiver, CaregiverMessage, Patient, PatientCaregiver, Request

User = get_user_model()
PASSWORD = "Sturdy-Pass-2024"


class GazeAPITests(TestCase):
    def setUp(self):
        cache.clear()  # reset throttle counters
        self.patient = Patient.objects.create(name="Ravi Menon")
        self.other = Patient.objects.create(name="Other Patient")
        self.token = ensure_gaze_token(self.patient)
        self.other_token = ensure_gaze_token(self.other)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token}")

        carer_user = User.objects.create_user("carer", password=PASSWORD, first_name="Anna")
        self.caregiver = Caregiver.objects.create(user=carer_user)
        PatientCaregiver.objects.create(patient=self.patient, caregiver=self.caregiver, role="primary")

    # --- authentication -------------------------------------------------
    def test_requires_token(self):
        anonymous = APIClient()
        for method, url in (
            ("post", reverse("api:heartbeat")),
            ("post", reverse("api:request_create")),
            ("get", reverse("api:messages_pending")),
        ):
            with self.subTest(url=url):
                self.assertEqual(getattr(anonymous, method)(url).status_code, 401)

    def test_bad_token_rejected(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Token not-a-real-token")
        self.assertEqual(client.post(reverse("api:heartbeat")).status_code, 401)

    def test_caregiver_token_rejected(self):
        """A token that doesn't belong to a patient account cannot use the gaze API."""
        key = Token.objects.create(user=self.caregiver.user).key
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {key}")
        self.assertEqual(client.post(reverse("api:heartbeat")).status_code, 403)

    def test_caregiver_session_cannot_use_api(self):
        self.client.credentials()
        self.client.login(username="carer", password=PASSWORD)
        self.assertEqual(self.client.post(reverse("api:heartbeat")).status_code, 401)

    def test_gaze_account_cannot_log_in_to_website(self):
        self.assertFalse(self.patient.user.has_usable_password())

    # --- heartbeat --------------------------------------------------------
    def test_heartbeat_updates_last_seen(self):
        response = self.client.post(reverse("api:heartbeat"), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["patient"]["id"], self.patient.pk)
        self.patient.refresh_from_db()
        self.assertTrue(self.patient.is_online)
        self.other.refresh_from_db()
        self.assertIsNone(self.other.last_seen)

    # --- requests ---------------------------------------------------------
    def test_create_request(self):
        response = self.client.post(
            reverse("api:request_create"),
            {"phrase": "  I am thirsty ", "is_emergency": False},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["phrase"], "I am thirsty")
        self.assertFalse(response.data["acknowledged"])
        request = Request.objects.get(pk=response.data["id"])
        self.assertEqual(request.patient, self.patient)

    def test_create_emergency_with_pain_level(self):
        response = self.client.post(
            reverse("api:request_create"),
            {"phrase": "Pain level 8", "is_emergency": True, "pain_level": 8},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["is_emergency"])
        self.assertEqual(response.data["pain_level"], 8)

    def test_create_request_validation(self):
        url = reverse("api:request_create")
        for body in ({"phrase": "   "}, {}, {"phrase": "x", "pain_level": 11}, {"phrase": "x" * 300}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post(url, body, format="json").status_code, 400)

    def test_status_before_and_after_acknowledge(self):
        req = Request.objects.create(patient=self.patient, phrase="SOS", is_emergency=True)
        url = reverse("api:request_status", args=[req.pk])
        self.assertFalse(self.client.get(url).data["acknowledged"])

        req.acknowledge(self.caregiver)
        data = self.client.get(url).data
        self.assertTrue(data["acknowledged"])
        self.assertEqual(data["acknowledged_by"], "Anna")
        self.assertEqual(data["message"], "Help is on the way")

    def test_status_of_other_patients_request_is_404(self):
        req = Request.objects.create(patient=self.other, phrase="Private")
        self.assertEqual(self.client.get(reverse("api:request_status", args=[req.pk])).status_code, 404)

    # --- messages ---------------------------------------------------------
    def test_pending_messages_delivered_once(self):
        CaregiverMessage.objects.create(patient=self.patient, caregiver=self.caregiver, text="On my way")
        CaregiverMessage.objects.create(patient=self.other, text="Not yours")

        first = self.client.get(reverse("api:messages_pending")).data["messages"]
        self.assertEqual([m["text"] for m in first], ["On my way"])
        self.assertEqual(first[0]["sender"], "Anna")
        self.assertEqual(self.client.get(reverse("api:messages_pending")).data["messages"], [])
        self.assertTrue(CaregiverMessage.objects.get(text="On my way").delivered)
        self.assertFalse(CaregiverMessage.objects.get(text="Not yours").delivered)

    def test_reset_token_revokes_old_one(self):
        new_key = ensure_gaze_token(self.patient, reset=True)
        self.assertNotEqual(new_key, self.token)
        self.assertEqual(self.client.post(reverse("api:heartbeat")).status_code, 401)


class LiveEndpointTests(TestCase):
    def setUp(self):
        self.patient = Patient.objects.create(name="Ravi Menon")
        self.unlinked = Patient.objects.create(name="Someone Else")
        user = User.objects.create_user("carer", password=PASSWORD)
        self.caregiver = Caregiver.objects.create(user=user)
        PatientCaregiver.objects.create(patient=self.patient, caregiver=self.caregiver, role="primary")
        self.old = Request.objects.create(patient=self.patient, phrase="Old one", is_emergency=True)

    def test_anonymous_gets_401_json(self):
        response = self.client.get(reverse("board:live_dashboard"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "signed_out")

    def test_unlinked_patient_feed_is_404(self):
        self.client.login(username="carer", password=PASSWORD)
        url = reverse("board:live_patient_feed", args=[self.unlinked.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_feed_returns_only_new_items(self):
        self.client.login(username="carer", password=PASSWORD)
        new = Request.objects.create(patient=self.patient, phrase="Brand new")
        url = reverse("board:live_patient_feed", args=[self.patient.pk])
        data = self.client.get(url, {"after": self.old.pk}).json()
        self.assertEqual([i["id"] for i in data["items"]], [new.pk])
        self.assertIn("Brand new", data["items"][0]["html"])
        self.assertEqual(data["latest_id"], new.pk)
        self.assertEqual(data["open_alerts"], 1)

    def test_feed_reports_acknowledged_items(self):
        self.client.login(username="carer", password=PASSWORD)
        since = (timezone.now() - timedelta(seconds=1)).isoformat()
        self.old.acknowledge(self.caregiver)
        url = reverse("board:live_patient_feed", args=[self.patient.pk])
        data = self.client.get(url, {"after": self.old.pk, "since": since, "filter": "open"}).json()
        self.assertEqual(data["items"], [])
        self.assertEqual(data["updated"][0]["id"], self.old.pk)
        self.assertFalse(data["updated"][0]["matches_filter"])
        self.assertEqual(data["open_alerts"], 0)

    def test_feed_shows_online_after_heartbeat(self):
        self.client.login(username="carer", password=PASSWORD)
        url = reverse("board:live_patient_feed", args=[self.patient.pk])
        self.assertFalse(self.client.get(url).json()["online"])

        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Token {ensure_gaze_token(self.patient)}")
        api.post(reverse("api:heartbeat"))
        self.assertTrue(self.client.get(url).json()["online"])

    def test_dashboard_live_lists_only_linked_patients(self):
        self.client.login(username="carer", password=PASSWORD)
        data = self.client.get(reverse("board:live_dashboard")).json()
        self.assertEqual([p["id"] for p in data["patients"]], [self.patient.pk])
        self.assertEqual(data["stats"]["unacknowledged"], 1)
        self.assertIn("Old one", data["alerts_html"])
