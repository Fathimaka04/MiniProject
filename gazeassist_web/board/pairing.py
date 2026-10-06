"""Connecting a gaze app to a patient with a one-time code ("pairing").

Flow:
  1. A primary caregiver clicks "Connect gaze app" -> create_pairing_code()
     returns a 6-digit code, valid for PAIRING_CODE_TTL_MINUTES.
  2. On the patient's computer the code is typed into the gaze app, which
     sends it to POST /api/pair/ -> redeem_pairing_code().
  3. That issues a fresh API token for the patient (revoking any token an
     older device had) and returns it to the gaze app, which stores it.
     Nobody ever sees or copies the token.

Only an HMAC of the code is stored, a code works once, and the pairing
endpoint is rate-limited, so guessing a code is impractical.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.authtoken.models import Token

from .api.tokens import ensure_gaze_token
from .models import DevicePairing, Patient

CODE_DIGITS = 6


def _hash_code(code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def normalise_code(raw: str) -> str:
    """Accept '482 913', '482-913' or '482913'."""
    return "".join(ch for ch in raw if ch.isdigit())


@transaction.atomic
def create_pairing_code(patient: Patient, caregiver) -> tuple[str, DevicePairing]:
    """Return (code, pairing). Any older unused code for this patient stops working."""
    now = timezone.now()
    DevicePairing.objects.filter(patient=patient, used_at__isnull=True, expires_at__gt=now).update(expires_at=now)

    active = DevicePairing.objects.filter(used_at__isnull=True, expires_at__gt=now)
    while True:
        code = f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"
        if not active.filter(code_hash=_hash_code(code)).exists():
            break
    pairing = DevicePairing.objects.create(
        patient=patient,
        code_hash=_hash_code(code),
        created_by=caregiver,
        expires_at=now + timedelta(minutes=settings.PAIRING_CODE_TTL_MINUTES),
    )
    return code, pairing


@transaction.atomic
def redeem_pairing_code(raw_code: str, device_name: str = "") -> Optional[tuple[Patient, str]]:
    """Exchange a valid code for the patient's new API token.

    Returns (patient, token) or None if the code is wrong, expired or used.
    """
    code = normalise_code(raw_code)
    if len(code) != CODE_DIGITS:
        return None
    now = timezone.now()
    pairing = (
        DevicePairing.objects.select_related("patient")
        .filter(code_hash=_hash_code(code), used_at__isnull=True, expires_at__gt=now)
        .first()
    )
    if pairing is None:
        return None
    # Conditional update: if two devices race with the same code, only one wins.
    device_name = (device_name or "").strip()[:100] or "Gaze app"
    claimed = DevicePairing.objects.filter(pk=pairing.pk, used_at__isnull=True).update(
        used_at=now, device_name=device_name
    )
    if not claimed:
        return None

    patient = pairing.patient
    token = ensure_gaze_token(patient, reset=True)   # the previous device stops working
    patient.device_name = device_name
    patient.device_paired_at = now
    patient.save(update_fields=["device_name", "device_paired_at"])
    return patient, token


@transaction.atomic
def disconnect_device(patient: Patient) -> bool:
    """Revoke the patient's API token. Returns True if a device was connected."""
    removed = 0
    if patient.user_id:
        removed, _ = Token.objects.filter(user_id=patient.user_id).delete()
    patient.device_name = ""
    patient.device_paired_at = None
    patient.save(update_fields=["device_name", "device_paired_at"])
    DevicePairing.objects.filter(patient=patient, used_at__isnull=True).update(expires_at=timezone.now())
    return bool(removed)
