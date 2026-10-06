"""Create demo data: 1 caregiver, 2 patients, sample reports and 50 requests.

Usage:
    python manage.py seed_demo
    python manage.py seed_demo --password "MyDemoPass123"

Running it again wipes the previous demo rows (only those it created) and
recreates them, so it is safe to repeat.
"""
import random
import struct
import zlib
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from board.api.tokens import ensure_gaze_token
from board.models import (
    Caregiver,
    CaregiverMessage,
    Patient,
    PatientCaregiver,
    PatientReport,
    Request,
)

DEMO_USERNAME = "demo_caregiver"

DEMO_PATIENTS = [
    {
        "name": "Ravi Menon",
        "date_of_birth": date(1968, 4, 12),
        "gender": Patient.Gender.MALE,
        "language": "Malayalam",
        "condition_type": Patient.Condition.ALS,
        "condition_notes": "Diagnosed 2021. Limited hand movement; reliable eye control. "
        "Uses gaze board for daily needs. Tires after 30 minutes of use.",
    },
    {
        "name": "Meera Nair",
        "date_of_birth": date(1994, 9, 3),
        "gender": Patient.Gender.FEMALE,
        "language": "English",
        "condition_type": Patient.Condition.CEREBRAL_PALSY,
        "condition_notes": "Spastic quadriplegia. Good head control; prefers larger targets. "
        "Weekly physiotherapy on Tuesdays.",
    },
]

NORMAL_PHRASES = [
    "I am thirsty", "I am hungry", "I need the bathroom", "Please adjust my pillow",
    "I am cold", "I am hot", "Turn on the TV", "I want to rest", "Call my family",
    "Yes", "No", "Thank you", "Please read to me", "Open the window",
]
EMERGENCY_PHRASES = ["SOS – I need help now", "I can't breathe", "Severe pain"]


def _pdf_bytes(title, lines):
    """Build a small, valid one-page PDF without extra libraries."""
    def esc(text):
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    ops = ["BT", "/F1 20 Tf", "72 740 Td", f"({esc(title)}) Tj", "/F1 12 Tf"]
    for line in lines:
        ops += ["0 -24 Td", f"({esc(line)}) Tj"]
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref_at,
    )
    return bytes(out)


def _png_bytes(width=240, height=160, rgb=(20, 184, 166)):
    """Build a plain-colour PNG without Pillow."""
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    row = b"\x00" + bytes(rgb) * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


