"""Tests: caregivers add patients and connect the gaze app with a one-time code."""
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Caregiver, DevicePairing, Patient, PatientCaregiver
from .pairing import create_pairing_code

User = get_user_model()
PASSWORD = "Sturdy-Pass-2024"


def make_caregiver(username):
    user = User.objects.create_user(username, password=PASSWORD, first_name=username.title())
    return Caregiver.objects.create(user=user)


class AddPatientTests(TestCase):
    def setUp(self):
        self.carer = make_caregiver("anna")
        self.client.login(username="anna", password=PASSWORD)

    def test_add_patient_makes_you_primary_and_goes_to_connect(self):
        response = self.client.post(reverse("board:patient_add"), {
            "name": "Leela Thomas", "gender": "female", "language": "Malayalam",
            "condition_type": "stroke", "condition_notes": "",
        })
        patient = Patient.objects.get(name="Leela Thomas")
        self.assertRedirects(response, reverse("board:device_connect", args=[patient.pk]))
        link = PatientCaregiver.objects.get(patient=patient)
        self.assertEqual((link.caregiver, link.role), (self.carer, "primary"))

    def test_add_patient_validation(self):
        response = self.client.post(reverse("board:patient_add"), {
            "name": "", "gender": "female", "language": "English", "condition_type": "als",
            "date_of_birth": (timezone.localdate() + timedelta(days=3)).isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")
        self.assertContains(response, "cannot be in the future")
        self.assertFalse(Patient.objects.exists())


@override_settings(PAIRING_CODE_TTL_MINUTES=10)
class PairingTests(TestCase):
    def setUp(self):
        cache.clear()  # reset rate limits
        self.primary = make_caregiver("anna")
        self.secondary = make_caregiver("ben")
        self.outsider = make_caregiver("zoe")
        self.patient = Patient.objects.create(name="Ravi Menon")
        PatientCaregiver.objects.create(patient=self.patient, caregiver=self.primary, role="primary")
        PatientCaregiver.objects.create(patient=self.patient, caregiver=self.secondary, role="secondary")
        self.connect_url = reverse("board:device_connect", args=[self.patient.pk])
        self.api = APIClient()

    def get_code(self):
        self.client.login(username="anna", password=PASSWORD)
        response = self.client.post(self.connect_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        return re.search(r'class="pairing-code"[^>]*>(\d{3} \d{3})<', response.content.decode()).group(1)

    def pair(self, code, device="WARD-PC"):
        return self.api.post(reverse("api:pair"), {"code": code, "device_name": device}, format="json")

    # --- caregiver side -------------------------------------------------
    def test_only_primary_caregiver_can_connect(self):
        self.client.login(username="ben", password=PASSWORD)
        self.assertEqual(self.client.post(self.connect_url).status_code, 403)
        self.client.login(username="zoe", password=PASSWORD)
        self.assertEqual(self.client.post(self.connect_url).status_code, 404)

    def test_code_is_not_stored_in_readable_form(self):
        code = self.get_code().replace(" ", "")
        pairing = DevicePairing.objects.get()
        self.assertNotIn(code, pairing.code_hash)
        self.assertEqual(len(pairing.code_hash), 64)

    # --- gaze app side --------------------------------------------------
    def test_full_flow_code_to_working_token(self):
        code = self.get_code()
        response = self.pair(code)            # spaces are fine: "482 913"
        self.assertEqual(response.status_code, 200)
        token = response.data["token"]
        self.assertEqual(response.data["patient"]["name"], "Ravi Menon")

        self.api.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(self.api.post(reverse("api:heartbeat")).status_code, 200)

        self.patient.refresh_from_db()
        self.assertEqual(self.patient.device_name, "WARD-PC")
        self.assertTrue(self.patient.has_gaze_app)
        status = self.client.get(reverse("board:device_status", args=[self.patient.pk])).json()
        self.assertTrue(status["connected"])

    def test_code_works_only_once(self):
        code = self.get_code()
        self.assertEqual(self.pair(code).status_code, 200)
        self.assertEqual(self.pair(code, "OTHER-PC").status_code, 400)

    def test_wrong_and_expired_codes_rejected(self):
        self.assertEqual(self.pair("000000").status_code, 400)
        self.assertEqual(self.pair("12").status_code, 400)
        code, pairing = create_pairing_code(self.patient, self.primary)
        DevicePairing.objects.filter(pk=pairing.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.pair(code).status_code, 400)

    def test_new_code_cancels_previous_code(self):
        old_code, _ = create_pairing_code(self.patient, self.primary)
        new_code, _ = create_pairing_code(self.patient, self.primary)
        self.assertEqual(self.pair(old_code).status_code, 400)
        self.assertEqual(self.pair(new_code).status_code, 200)

    def test_new_computer_disconnects_the_old_one(self):
        first = self.pair(create_pairing_code(self.patient, self.primary)[0], "OLD-PC").data["token"]
        second = self.pair(create_pairing_code(self.patient, self.primary)[0], "NEW-PC").data["token"]
        self.assertNotEqual(first, second)
        self.api.credentials(HTTP_AUTHORIZATION=f"Token {first}")
        self.assertEqual(self.api.post(reverse("api:heartbeat")).status_code, 401)

    def test_disconnect_revokes_token(self):
        token = self.pair(create_pairing_code(self.patient, self.primary)[0]).data["token"]
        self.client.login(username="anna", password=PASSWORD)
        response = self.client.post(reverse("board:device_disconnect", args=[self.patient.pk]))
        self.assertRedirects(response, reverse("board:patient_detail", args=[self.patient.pk]) + "#gaze-app")
        self.api.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(self.api.post(reverse("api:heartbeat")).status_code, 401)
        self.patient.refresh_from_db()
        self.assertFalse(self.patient.has_gaze_app)

    def test_secondary_cannot_disconnect(self):
        self.client.login(username="ben", password=PASSWORD)
        url = reverse("board:device_disconnect", args=[self.patient.pk])
        self.assertEqual(self.client.post(url).status_code, 403)

    def test_pairing_is_rate_limited(self):
        statuses = [self.pair(f"{n:06d}").status_code for n in range(11)]
        self.assertEqual(statuses[:10], [400] * 10)
        self.assertEqual(statuses[10], 429)

    def test_stale_token_header_does_not_block_pairing(self):
        self.api.credentials(HTTP_AUTHORIZATION="Token revoked-old-token")
        self.assertEqual(self.pair(create_pairing_code(self.patient, self.primary)[0]).status_code, 200)

    def test_overview_shows_gaze_app_card(self):
        self.client.login(username="anna", password=PASSWORD)
        page = self.client.get(reverse("board:patient_detail", args=[self.patient.pk]))
        self.assertContains(page, "Connect gaze app")
        self.client.login(username="ben", password=PASSWORD)
        page = self.client.get(reverse("board:patient_detail", args=[self.patient.pk]))
        self.assertContains(page, "Only a primary caregiver")
