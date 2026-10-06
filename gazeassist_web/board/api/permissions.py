"""Permissions for the gaze app API."""
from rest_framework.permissions import BasePermission


class IsGazeApp(BasePermission):
    """Allow only tokens that belong to a patient's gaze-app account.

    A caregiver's (or admin's) token is refused, so the API can only ever
    act on behalf of the one patient the token was issued for.
    """

    message = "This token is not linked to a patient."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and hasattr(user, "patient_profile"))
