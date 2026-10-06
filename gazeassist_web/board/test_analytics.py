"""Phase 5 tests: analytics chart data."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Caregiver, Patient, PatientCaregiver, Request

User = get_user_model()
PASSWORD = "Sturdy-Pass-2024"


class AnalyticsTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("carer", password=PASSWORD)
        caregiver = Caregiver.objects.create(user=user)
        self.patient = Patient.objects.create(name="Ravi Menon")
        PatientCaregiver.objects.create(patient=self.patient, caregiver=caregiver)
        self.client.login(username="carer", password=PASSWORD)
        self.url = reverse("board:patient_analytics", args=[self.patient.pk])
        self.midnight = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)

    def add(self, when, phrase="I am thirsty", **kwargs):
        return Request.objects.create(patient=self.patient, phrase=phrase, timestamp=when, **kwargs)

    def test_empty_period_shows_empty_state_without_chart_library(self):
        response = self.client.get(self.url)
        self.assertContains(response, "No activity in this period")
        self.assertNotContains(response, "chart.umd.min.js")

    def test_one_entry_per_day_zero_filled(self):
        self.add(self.midnight + timedelta(minutes=30))
        for days in (7, 30, 90):
            with self.subTest(days=days):
                data = self.client.get(self.url, {"days": days}).context["chart_data"]
                self.assertEqual(len(data["days"]), days)
                self.assertEqual(data["days"][-1], timezone.localdate().isoformat())
                self.assertEqual(sum(data["requests"]), 1)
                self.assertEqual(data["requests"][-1], 1)

    def test_invalid_period_falls_back_to_30(self):
        self.add(self.midnight + timedelta(hours=1))
        for value in ("abc", "12", "-1"):
            with self.subTest(value=value):
                self.assertEqual(self.client.get(self.url, {"days": value}).context["days"], 30)

    def test_days_use_local_time(self):
        # 23:30 local yesterday belongs to yesterday, even though in UTC it may be "today".
        self.add(self.midnight - timedelta(minutes=30))
        self.add(self.midnight + timedelta(minutes=5))
        data = self.client.get(self.url, {"days": 7}).context["chart_data"]
        self.assertEqual(data["requests"][-2:], [1, 1])

    def test_period_boundary(self):
        self.add(self.midnight - timedelta(days=6, hours=-1))   # first day of a 7-day period: counted
        self.add(self.midnight - timedelta(days=7, hours=-1))   # 8 days ago: excluded
        context = self.client.get(self.url, {"days": 7}).context
        self.assertEqual(context["stats"]["total"], 1)
        self.assertEqual(context["chart_data"]["requests"][0], 1)

    def test_emergencies_and_pain_average(self):
        t = self.midnight + timedelta(hours=2)
        self.add(t, "SOS", is_emergency=True)
        self.add(t, "Pain level 4", pain_level=4)
        self.add(t, "Pain level 7", pain_level=7)
        data = self.client.get(self.url, {"days": 7}).context["chart_data"]
        self.assertEqual(data["emergencies"][-1], 1)
        self.assertEqual(data["painAvg"][-1], 5.5)
        self.assertEqual(data["painCount"][-1], 2)
        self.assertIsNone(data["painAvg"][0])  # no reports that day -> gap, not 0

    def test_top_phrases_order_and_json_on_page(self):
        t = self.midnight + timedelta(hours=3)
        for _ in range(3):
            self.add(t, "Water")
        self.add(t, "Blanket")
        response = self.client.get(self.url, {"days": 7})
        data = response.context["chart_data"]
        self.assertEqual(data["phrases"], ["Water", "Blanket"])
        self.assertEqual(data["phraseCounts"], [3, 1])
        self.assertContains(response, 'id="analytics-data"')
        self.assertContains(response, "chart.umd.min.js")
        self.assertContains(response, "Show as table")
