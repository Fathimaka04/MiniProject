"""Data models for the GazeAssist caregiver board."""
import uuid
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from .validators import validate_report_file

phone_validator = RegexValidator(
    regex=r"^\+?[0-9 ()-]{7,20}$",
    message="Enter a valid phone number, e.g. +91 98765 43210.",
)


class SiteSettings(models.Model):
    """Singleton row of admin-editable switches for the dashboard.

    Always stored with pk=1; use ``SiteSettings.load()`` to read it.
    """

    require_caregiver_approval = models.BooleanField(
        default=False,
        help_text="If on, self-registered caregivers cannot sign in until an admin approves them.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "site settings"
        verbose_name_plural = "site settings"

    def __str__(self):
        return "Dashboard settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        """Return the single settings row, creating it with defaults if needed."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Patient(models.Model):
    """A person who communicates through the GazeAssist eye-gaze app."""

    class Gender(models.TextChoices):
        FEMALE = "female", "Female"
        MALE = "male", "Male"
        OTHER = "other", "Other"
        UNDISCLOSED = "undisclosed", "Prefer not to say"

    class Condition(models.TextChoices):
        ALS = "als", "ALS"
        CEREBRAL_PALSY = "cerebral_palsy", "Cerebral Palsy"
        STROKE = "stroke", "Stroke"
        SPINAL_CORD_INJURY = "spinal_cord_injury", "Spinal Cord Injury"
        LOCKED_IN = "locked_in", "Locked-in Syndrome"
        MUSCULAR_DYSTROPHY = "muscular_dystrophy", "Muscular Dystrophy"
        PARKINSONS = "parkinsons", "Parkinson's"
        OTHER = "other", "Other"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="patient_profile",
        help_text="Account used by the gaze app to call the API (owner of the API token).",
    )
    name = models.CharField(max_length=120)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, choices=Gender.choices, default=Gender.UNDISCLOSED)
    language = models.CharField(max_length=50, default="English")
    condition_type = models.CharField(max_length=30, choices=Condition.choices, default=Condition.OTHER)
    condition_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(
        null=True, blank=True, help_text="Updated by the gaze app heartbeat."
    )
    device_name = models.CharField(
        max_length=100, blank=True, help_text="Computer the gaze app was connected from (set when pairing)."
    )
    device_paired_at = models.DateTimeField(null=True, blank=True)
    caregivers = models.ManyToManyField(
        "Caregiver", through="PatientCaregiver", related_name="patients", blank=True
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def age(self):
        """Age in whole years, or None if the date of birth is unknown."""
        if not self.date_of_birth:
            return None
        today = date.today()
        dob = self.date_of_birth
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    @property
    def initials(self):
        """Up to two initials for the avatar, e.g. 'Ravi Menon' -> 'RM'."""
        parts = [p for p in self.name.split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    @property
    def avatar_hue(self):
        """A stable colour hue (0-359) so each patient keeps the same avatar colour."""
        return (self.pk or 0) * 67 % 360

    @property
    def is_online(self):
        """True if the gaze app sent a heartbeat within the online window."""
        if not self.last_seen:
            return False
        window = timedelta(seconds=settings.GAZEASSIST_ONLINE_WINDOW_SECONDS)
        return timezone.now() - self.last_seen <= window

    @property
    def has_gaze_app(self):
        """True if a gaze app holds a valid API token for this patient."""
        from rest_framework.authtoken.models import Token

        return bool(self.user_id) and Token.objects.filter(user_id=self.user_id).exists()


class DevicePairing(models.Model):
    """A one-time 6-digit code that connects a gaze app to a patient.

    Only a hash of the code is stored. A code works once, for
    PAIRING_CODE_TTL minutes, and creating a new code cancels older ones.
    """

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="pairings")
    code_hash = models.CharField(max_length=64, db_index=True)
    created_by = models.ForeignKey(
        "Caregiver", on_delete=models.SET_NULL, null=True, related_name="pairings_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    device_name = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        state = "used" if self.used_at else ("expired" if self.is_expired else "active")
        return f"Pairing for {self.patient} ({state})"

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at


class Caregiver(models.Model):
    """A family member, nurse or carer who monitors one or more patients."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="caregiver"
    )
    phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    is_primary = models.BooleanField(
        default=False, help_text="Main point of contact for their patients."
    )
    is_approved = models.BooleanField(
        default=True, help_text="Unapproved caregivers cannot sign in to the board."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user__first_name", "user__last_name", "user__username"]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return self.user.get_full_name() or self.user.username

    @property
    def initials(self):
        first = self.user.first_name[:1]
        last = self.user.last_name[:1]
        return (first + last).upper() or self.user.username[:2].upper()


class PatientCaregiver(models.Model):
    """Link between a patient and a caregiver. This link is what grants access."""

    class Role(models.TextChoices):
        PRIMARY = "primary", "Primary"
        SECONDARY = "secondary", "Secondary"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="caregiver_links")
    caregiver = models.ForeignKey(Caregiver, on_delete=models.CASCADE, related_name="patient_links")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.SECONDARY)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "patient–caregiver link"
        constraints = [
            models.UniqueConstraint(fields=["patient", "caregiver"], name="unique_patient_caregiver"),
        ]

    def __str__(self):
        return f"{self.caregiver} → {self.patient} ({self.get_role_display()})"


def report_upload_to(instance, filename):
    """Store reports under a random name so filenames cannot be guessed."""
    extension = Path(filename).suffix.lower()
    return f"reports/{instance.patient_id}/{uuid.uuid4().hex}{extension}"


class PatientReport(models.Model):
    """A medical document (PDF or image) attached to a patient."""

    class ReportType(models.TextChoices):
        DOCTOR_VISIT = "doctor_visit", "Doctor visit"
        LAB = "lab", "Lab"
        THERAPY = "therapy", "Therapy"
        DISCHARGE = "discharge", "Discharge summary"
        OTHER = "other", "Other"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="reports")
    report_date = models.DateField(default=date.today)
    report_type = models.CharField(max_length=20, choices=ReportType.choices, default=ReportType.OTHER)
    title = models.CharField(max_length=150)
    notes = models.TextField(blank=True)
    file = models.FileField(upload_to=report_upload_to, validators=[validate_report_file])
    original_filename = models.CharField(max_length=255, blank=True, editable=False)
    added_by = models.ForeignKey(
        Caregiver, on_delete=models.SET_NULL, null=True, blank=True, related_name="reports_added"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-report_date", "-created_at"]

    def __str__(self):
        return f"{self.title} ({self.patient})"

    CONTENT_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}

    @property
    def extension(self):
        return Path(self.file.name).suffix.lower().lstrip(".")

    @property
    def is_image(self):
        return self.extension in ("png", "jpg", "jpeg")

    @property
    def content_type(self):
        return self.CONTENT_TYPES.get(self.extension, "application/octet-stream")

    @property
    def download_name(self):
        """Filename offered to the browser: the original name, or one built from the title."""
        if self.original_filename:
            return self.original_filename
        return f"{slugify(self.title) or 'report'}.{self.extension}"


class Request(models.Model):
    """A phrase selected by the patient in the gaze app (normal or emergency)."""

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="requests")
    phrase = models.CharField(max_length=255)
    is_emergency = models.BooleanField(default=False)
    pain_level = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
        help_text="0–10, filled when the phrase comes from the pain scale.",
    )
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    acknowledged_by = models.ForeignKey(
        Caregiver,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acknowledged_requests",
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["patient", "-timestamp"], name="request_patient_time_idx"),
            models.Index(fields=["is_emergency", "acknowledged_at"], name="request_alert_idx"),
        ]

    def __str__(self):
        prefix = "SOS: " if self.is_emergency else ""
        return f"{prefix}{self.phrase} ({self.patient})"

    @property
    def is_acknowledged(self):
        return self.acknowledged_at is not None

    def acknowledge(self, caregiver):
        """Mark as acknowledged by `caregiver`. Returns True if this call did it.

        Only the first acknowledgement counts. The conditional UPDATE makes this
        safe when two caregivers press the button at the same moment.
        """
        updated = Request.objects.filter(pk=self.pk, acknowledged_at__isnull=True).update(
            acknowledged_by=caregiver, acknowledged_at=timezone.now()
        )
        self.refresh_from_db(fields=["acknowledged_by", "acknowledged_at"])
        return bool(updated)


class CaregiverMessage(models.Model):
    """A short text a caregiver sends to the patient's gaze app screen."""

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="caregiver_messages")
    caregiver = models.ForeignKey(
        Caregiver, on_delete=models.SET_NULL, null=True, related_name="sent_messages"
    )
    text = models.CharField(max_length=280)
    created_at = models.DateTimeField(auto_now_add=True)
    delivered = models.BooleanField(default=False)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"To {self.patient}: {self.text[:40]}"
