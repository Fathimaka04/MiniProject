"""Signal handlers for the board app."""
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import PatientReport


@receiver(post_delete, sender=PatientReport)
def delete_report_file(sender, instance, **kwargs):
    """Remove the stored file when a report row is deleted."""
    if instance.file:
        instance.file.delete(save=False)
