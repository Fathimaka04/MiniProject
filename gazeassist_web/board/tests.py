"""Tests for the caregiver board.

Phase 1: access control, registration/approval, upload validation.
Phase 2: patient tabs, report upload/view/download/delete.
"""
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Caregiver, Patient, PatientCaregiver, PatientReport, Request, SiteSettings
from .validators import validate_report_file

User = get_user_model()
PASSWORD = "Sturdy-Pass-2024"


def make_caregiver(username, approved=True):
    user = User.objects.create_user(username=username, password=PASSWORD, first_name=username.title())
    return Caregiver.objects.create(user=user, is_approved=approved)


class AccessControlTests(TestCase):
    """A caregiver must only ever see the patients linked to them."""

    def setUp(self):
        self.alice = make_caregiver("alice")
        self.bob = make_caregiver("bob")
        self.alice_patient = Patient.objects.create(name="Alice Patient")
        self.bob_patient = Patient.objects.create(name="Bob Patient")
        PatientCaregiver.objects.create(patient=self.alice_patient, caregiver=self.alice)
        PatientCaregiver.objects.create(patient=self.bob_patient, caregiver=self.bob)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("board:dashboard"))
        self.assertRedirects(response, f"{reverse('login')}?next=/")

    def test_dashboard_lists_only_linked_patients(self):
        self.client.login(username="alice", password=PASSWORD)
        response = self.client.get(reverse("board:dashboard"))
        self.assertContains(response, "Alice Patient")
        self.assertNotContains(response, "Bob Patient")

    def test_unlinked_patient_detail_returns_404(self):
        self.client.login(username="alice", password=PASSWORD)
        ok = self.client.get(reverse("board:patient_detail", args=[self.alice_patient.pk]))
        blocked = self.client.get(reverse("board:patient_detail", args=[self.bob_patient.pk]))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(blocked.status_code, 404)


class RegistrationTests(TestCase):
    def _register(self, username="newcarer"):
        return self.client.post(reverse("register"), {
            "first_name": "New", "last_name": "Carer", "email": f"{username}@example.com",
            "phone": "+91 98765 43210", "username": username,
            "password1": PASSWORD, "password2": PASSWORD,
        })

    def test_register_without_approval_logs_in(self):
        response = self._register()
        self.assertRedirects(response, reverse("board:dashboard"))
        self.assertTrue(Caregiver.objects.get(user__username="newcarer").is_approved)

    def test_register_with_approval_blocks_login(self):
        settings_row = SiteSettings.load()
        settings_row.require_caregiver_approval = True
        settings_row.save()

        response = self._register()
        self.assertRedirects(response, reverse("login"))
        caregiver = Caregiver.objects.get(user__username="newcarer")
        self.assertFalse(caregiver.is_approved)

        login = self.client.post(reverse("login"), {"username": "newcarer", "password": PASSWORD})
        self.assertContains(login, "waiting for administrator approval")

    def test_password_is_hashed(self):
        self._register()
        user = User.objects.get(username="newcarer")
        self.assertNotEqual(user.password, PASSWORD)
        self.assertTrue(user.check_password(PASSWORD))

    def test_duplicate_email_rejected(self):
        self._register("first")
        self.client.logout()
        response = self.client.post(reverse("register"), {
            "first_name": "X", "email": "FIRST@example.com", "username": "second",
            "password1": PASSWORD, "password2": PASSWORD,
        })
        self.assertContains(response, "already exists")


class ReportValidatorTests(TestCase):
    def test_accepts_real_pdf(self):
        validate_report_file(SimpleUploadedFile("r.pdf", b"%PDF-1.4 test", content_type="application/pdf"))

    def test_rejects_fake_pdf(self):
        fake = SimpleUploadedFile("r.pdf", b"MZ\x90\x00 not a pdf", content_type="application/pdf")
        with self.assertRaisesMessage(ValidationError, "does not look like"):
            validate_report_file(fake)

    def test_rejects_wrong_extension(self):
        with self.assertRaises(ValidationError):
            validate_report_file(SimpleUploadedFile("r.exe", b"%PDF-", content_type="application/pdf"))

    def test_rejects_mismatched_content_type(self):
        with self.assertRaises(ValidationError):
            validate_report_file(SimpleUploadedFile("r.png", b"\x89PNG\r\n\x1a\n", content_type="text/html"))

    @override_settings(REPORT_MAX_UPLOAD_BYTES=10)
    def test_rejects_large_file(self):
        with self.assertRaisesMessage(ValidationError, "maximum allowed size"):
            validate_report_file(SimpleUploadedFile("r.pdf", b"%PDF-" + b"0" * 20, content_type="application/pdf"))


class PatientModelTests(TestCase):
    def test_online_window(self):
        patient = Patient.objects.create(name="Test Person")
        self.assertFalse(patient.is_online)
        patient.last_seen = timezone.now() - timedelta(seconds=5)
        self.assertTrue(patient.is_online)
        patient.last_seen = timezone.now() - timedelta(seconds=60)
        self.assertFalse(patient.is_online)
        self.assertEqual(patient.initials, "TP")


