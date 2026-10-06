"""Phase 4 tests: SOS banner, acknowledging requests and messaging the patient."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .api.tokens import ensure_gaze_token
from .models import Caregiver, CaregiverMessage, Patient, PatientCaregiver, Request

User = get_user_model()
PASSWORD = "Sturdy-Pass-2024"
JSON = {"HTTP_ACCEPT": "application/json"}


def make_caregiver(username, first_name=""):
    user = User.objects.create_user(username, password=PASSWORD, first_name=first_name)
    return Caregiver.objects.create(user=user)


class SosFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.anna = make_caregiver("anna", "Anna")
        self.ben = make_caregiver("ben", "Ben")
        self.outsider = make_caregiver("outsider")
        self.patient = Patient.objects.create(name="Ravi Menon")
        self.other = Patient.objects.create(name="Other Patient")
        for carer in (self.anna, self.ben):
            PatientCaregiver.objects.create(patient=self.patient, caregiver=carer)
        PatientCaregiver.objects.create(patient=self.other, caregiver=self.outsider)
        self.sos = Request.objects.create(patient=self.patient, phrase="SOS – help", is_emergency=True)
        self.ack_url = reverse("board:request_ack", args=[self.sos.pk])

    def login(self, username):
        self.client.login(username=username, password=PASSWORD)

    # --- banner -----------------------------------------------------------
    def test_banner_shown_on_every_page_while_sos_open(self):
        self.login("anna")
        for url in (reverse("board:dashboard"), reverse("board:patient_reports", args=[self.patient.pk])):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, "data-sos-banner")
                self.assertContains(response, 'data-sos-id="%d"' % self.sos.pk)

    def test_banner_hidden_for_unlinked_caregiver(self):
        self.login("outsider")
        self.assertNotContains(self.client.get(reverse("board:dashboard")), 'data-sos-id="%d"' % self.sos.pk)

    def test_live_alerts_endpoint(self):
        self.login("anna")
        data = self.client.get(reverse("board:live_alerts")).json()
        self.assertEqual(data["sos_ids"], [self.sos.pk])
        self.assertEqual(data["sos_count"], 1)
        self.assertIn("Ravi Menon", data["sos_html"])

    # --- acknowledge ------------------------------------------------------
    def test_acknowledge_json(self):
        self.login("anna")
        data = self.client.post(self.ack_url, **JSON).json()
        self.assertFalse(data["already"])
        self.assertEqual(data["acknowledged_by"], "Anna")
        self.sos.refresh_from_db()
        self.assertEqual(self.sos.acknowledged_by, self.anna)
        self.assertIsNotNone(self.sos.acknowledged_at)
        self.assertEqual(self.client.get(reverse("board:live_alerts")).json()["sos_count"], 0)

    def test_acknowledge_plain_form_redirects_back(self):
        self.login("anna")
        back = reverse("board:dashboard")
        response = self.client.post(self.ack_url, HTTP_REFERER=f"http://testserver{back}")
        self.assertRedirects(response, f"http://testserver{back}", fetch_redirect_response=False)

    def test_acknowledge_ignores_offsite_referer(self):
        self.login("anna")
        response = self.client.post(self.ack_url, HTTP_REFERER="https://evil.example.com/")
        self.assertRedirects(
            response, reverse("board:patient_activity", args=[self.patient.pk]), fetch_redirect_response=False
        )

    def test_second_caregiver_sees_already_acknowledged(self):
        self.login("anna")
        self.client.post(self.ack_url, **JSON)
        self.client.logout()
        self.login("ben")
        data = self.client.post(self.ack_url, **JSON).json()
        self.assertTrue(data["already"])
        self.assertIn("Already acknowledged by Anna", data["message"])
        self.sos.refresh_from_db()
        self.assertEqual(self.sos.acknowledged_by, self.anna)  # first one wins

    def test_acknowledge_unlinked_is_404(self):
        self.login("outsider")
        self.assertEqual(self.client.post(self.ack_url, **JSON).status_code, 404)
        self.sos.refresh_from_db()
        self.assertIsNone(self.sos.acknowledged_at)

    def test_acknowledge_requires_post_and_login(self):
        self.assertEqual(self.client.post(self.ack_url).status_code, 302)  # to login
        self.login("anna")
        self.assertEqual(self.client.get(self.ack_url).status_code, 405)

    def test_acknowledge_requires_csrf(self):
        client = self.client_class(enforce_csrf_checks=True)
        client.login(username="anna", password=PASSWORD)
        self.assertEqual(client.post(self.ack_url, **JSON).status_code, 403)

    def test_gaze_app_sees_help_is_on_the_way(self):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Token {ensure_gaze_token(self.patient)}")
        status_url = reverse("api:request_status", args=[self.sos.pk])
        self.assertEqual(api.get(status_url).data["message"], "Waiting for a caregiver")
        self.login("anna")
        self.client.post(self.ack_url, **JSON)
        self.assertEqual(api.get(status_url).data["message"], "Help is on the way")

    def test_normal_request_can_be_marked_seen(self):
        normal = Request.objects.create(patient=self.patient, phrase="I am thirsty")
        self.login("ben")
        data = self.client.post(reverse("board:request_ack", args=[normal.pk]), **JSON).json()
        self.assertIn("Marked as seen", data["message"])

    # --- messages ---------------------------------------------------------
    def test_send_message_json_and_gaze_app_receives_it(self):
        self.login("anna")
        url = reverse("board:message_send", args=[self.patient.pk])
        data = self.client.post(url, {"text": "  I'm   on my way. "}, **JSON).json()
        self.assertTrue(data["ok"])
        self.assertIn("on my way", data["html"])
        msg = CaregiverMessage.objects.get()
        self.assertEqual(msg.text, "I'm on my way.")
        self.assertEqual(msg.caregiver, self.anna)

        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Token {ensure_gaze_token(self.patient)}")
        received = api.get(reverse("api:messages_pending")).data["messages"]
        self.assertEqual([m["text"] for m in received], ["I'm on my way."])

    def test_send_message_validation(self):
        self.login("anna")
        url = reverse("board:message_send", args=[self.patient.pk])
        for text in ("   ", "x" * 281):
            with self.subTest(length=len(text)):
                response = self.client.post(url, {"text": text}, **JSON)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json()["ok"])
        self.assertFalse(CaregiverMessage.objects.exists())

    def test_send_message_plain_form_redirects(self):
        self.login("anna")
        url = reverse("board:message_send", args=[self.patient.pk])
        response = self.client.post(url, {"text": "Hello"})
        self.assertRedirects(response, reverse("board:patient_detail", args=[self.patient.pk]) + "#message")

    def test_send_message_unlinked_is_404(self):
        self.login("outsider")
        url = reverse("board:message_send", args=[self.patient.pk])
        self.assertEqual(self.client.post(url, {"text": "Hi"}).status_code, 404)
        self.assertFalse(CaregiverMessage.objects.exists())
