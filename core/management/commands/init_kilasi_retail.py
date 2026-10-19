"""Initialize Kilasi retail categories without demo users or demo products."""
from django.core.management.base import BaseCommand
from core.models import Category


class Command(BaseCommand):
    help = 'Create empty Kilasi retail starter categories (safe to rerun).'

    def handle(self, *args, **options):
        for name in ('Furniture', 'Electronics', 'Magodoro'):
            _, created = Category.objects.get_or_create(name=name)
            self.stdout.write(('Created: ' if created else 'Exists: ') + name)
        self.stdout.write(self.style.SUCCESS('Kilasi retail categories are ready.'))
