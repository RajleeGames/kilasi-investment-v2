"""Remove the unused migration placeholder Main Branch only if absolutely unreferenced."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import Branch

ALLOWED = ('KILASI JUU', 'KILASI CHINI', 'KILASI ARUSHA')


class Command(BaseCommand):
    help = 'Audit and safely remove the unused Main Branch without reassigning historical/staff data.'

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--check', action='store_true')
        group.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        expected = list(Branch.objects.filter(name__in=ALLOWED).order_by('name'))
        if len(expected) != 3 or any(not b.is_active for b in expected):
            raise CommandError('All three named Retail branches must exist and be active before Main Branch cleanup.')
        mains = list(Branch.objects.filter(name__iexact='Main Branch'))
        if not mains:
            self.stdout.write(self.style.SUCCESS('ALREADY CLEAN: Main Branch does not exist; three Retail branches preserved.'))
            return
        if len(mains) != 1:
            raise CommandError('Multiple Main Branch rows found. Refusing automatic cleanup.')
        main = mains[0]
        references = []
        for related in Branch._meta.related_objects:
            model = related.related_model
            field = related.field
            count = model._base_manager.filter(**{field.name: main}).count()
            if count:
                references.append(f'{model._meta.label}.{field.name}: {count}')
        if references:
            for line in references:
                self.stdout.write('IN USE: ' + line)
            raise CommandError('Main Branch is referenced. Reassign relevant staff manually and review every other reference. Nothing deleted.')
        if options['check']:
            self.stdout.write(self.style.SUCCESS('CHECK OK — Main Branch is unreferenced and can be deleted. No change made.'))
            return
        with transaction.atomic():
            # Re-validate inside the transaction to prevent removing any related history.
            main = Branch.objects.select_for_update().get(pk=main.pk)
            for related in Branch._meta.related_objects:
                if related.related_model._base_manager.filter(**{related.field.name:main}).exists():
                    raise CommandError('Reference appeared during cleanup; deletion cancelled.')
            main.delete()
        self.stdout.write(self.style.SUCCESS('APPLY OK — unused Main Branch physically deleted. Exactly three active Retail branches remain.'))
