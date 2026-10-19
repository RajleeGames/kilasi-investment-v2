"""Correct historical Novman batch timestamps imported as naive local datetimes.

Place in novman_new/core/management/commands/repair_novman_dates.py.

Original Novman has USE_TZ=True and its SQLite datetime values are stored as
naive UTC. Stage-1 importer sent them to an aware DateTimeField as naive, so
Django interpreted them in Africa/Dar_es_Salaam, moving them back 3 hours.

This command touches ONLY target StockBatch.received_at for legacy batches.
Runs in audit/dry-run mode unless --apply is explicitly supplied.
"""
from datetime import datetime, timezone as utc
from pathlib import Path
import sqlite3

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import StockBatch

THREE_HOURS = 10800
TOLERANCE_SECONDS = 2


def old_sqlite_datetime_as_utc(value):
    if not value:
        raise CommandError("Source batch has a blank created_at value")
    try:
        dt = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    except (ValueError, TypeError) as exc:
        raise CommandError(f"Unparseable source batch datetime: {value!r}") from exc
    # With USE_TZ=True, Django SQLite stores aware dates as UTC without tzinfo.
    return dt.replace(tzinfo=utc.utc) if dt.tzinfo is None else dt.astimezone(utc.utc)


class Command(BaseCommand):
    help = "Verify/repair Novman legacy stock batch datetimes (dry run unless --apply)"

    def add_arguments(self, parser):
        parser.add_argument('source', help='Original read-only Novman SQLite snapshot')
        parser.add_argument('--apply', action='store_true', help='Commit timestamp corrections')

    def handle(self, *args, **options):
        source = Path(options['source']).expanduser().resolve()
        if not source.is_file():
            raise CommandError(f'Cannot find source snapshot: {source}')
        try:
            with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as connection:
                connection.row_factory = sqlite3.Row
                if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise CommandError('Source database failed SQLite quick_check')
                rows = connection.execute('''
                    SELECT id, quantity_remaining, created_at
                    FROM inventory_productbatch
                ''').fetchall()
                source_times = {
                    row['id']: old_sqlite_datetime_as_utc(row['created_at'])
                    for row in rows if int(row['quantity_remaining']) > 0
                }
        except sqlite3.Error as exc:
            raise CommandError(f'Could not read source database: {exc}') from exc

        target = list(StockBatch.objects.filter(
            legacy_novman_id__isnull=False
        ).order_by('id'))
        target_ids = [row.legacy_novman_id for row in target]
        if len(target_ids) != len(set(target_ids)):
            raise CommandError('Duplicate legacy_novman_id records in target')
        if set(target_ids) != set(source_times):
            missing = sorted(set(source_times) - set(target_ids))[:10]
            extra = sorted(set(target_ids) - set(source_times))[:10]
            raise CommandError(f'Legacy batch ID mismatch: missing={missing} extra={extra}')

        to_fix = []
        already_correct = 0
        for batch in target:
            expected = source_times[batch.legacy_novman_id]
            actual = batch.received_at
            if actual is None or actual.tzinfo is None:
                raise CommandError(f'Target batch {batch.pk} has missing or naive received_at')
            actual = actual.astimezone(utc.utc)
            seconds = (expected - actual).total_seconds()
            if abs(seconds) <= TOLERANCE_SECONDS:
                already_correct += 1
                continue
            if abs(seconds - THREE_HOURS) > TOLERANCE_SECONDS:
                raise CommandError(
                    f'Unexpected timestamp difference for legacy batch {batch.legacy_novman_id}: '
                    f'expected={expected.isoformat()}, target={actual.isoformat()}, delta={seconds}s. '
                    'Refusing changes; this batch may have been edited or imported differently.'
                )
            to_fix.append((batch, expected))

        self.stdout.write(f'Source positive legacy batches: {len(source_times)}')
        self.stdout.write(f'Target mapped batches: {len(target)}')
        self.stdout.write(f'Already correct: {already_correct}')
        self.stdout.write(f'3-hour timestamp corrections needed: {len(to_fix)}')
        for batch, expected in to_fix[:3]:
            self.stdout.write(
                f'  legacy #{batch.legacy_novman_id}: '
                f'{batch.received_at.astimezone(utc.utc).isoformat()} -> {expected.isoformat()} UTC'
            )
        if not options['apply']:
            self.stdout.write(self.style.WARNING('DRY RUN: no target records changed.'))
            return
        with transaction.atomic():
            for batch, expected in to_fix:
                batch.received_at = expected
            StockBatch.objects.bulk_update(
                [batch for batch, _ in to_fix], ['received_at'], batch_size=250
            ) if to_fix else None
        self.stdout.write(self.style.SUCCESS(
            f'Repaired {len(to_fix)} timestamps. Quantities/prices/branches unchanged.'
        ))
