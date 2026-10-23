"""Run from a real scheduler (Task Scheduler/cron), never inside runserver loop."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from kilasi_sms.automation import scan_trip_reminders, can_send_automatically, deliver_job, retry_confirmed_delivery_failures
from kilasi_sms.models import KilasiSmsAutomaticJob


class Command(BaseCommand):
    help='Scan trip reminders and process due SMS jobs. --send requires both explicit live switches.'

    def add_arguments(self,parser):
        group=parser.add_mutually_exclusive_group()
        group.add_argument('--send',action='store_true',help='Send due messages if both live switches are enabled.')
        group.add_argument('--dry-run',action='store_true',help='Inspect due messages without creating jobs/sending.')
        parser.add_argument('--limit',type=int,default=50)

    def handle(self,*args,**options):
        if not 1<=options['limit']<=200:raise CommandError('limit must be 1 to 200.')
        now=timezone.now()
        if options['dry_run'] or not options['send']:
            due=KilasiSmsAutomaticJob.objects.filter(status='queued',due_at__lte=now)
            self.stdout.write(f'DRY RUN. Pending due jobs: {due.count()}. No provider requests. No reminders created.')
            return
        if not can_send_automatically():
            raise CommandError('Automatic SMS is OFF. Requires KILASI_SMS_AUTOMATION_ENABLED=1, KILASI_SMS_LIVE_ENABLED=1 and complete Beem configuration.')
        scan_trip_reminders(now=now)
        retry_confirmed_delivery_failures()
        ids=list(KilasiSmsAutomaticJob.objects.filter(status='queued',due_at__lte=timezone.now())
                 .order_by('due_at','pk').values_list('pk',flat=True)[:options['limit']])
        accepted=0
        for pk in ids:
            try:
                accepted+=int(deliver_job(pk) is True)
            except Exception as exc:
                # Status 'sending' may mean Beem accepted already: never auto retry.
                self.stderr.write(f'Job {pk}: check provider status; no retry. Error type {type(exc).__name__}.')
        self.stdout.write(f'Processed {len(ids)} due jobs. Beem accepted: {accepted}. Verify delivery receipts separately.')
