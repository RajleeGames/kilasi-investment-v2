"""Rename text of old SMS templates only, without rewriting sent campaigns or Beem Sender IDs."""
from django.core.management.base import BaseCommand
from sms_center.models import SmsTemplate


class Command(BaseCommand):
    help='Dry-run by default: rebrand old Novman wording in unsent SMS templates only.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self,*args,**kwargs):
        count=0
        for item in SmsTemplate.objects.all():
            body=item.body
            name=item.name
            for old,new in [('Novman Auto Spares','Kilasi Investment'),('NOVMAN AUTO SPARES','KILASI INVESTMENT'),
                            ('Novman','Kilasi Investment'),('NOVMAN','KILASI INVESTMENT'),
                            ('Kilasi Investment Transport','HAMA NA KILASI'),('Kilasi Transport','HAMA NA KILASI')]:
                body=body.replace(old,new)
            if body!=item.body:
                count+=1
                if kwargs['apply']:
                    item.body=body
                    item.save(update_fields=['body','updated_at'])
        self.stdout.write(f'{"UPDATED" if kwargs["apply"] else "DRY RUN"}: {count} template body/bodies. '
                          'Sender IDs and historical campaigns/messages are unchanged.')