class Command(BaseCommand):
    help = "Seed demo data: 1 caregiver, 2 patients, sample reports and 50 fake requests."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password", default="Demo@12345", help="Password for the demo caregiver."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(42)
        now = timezone.now()
        User = get_user_model()

        # 1. Remove previous demo rows (report files are deleted by a signal),
        #    including the patients' gaze-app API accounts and tokens.
        old_patients = Patient.objects.filter(name__in=[p["name"] for p in DEMO_PATIENTS])
        old_gaze_users = list(old_patients.exclude(user=None).values_list("user_id", flat=True))
        old_patients.delete()
        User.objects.filter(pk__in=old_gaze_users).delete()
        User.objects.filter(username=DEMO_USERNAME).delete()

        # 2. Caregiver.
        user = User.objects.create_user(
            username=DEMO_USERNAME,
            password=options["password"],
            first_name="Anna",
            last_name="Joseph",
            email="demo.caregiver@example.com",
        )
        caregiver = Caregiver.objects.create(
            user=user, phone="+91 98765 43210", is_primary=True, is_approved=True
        )

        # 3. Patients, linked to the caregiver.
        patients = []
        for index, data in enumerate(DEMO_PATIENTS):
            patient = Patient.objects.create(**data)
            PatientCaregiver.objects.create(
                patient=patient,
                caregiver=caregiver,
                role=PatientCaregiver.Role.PRIMARY if index == 0 else PatientCaregiver.Role.SECONDARY,
            )
            patients.append(patient)
        ravi, meera = patients
        ravi.last_seen = now - timedelta(minutes=3)
        ravi.save(update_fields=["last_seen"])

        # 4. Reports.
        reports = [
            (ravi, PatientReport.ReportType.DOCTOR_VISIT, "Neurology review", 20, "pdf",
             ["Neurology outpatient review", "ALSFRS-R score: 31", "Plan: continue riluzole."]),
            (ravi, PatientReport.ReportType.LAB, "Blood panel", 9, "pdf",
             ["Complete blood count: within normal limits", "Vitamin D: slightly low"]),
            (meera, PatientReport.ReportType.THERAPY, "Physiotherapy progress", 5, "png", None),
        ]
        for patient, rtype, title, days_ago, ext, lines in reports:
            report = PatientReport(
                patient=patient,
                report_type=rtype,
                title=title,
                report_date=date.today() - timedelta(days=days_ago),
                notes="Demo report generated by seed_demo.",
                added_by=caregiver,
                original_filename=f"{title.lower().replace(' ', '_')}.{ext}",
            )
            content = _pdf_bytes(title, lines) if ext == "pdf" else _png_bytes()
            report.file.save(report.original_filename, ContentFile(content), save=False)
            report.save()

        # 5. Fifty requests over the last 14 days.
        requests = []
        for _ in range(50):
            patient = rng.choice(patients)
            timestamp = now - timedelta(
                days=rng.randint(0, 13), hours=rng.randint(0, 12), minutes=rng.randint(0, 59)
            )
            roll = rng.random()
            if roll < 0.12:
                phrase, emergency, pain = rng.choice(EMERGENCY_PHRASES), True, None
            elif roll < 0.35:
                pain = rng.randint(1, 9)
                phrase, emergency = f"Pain level {pain}", False
            else:
                phrase, emergency, pain = rng.choice(NORMAL_PHRASES), False, None
            requests.append(
                Request(
                    patient=patient,
                    phrase=phrase,
                    is_emergency=emergency,
                    pain_level=pain,
                    timestamp=timestamp,
                    acknowledged_by=caregiver,
                    acknowledged_at=timestamp + timedelta(minutes=rng.randint(1, 6)),
                )
            )
        # Two open emergencies today so the alert cards have something to show.
        for patient, minutes_ago in ((ravi, 4), (meera, 25)):
            requests.append(
                Request(
                    patient=patient,
                    phrase="SOS – I need help now",
                    is_emergency=True,
                    timestamp=now - timedelta(minutes=minutes_ago),
                )
            )
            requests.pop(0)  # keep the total at exactly 50
        Request.objects.bulk_create(requests)

        # 6. A couple of caregiver messages.
        sent = CaregiverMessage.objects.create(
            patient=ravi, caregiver=caregiver, text="I'll be there in 5 minutes.", delivered=True,
            delivered_at=now - timedelta(hours=2),
        )
        # created_at is auto_now_add, so backdate it with an update.
        CaregiverMessage.objects.filter(pk=sent.pk).update(
            created_at=now - timedelta(hours=2, seconds=5)
        )
        CaregiverMessage.objects.create(
            patient=meera, caregiver=caregiver, text="Physio is at 4 pm today."
        )

        self.stdout.write(self.style.SUCCESS("Demo data created."))
        self.stdout.write(f"  Caregiver login : {DEMO_USERNAME} / {options['password']}")
        self.stdout.write(f"  Patients        : {', '.join(p.name for p in patients)}")
        self.stdout.write(f"  Reports         : {PatientReport.objects.filter(patient__in=patients).count()}")
        self.stdout.write(f"  Requests        : {Request.objects.filter(patient__in=patients).count()}")

        # 7. Gaze app API tokens (one per patient).
        self.stdout.write("  Gaze app tokens :")
        for patient in patients:
            key = ensure_gaze_token(patient)
            patient.device_name = "Demo setup (seed_demo)"
            patient.device_paired_at = now
            patient.save(update_fields=["device_name", "device_paired_at"])
            self.stdout.write(f"    {patient.name} (id {patient.pk}): {key}")
