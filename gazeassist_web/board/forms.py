"""Forms for the caregiver board, styled for Bootstrap 5 floating labels."""
from datetime import date

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.template.defaultfilters import filesizeformat

from .access import get_caregiver
from .models import Caregiver, CaregiverMessage, Patient, PatientReport, phone_validator

User = get_user_model()


class BootstrapFormMixin:
    """Add Bootstrap classes, floating-label placeholders and error states to fields."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                css = "form-check-input"
            elif isinstance(widget, forms.Select):
                css = "form-select"
            else:
                css = "form-control"
                # Floating labels need a placeholder to work.
                widget.attrs.setdefault("placeholder", field.label or name)
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()

    def full_clean(self):
        super().full_clean()
        for name in self.errors:
            if name in self.fields:
                attrs = self.fields[name].widget.attrs
                if "is-invalid" not in attrs.get("class", ""):
                    attrs["class"] = f"{attrs.get('class', '')} is-invalid".strip()
                attrs["aria-invalid"] = "true"
                attrs["aria-describedby"] = f"{self[name].auto_id}_error"


class CaregiverLoginForm(BootstrapFormMixin, AuthenticationForm):
    """Login form that also blocks caregivers who are awaiting approval."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "pending_approval": "Your account is waiting for administrator approval. "
        "You will be able to sign in once it is approved.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs["autofocus"] = True

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        caregiver = get_caregiver(user)
        if caregiver is not None and not caregiver.is_approved:
            raise ValidationError(self.error_messages["pending_approval"], code="pending_approval")


class CaregiverRegistrationForm(BootstrapFormMixin, UserCreationForm):
    """Self-registration: creates a Django user plus its Caregiver profile."""

    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, label="Last name", required=False)
    email = forms.EmailField(label="Email address")
    phone = forms.CharField(
        max_length=20, label="Phone number", required=False, validators=[phone_validator]
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("first_name", "last_name", "email", "phone", "username")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = "Letters, digits and @ . + - _ only."
        self.fields["username"].widget.attrs.pop("autofocus", None)
        self.fields["first_name"].widget.attrs["autofocus"] = True
        self.fields["email"].widget.attrs["autocomplete"] = "email"
        self.fields["phone"].widget.attrs["autocomplete"] = "tel"
        self.fields["first_name"].widget.attrs["autocomplete"] = "given-name"
        self.fields["last_name"].widget.attrs["autocomplete"] = "family-name"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def save(self, commit=True, require_approval=False):
        """Save the user and create the linked Caregiver profile."""
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data.get("last_name", "")
        if commit:
            user.save()
            Caregiver.objects.create(
                user=user,
                phone=self.cleaned_data.get("phone", ""),
                is_approved=not require_approval,
            )
        return user


class PatientReportForm(BootstrapFormMixin, forms.ModelForm):
    """Upload a report. File checks (type, signature, size) live in validators.py."""

    class Meta:
        model = PatientReport
        fields = ("title", "report_type", "report_date", "notes", "file")
        widgets = {
            "report_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 4}),
            "file": forms.FileInput(
                attrs={"accept": ".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"}
            ),
        }
        labels = {"report_type": "Report type", "report_date": "Report date", "file": "File"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["title"].widget.attrs["autofocus"] = True
        self.fields["file"].help_text = (
            f"PDF, PNG or JPG, up to {filesizeformat(settings.REPORT_MAX_UPLOAD_BYTES)}."
        )

    def clean_report_date(self):
        value = self.cleaned_data["report_date"]
        if value and value > date.today():
            raise ValidationError("The report date cannot be in the future.")
        return value


QUICK_REPLIES = (
    "I'm on my way.",
    "Help is coming, stay calm.",
    "I'll be there in 5 minutes.",
    "I saw your message.",
)


class CaregiverMessageForm(BootstrapFormMixin, forms.ModelForm):
    """A short text shown on the patient's gaze app screen."""

    class Meta:
        model = CaregiverMessage
        fields = ("text",)
        widgets = {"text": forms.Textarea(attrs={"rows": 3, "maxlength": 280})}
        labels = {"text": "Message to the patient"}

    def clean_text(self):
        text = " ".join(self.cleaned_data["text"].split())
        if not text:
            raise ValidationError("Write a short message first.")
        return text


class PatientForm(BootstrapFormMixin, forms.ModelForm):
    """Add a patient from the caregiver dashboard."""

    class Meta:
        model = Patient
        fields = ("name", "date_of_birth", "gender", "language", "condition_type", "condition_notes")
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "condition_notes": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "name": "Full name",
            "date_of_birth": "Date of birth",
            "condition_type": "Condition",
            "condition_notes": "Condition notes (optional)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs["autofocus"] = True
        self.fields["date_of_birth"].required = False

    def clean_date_of_birth(self):
        value = self.cleaned_data.get("date_of_birth")
        if value and value > date.today():
            raise ValidationError("The date of birth cannot be in the future.")
        return value
