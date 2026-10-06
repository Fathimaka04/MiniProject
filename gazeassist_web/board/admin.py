"""Admin panel configuration with search, filters and approval actions."""
from django.contrib import admin, messages
from django.db.models import Count
from django.shortcuts import redirect
from django.urls import reverse

from .api.tokens import ensure_gaze_token
from .models import (
    Caregiver,
    CaregiverMessage,
    DevicePairing,
    Patient,
    PatientCaregiver,
    PatientReport,
    Request,
    SiteSettings,
)


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Singleton: the list page jumps straight to the one settings row."""

    list_display = ("__str__", "require_caregiver_approval", "updated_at")

    def changelist_view(self, request, extra_context=None):
        obj = SiteSettings.load()
        return redirect(reverse("admin:board_sitesettings_change", args=[obj.pk]))

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class PatientCaregiverInline(admin.TabularInline):
    model = PatientCaregiver
    extra = 1
    autocomplete_fields = ("caregiver",)


class CaregiverPatientInline(admin.TabularInline):
    model = PatientCaregiver
    extra = 0
    autocomplete_fields = ("patient",)
    verbose_name_plural = "Linked patients"


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = (
        "name", "condition_type", "age_display", "gender", "language",
        "online_display", "last_seen", "created_at",
    )
    list_filter = ("condition_type", "gender", "language", "created_at")
    search_fields = ("name", "condition_notes", "user__username")
    readonly_fields = ("created_at", "last_seen", "device_name", "device_paired_at")
    autocomplete_fields = ("user",)
    inlines = (PatientCaregiverInline,)
    actions = ("show_gaze_tokens", "reset_gaze_tokens")
    fieldsets = (
        ("Profile", {"fields": ("name", "date_of_birth", "gender", "language")}),
        ("Condition", {"fields": ("condition_type", "condition_notes")}),
        ("Gaze app", {"fields": ("user", "device_name", "device_paired_at", "last_seen", "created_at")}),
    )

    @admin.display(description="Age")
    def age_display(self, obj):
        return obj.age if obj.age is not None else "—"

    @admin.display(description="Online", boolean=True)
    def online_display(self, obj):
        return obj.is_online

    @admin.action(description="Show gaze app API token (creates one if missing)")
    def show_gaze_tokens(self, request, queryset):
        for patient in queryset:
            key = ensure_gaze_token(patient)
            self.message_user(request, f"{patient.name}: {key}", messages.INFO)

    @admin.action(description="Reset gaze app API token (old token stops working)")
    def reset_gaze_tokens(self, request, queryset):
        for patient in queryset:
            key = ensure_gaze_token(patient, reset=True)
            self.message_user(request, f"New token for {patient.name}: {key}", messages.WARNING)


@admin.register(Caregiver)
class CaregiverAdmin(admin.ModelAdmin):
    list_display = (
        "display_name", "username", "email", "phone", "is_primary", "is_approved",
        "patient_count", "created_at",
    )
    list_filter = ("is_approved", "is_primary", "created_at")
    list_editable = ("is_approved",)
    search_fields = ("user__username", "user__first_name", "user__last_name", "user__email", "phone")
    list_select_related = ("user",)
    autocomplete_fields = ("user",)
    inlines = (CaregiverPatientInline,)
    actions = ("approve_caregivers", "revoke_approval")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(num_patients=Count("patient_links"))

    @admin.display(description="Name", ordering="user__first_name")
    def display_name(self, obj):
        return obj.display_name

    @admin.display(description="Username", ordering="user__username")
    def username(self, obj):
        return obj.user.username

    @admin.display(description="Email", ordering="user__email")
    def email(self, obj):
        return obj.user.email

    @admin.display(description="Patients", ordering="num_patients")
    def patient_count(self, obj):
        return obj.num_patients

    @admin.action(description="Approve selected caregivers")
    def approve_caregivers(self, request, queryset):
        updated = queryset.update(is_approved=True)
        self.message_user(request, f"{updated} caregiver(s) approved.", messages.SUCCESS)

    @admin.action(description="Revoke approval (block sign-in)")
    def revoke_approval(self, request, queryset):
        updated = queryset.update(is_approved=False)
        self.message_user(request, f"{updated} caregiver(s) blocked.", messages.WARNING)


@admin.register(PatientCaregiver)
class PatientCaregiverAdmin(admin.ModelAdmin):
    list_display = ("patient", "caregiver", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("patient__name", "caregiver__user__username", "caregiver__user__first_name")
    autocomplete_fields = ("patient", "caregiver")
    list_select_related = ("patient", "caregiver__user")


@admin.register(PatientReport)
class PatientReportAdmin(admin.ModelAdmin):
    list_display = ("title", "patient", "report_type", "report_date", "added_by", "created_at")
    list_filter = ("report_type", "report_date", "created_at")
    search_fields = ("title", "notes", "patient__name", "original_filename")
    date_hierarchy = "report_date"
    autocomplete_fields = ("patient", "added_by")
    readonly_fields = ("original_filename", "created_at")
    list_select_related = ("patient", "added_by__user")


class AcknowledgedFilter(admin.SimpleListFilter):
    title = "acknowledged"
    parameter_name = "acknowledged"

    def lookups(self, request, model_admin):
        return (("yes", "Acknowledged"), ("no", "Waiting"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(acknowledged_at__isnull=False)
        if self.value() == "no":
            return queryset.filter(acknowledged_at__isnull=True)
        return queryset


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = (
        "phrase", "patient", "is_emergency", "pain_level", "timestamp",
        "acknowledged_display", "acknowledged_by",
    )
    list_filter = ("is_emergency", AcknowledgedFilter, "timestamp", "patient")
    search_fields = ("phrase", "patient__name")
    date_hierarchy = "timestamp"
    autocomplete_fields = ("patient", "acknowledged_by")
    list_select_related = ("patient", "acknowledged_by__user")

    @admin.display(description="Acknowledged", boolean=True)
    def acknowledged_display(self, obj):
        return obj.is_acknowledged


@admin.register(CaregiverMessage)
class CaregiverMessageAdmin(admin.ModelAdmin):
    list_display = ("text", "patient", "caregiver", "created_at", "delivered", "delivered_at")
    list_filter = ("delivered", "created_at")
    search_fields = ("text", "patient__name", "caregiver__user__username")
    autocomplete_fields = ("patient", "caregiver")
    list_select_related = ("patient", "caregiver__user")


@admin.register(DevicePairing)
class DevicePairingAdmin(admin.ModelAdmin):
    """Read-only history of gaze app connection codes (codes themselves are never stored)."""

    list_display = ("patient", "created_by", "created_at", "expires_at", "used_at", "device_name")
    list_filter = ("used_at", "created_at")
    search_fields = ("patient__name", "device_name")
    list_select_related = ("patient", "created_by__user")
    readonly_fields = ("patient", "code_hash", "created_by", "created_at", "expires_at", "used_at", "device_name")

    def has_add_permission(self, request):
        return False
