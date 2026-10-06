"""Create API accounts and tokens for the gaze app.

Each patient gets a dedicated Django user (no password, so it can never sign
in to the website). The DRF token belongs to that user, so every API call is
automatically scoped to exactly one patient.
"""
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.authtoken.models import Token


def gaze_username(patient):
    return f"gaze-patient-{patient.pk}"


@transaction.atomic
def ensure_gaze_token(patient, reset=False):
    """Return the patient's API token, creating the account and token if needed.

    With ``reset=True`` the old token is revoked and a new one issued.
    """
    User = get_user_model()
    if patient.user is None:
        user, _ = User.objects.get_or_create(
            username=gaze_username(patient),
            defaults={"first_name": "Gaze app", "last_name": patient.name[:150]},
        )
        user.set_unusable_password()
        user.save()
        patient.user = user
        patient.save(update_fields=["user"])

    if reset:
        Token.objects.filter(user=patient.user).delete()
    token, _ = Token.objects.get_or_create(user=patient.user)
    return token.key
