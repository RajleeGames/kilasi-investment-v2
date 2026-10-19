"""Manage old SMS template branding WITHOUT editing sent SMS history or sender approvals.

Django management command: novman_sms_brand [--apply]
"""
import re

from django.core.management.base import BaseCommand
from django.db import transaction

from sms_center.models import SmsCampaign, SmsSenderProfile, SmsTemplate


OLD_BRAND = re.compile(r"\bJane\s*[-_ ]\s*Cosmetics\b", re.IGNORECASE)
LEGACY_PRODUCT_WORDS = re.compile(r"\b(beauty|makeup|lipstick|skincare|cosmetics|perfume|facial|eyelash)\b", re.IGNORECASE)
NEW_BRAND = "NOVMAN AUTO SPARES"


class Command(BaseCommand):
    help = "Preview or replace Jane Cosmetics branding in saved SMS templates (not sent history or Sender IDs)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Save the proposed template wording updates.")

    def handle(self, *args, **options):
        changed = 0
        changed_fields = 0
        conflicts = 0
        with transaction.atomic():
            for template in SmsTemplate.objects.order_by("id"):
                replacements = {}
                for field in ("name", "category", "body"):
                    current = getattr(template, field)
                    revised = OLD_BRAND.sub(NEW_BRAND, current)
                    if current != revised:
                        replacements[field] = revised
                if not replacements:
                    continue
                if "name" in replacements and SmsTemplate.objects.exclude(pk=template.pk).filter(name=replacements["name"]).exists():
                    self.stdout.write(self.style.WARNING(
                        f"SKIP template #{template.pk}: renamed title would conflict with another template."
                    ))
                    conflicts += 1
                    continue
                changed += 1
                changed_fields += len(replacements)
                self.stdout.write(f"{'UPDATING' if options['apply'] else 'WOULD UPDATE'} "
                                  f"template #{template.pk}: {', '.join(replacements)}")
                if options["apply"]:
                    for field, new_value in replacements.items():
                        setattr(template, field, new_value)
                    template.save(update_fields=[*replacements.keys(), "updated_at"])

        legacy_topic_templates = sum(
            bool(LEGACY_PRODUCT_WORDS.search(t.name + " " + t.category + " " + t.body))
            for t in SmsTemplate.objects.all()
        )
        old_profiles = SmsSenderProfile.objects.filter(sender_id__iexact="JANE").count()
        old_campaigns = SmsCampaign.objects.filter(message__icontains="Jane Cosmetics").count()
        self.stdout.write(f"Templates affected: {changed} ({changed_fields} fields); name conflicts: {conflicts}.")
        self.stdout.write(f"Saved JANE sender profiles: {old_profiles}. Not changed: Sender IDs require provider approval.")
        self.stdout.write(f"Templates needing manual motorcycle-spares copy review: {legacy_topic_templates} (cosmetics/beauty wording).")
        self.stdout.write(f"Historical campaigns mentioning old branding: {old_campaigns}. Not changed for audit accuracy.")
        if options["apply"]:
            self.stdout.write(self.style.SUCCESS("Saved template branding corrections. No SMS sent."))
        else:
            self.stdout.write("DRY RUN: no records modified. Run with --apply to update templates.")