TEMP_MEDIA = tempfile.mkdtemp(prefix="gazeassist-test-media-")
PDF_BYTES = b"%PDF-1.4\n% test file\n"


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class PatientTabsAndReportsTests(TestCase):
    """Phase 2: every tab and report route must respect the caregiver-patient link."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.primary = make_caregiver("primary")
        self.secondary = make_caregiver("secondary")
        self.outsider = make_caregiver("outsider")
        self.patient = Patient.objects.create(name="Ravi Menon", condition_type=Patient.Condition.ALS)
        PatientCaregiver.objects.create(patient=self.patient, caregiver=self.primary, role="primary")
        PatientCaregiver.objects.create(patient=self.patient, caregiver=self.secondary, role="secondary")
        self.report = PatientReport(
            patient=self.patient, title="Lab panel", report_type="lab", added_by=self.primary,
            original_filename="lab_panel.pdf",
        )
        self.report.file.save("lab_panel.pdf", ContentFile(PDF_BYTES), save=False)
        self.report.save()
        Request.objects.create(patient=self.patient, phrase="I am thirsty")
        Request.objects.create(patient=self.patient, phrase="SOS", is_emergency=True)

    def login(self, caregiver):
        self.client.login(username=caregiver.user.username, password=PASSWORD)

    def tab_urls(self):
        pk = self.patient.pk
        return [
            reverse("board:patient_detail", args=[pk]),
            reverse("board:patient_reports", args=[pk]),
            reverse("board:report_add", args=[pk]),
            reverse("board:patient_activity", args=[pk]),
            reverse("board:patient_analytics", args=[pk]),
        ]

    def test_all_tabs_render_for_linked_caregiver(self):
        self.login(self.secondary)
        for url in self.tab_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_all_tabs_404_for_unlinked_caregiver(self):
        self.login(self.outsider)
        for url in self.tab_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_activity_filter(self):
        self.login(self.primary)
        url = reverse("board:patient_activity", args=[self.patient.pk])
        response = self.client.get(url, {"filter": "emergency"})
        self.assertEqual([r.phrase for r in response.context["requests"]], ["SOS"])

    def test_upload_valid_report(self):
        self.login(self.secondary)
        upload = SimpleUploadedFile("My Scan.pdf", PDF_BYTES, content_type="application/pdf")
        response = self.client.post(reverse("board:report_add", args=[self.patient.pk]), {
            "title": "Neurology", "report_type": "doctor_visit",
            "report_date": date.today().isoformat(), "notes": "", "file": upload,
        })
        self.assertRedirects(response, reverse("board:patient_reports", args=[self.patient.pk]))
        report = PatientReport.objects.get(title="Neurology")
        self.assertEqual(report.added_by, self.secondary)
        self.assertEqual(report.original_filename, "My_Scan.pdf")
        # Stored under a random name, not the uploaded one.
        self.assertNotIn("Scan", report.file.name)
        self.assertTrue(report.file.name.startswith(f"reports/{self.patient.pk}/"))

    def test_upload_rejects_fake_pdf(self):
        self.login(self.primary)
        fake = SimpleUploadedFile("virus.pdf", b"MZ\x90\x00 binary", content_type="application/pdf")
        response = self.client.post(reverse("board:report_add", args=[self.patient.pk]), {
            "title": "Fake", "report_type": "other", "report_date": date.today().isoformat(), "file": fake,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not look like a real PDF")
        self.assertFalse(PatientReport.objects.filter(title="Fake").exists())

    def test_upload_blocked_for_unlinked_caregiver(self):
        self.login(self.outsider)
        upload = SimpleUploadedFile("x.pdf", PDF_BYTES, content_type="application/pdf")
        response = self.client.post(reverse("board:report_add", args=[self.patient.pk]), {
            "title": "Sneaky", "report_type": "other", "report_date": date.today().isoformat(), "file": upload,
        })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(PatientReport.objects.filter(title="Sneaky").exists())

    def test_view_and_download(self):
        self.login(self.secondary)
        view = self.client.get(reverse("board:report_view", args=[self.report.pk]))
        self.assertEqual(view.status_code, 200)
        self.assertEqual(view["Content-Type"], "application/pdf")
        self.assertTrue(view["Content-Disposition"].startswith("inline"))
        self.assertEqual(b"".join(view.streaming_content), PDF_BYTES)

        download = self.client.get(reverse("board:report_download", args=[self.report.pk]))
        self.assertIn('attachment; filename="lab_panel.pdf"', download["Content-Disposition"])

    def test_files_404_for_unlinked_caregiver(self):
        self.login(self.outsider)
        for name in ("board:report_view", "board:report_download", "board:report_delete"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=[self.report.pk])).status_code, 404)

    def test_secondary_cannot_delete_others_report(self):
        self.login(self.secondary)
        response = self.client.post(reverse("board:report_delete", args=[self.report.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(PatientReport.objects.filter(pk=self.report.pk).exists())

    def test_primary_delete_removes_row_and_file(self):
        path = Path(self.report.file.path)
        self.assertTrue(path.exists())
        self.login(self.primary)
        response = self.client.post(reverse("board:report_delete", args=[self.report.pk]))
        self.assertRedirects(response, reverse("board:patient_reports", args=[self.patient.pk]))
        self.assertFalse(PatientReport.objects.filter(pk=self.report.pk).exists())
        self.assertFalse(path.exists())
