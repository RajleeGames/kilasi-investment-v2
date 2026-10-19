"""Initialize OFF-by-default branch and department SMS rules. No sends."""
from django.core.management.base import BaseCommand
from core.models import Branch
from sms_center.models import SmsTemplate
from kilasi_sms.models import KilasiSmsAutomationRule
from kilasi_sms.automation_definitions import EVENTS


class Command(BaseCommand):
    help='Create disabled Kilasi SMS automation rules for all active branches. Never sends SMS.'

    def handle(self,*args,**options):
        created_count=0
        # Retail remains branch-specific.
        for branch in Branch.objects.filter(is_active=True):
            for key, label, department, template_name in EVENTS:
                if department!='retail':continue
                template=SmsTemplate.objects.filter(name=template_name,category='Kilasi Retail').first()
                if not template:continue
                _,made=KilasiSmsAutomationRule.objects.get_or_create(
                    branch=branch,event_key=key,defaults={'department':'retail','template':template,'enabled':False,'sender_id':''})
                created_count+=int(made)
        # One HAMA NA KILASI rule set: no Retail branch FK.
        for key,label,department,template_name in EVENTS:
            if department!='transport':continue
            template=SmsTemplate.objects.filter(name=template_name,category='Kilasi Transport').first()
            if not template:continue
            _,made=KilasiSmsAutomationRule.objects.get_or_create(
                branch=None,event_key=key,defaults={'department':'transport','template':template,'enabled':False,'sender_id':''})
            created_count+=int(made)
        self.stdout.write(self.style.SUCCESS(f'Created {created_count} disabled rules. Zero SMS sent.'))
