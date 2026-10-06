"""Show (or reset) the API token the gaze app uses for a patient.

Usage:
    python manage.py gaze_token --list
    python manage.py gaze_token 1
    python manage.py gaze_token 1 --reset      # revoke the old token, issue a new one
"""
from django.core.management.base import BaseCommand, CommandError

from board.api.tokens import ensure_gaze_token
from board.models import Patient


class Command(BaseCommand):
    help = "Print the gaze app API token for a patient (creates it if needed)."

    def add_arguments(self, parser):
        parser.add_argument("patient_id", nargs="?", type=int, help="Patient ID (see --list).")
        parser.add_argument("--reset", action="store_true", help="Revoke the old token and create a new one.")
        parser.add_argument("--list", action="store_true", help="List patients and their IDs.")

    def handle(self, *args, **options):
        if options["list"] or options["patient_id"] is None:
            for patient in Patient.objects.order_by("pk"):
                has_token = bool(patient.user_id and hasattr(patient.user, "auth_token"))
                self.stdout.write(f"{patient.pk:>4}  {patient.name:<30} token: {'yes' if has_token else 'no'}")
            if options["patient_id"] is None and not options["list"]:
                self.stdout.write("\nRun: python manage.py gaze_token <patient_id>")
            return

        try:
            patient = Patient.objects.get(pk=options["patient_id"])
        except Patient.DoesNotExist:
            raise CommandError(f"No patient with ID {options['patient_id']}. Use --list.")

        key = ensure_gaze_token(patient, reset=options["reset"])
        action = "New token" if options["reset"] else "Token"
        self.stdout.write(self.style.SUCCESS(f"{action} for {patient.name} (patient {patient.pk}):"))
        self.stdout.write(key)
        self.stdout.write("Put it in the gaze app's .env as GAZEASSIST_API_TOKEN=<token>")
