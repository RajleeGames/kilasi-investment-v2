"""Import a Novman SQLite snapshot into a FRESH, locally patched Jane Cosmetics project.

SAFETY: requires --apply; import is atomic, read-only on source, refuses to import
into a non-empty target product/stock database, and validates per-branch stock.

Intended install location: novman_new/core/management/commands/import_novman_inventory.py
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from core.models import (
    Branch, Category, Product, ProductUnit, ShopSettings, StockBatch, Supplier,
)

BRANCH_NAMES = {2: 'MAJENGO', 3: 'SIRONGA'}


def dec(value):
    try:
        return Decimal(str(value if value is not None else 0))
    except (ValueError, TypeError, InvalidOperation) as e:
        raise CommandError(f'Invalid numeric value in source: {value!r}') from e


def read_rows(conn, table):
    return [dict(row) for row in conn.execute(f'SELECT * FROM "{table}"')]


def build_snapshot(source):
    try:
        conn = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    except sqlite3.Error as e:
        raise CommandError(f'Cannot open source database: {e}') from e
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise CommandError('Source SQLite quick_check failed.')
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {
            'inventory_branch', 'inventory_category', 'inventory_supplier',
            'inventory_product', 'inventory_productstock', 'inventory_productbatch',
        }
        missing = required - names
        if missing:
            raise CommandError('Source is missing tables: ' + ', '.join(sorted(missing)))
        snap = {k: read_rows(conn, k) for k in required}
    finally:
        conn.close()

    b_by_id = {r['id']: r for r in snap['inventory_branch']}
    for bid, word in BRANCH_NAMES.items():
        if bid not in b_by_id or word not in b_by_id[bid]['name'].upper():
            raise CommandError(f'Expected branch ID {bid} containing {word!r}; snapshot does not match.')
    for branch in snap['inventory_branch']:
        if branch['id'] not in BRANCH_NAMES and any(
            x['branch_id'] == branch['id'] and dec(x['quantity']) != 0
            for x in snap['inventory_productstock']
        ):
            raise CommandError(f'Non-spares branch {branch["name"]!r} has stock; stop and inspect.')

    old_products = {p['id']: p for p in snap['inventory_product']}
    branch_qty = defaultdict(Decimal)
    for x in snap['inventory_productstock']:
        key = (x['product_id'], x['branch_id'])
        q = dec(x['quantity'])
        if q < 0:
            raise CommandError(f'Negative stock in branch stock row {key}')
        branch_qty[key] += q
    batch_qty = defaultdict(Decimal)
    for x in snap['inventory_productbatch']:
        key = (x['product_id'], x['branch_id'])
        q = dec(x['quantity_remaining'])
        if q < 0:
            raise CommandError(f'Negative batch quantity for {key}')
        if dec(x['buying_price']) < 0:
            raise CommandError(f'Negative batch buying price for batch {x["id"]}')
        batch_qty[key] += q
    all_keys = set(branch_qty) | set(batch_qty)
    mismatches = [
        (key, branch_qty.get(key, Decimal(0)), batch_qty.get(key, Decimal(0)))
        for key in all_keys if branch_qty.get(key, Decimal(0)) != batch_qty.get(key, Decimal(0))
    ]
    if mismatches:
        raise CommandError(f'Source branch/batch stock mismatch, first 5: {mismatches[:5]}')
    for p in old_products.values():
        qty = sum((q for (pid, _), q in branch_qty.items() if pid == p['id']), Decimal(0))
        if dec(p['stock']) != qty:
            raise CommandError(f'Cached stock mismatch for product ID {p["id"]}')
        if not str(p['name'] or '').strip():
            raise CommandError(f'Blank product name ID {p["id"]}')
        if dec(p['buying_price']) < 0 or dec(p['selling_price']) < 0:
            raise CommandError(f'Negative product price ID {p["id"]}')
    barcodes = defaultdict(list)
    for p in old_products.values():
        value = (p['barcode'] or '').strip()
        if value:
            barcodes[value].append(p['id'])
    dup_codes = {code: ids for code, ids in barcodes.items() if len(ids) > 1}
    if dup_codes:
        raise CommandError(f'Duplicate source barcodes found; first 5: {list(dup_codes.items())[:5]}')
    total = sum(branch_qty.values(), Decimal(0))
    return snap, branch_qty, total


class Command(BaseCommand):
    help = 'Read-only audit or atomic inventory import from original Novman SQLite snapshot'

    def add_arguments(self, parser):
        parser.add_argument('source', help='Path to offline Novman SQLite snapshot')
        parser.add_argument('--user', default='admin', help='Existing new-system staff username')
        parser.add_argument('--apply', action='store_true', help='Actually write imported records')
        parser.add_argument('--expect-products', type=int, default=519)
        parser.add_argument('--expect-stock', default='13818')

    def handle(self, *args, **opts):
        src = Path(opts['source']).expanduser().resolve()
        if not src.is_file():
            raise CommandError('Source database file not found: '+str(src))
        snap, expected_by_branch, expected_total = build_snapshot(src)
        count = len(snap['inventory_product'])
        if count != opts['expect_products'] or expected_total != dec(opts['expect_stock']):
            raise CommandError(
                f'Snapshot counts changed. Products={count}, stock={expected_total}; '
                f'expected products={opts["expect_products"]}, stock={opts["expect_stock"]}. '
                'Check your source snapshot before setting new expected values.'
            )
        self.stdout.write(f'SOURCE VERIFIED (read-only): {count} products, {expected_total} units')
        self.stdout.write(f'Categories={len(snap["inventory_category"])}; '
                          f'Suppliers={len(snap["inventory_supplier"])}; '
                          f'Batches={len(snap["inventory_productbatch"])}')
        for branch in snap['inventory_branch']:
            self.stdout.write(f'  {branch["id"]}: {branch["name"]} - '
                              f'{sum((q for (_,bid),q in expected_by_branch.items() if bid==branch["id"]), Decimal(0))} units')
        if not opts['apply']:
            self.stdout.write(self.style.WARNING('DRY RUN ONLY: no target data changed.'))
            return
        user = User.objects.filter(username=opts['user'], is_active=True, is_staff=True).first()
        if user is None:
            raise CommandError('Create an active staff/superuser first: ' + opts['user'])
        if Product.objects.exists() or StockBatch.objects.exists() or Branch.objects.exists() or Supplier.objects.exists() or ProductUnit.objects.exists():
            raise CommandError('Target already has products/stock/branches/suppliers/units. Use a FRESH empty new database, NOT --apply again.')
        if Category.objects.exists():
            raise CommandError('Target has categories. Refusing to mix demo and customer records.')

        with transaction.atomic():
            branch_map = {}
            for old in snap['inventory_branch']:
                if old['id'] in BRANCH_NAMES:
                    branch_map[old['id']] = Branch.objects.create(
                        name=old['name'], legacy_novman_id=old['id'],
                        is_active=bool(old['is_active']),
                    )
            cat_map = {}
            for old in snap['inventory_category']:
                cat_map[old['id']] = Category.objects.create(name=old['name'])
            unknown_cat = None
            if any(old['category_id'] is None for old in snap['inventory_product']):
                name = 'Uncategorized - Novman'
                existing = {c.name.casefold() for c in cat_map.values()}
                while name.casefold() in existing:
                    name += ' (other)'
                unknown_cat = Category.objects.create(name=name)
            supplier_map = {}
            for old in snap['inventory_supplier']:
                supplier_map[old['id']] = Supplier.objects.create(
                    name=old['name'], contact=old.get('contact') or '', legacy_novman_id=old['id'],
                )
            prod_map = {}
            for old in snap['inventory_product']:
                cat = cat_map.get(old['category_id']) if old['category_id'] is not None else unknown_cat
                if cat is None:
                    raise CommandError(f'Missing category mapping for product {old["id"]}')
                supplier = None
                if old['supplier_id'] is not None:
                    supplier = supplier_map.get(old['supplier_id'])
                    if supplier is None:
                        raise CommandError(f'Missing supplier mapping for product {old["id"]}')
                prod = Product.objects.create(
                    name=old['name'], category=cat, supplier=supplier,
                    barcode=(old['barcode'] or '').strip() or None,
                    legacy_novman_id=old['id'], last_buying_price=dec(old['buying_price']),
                    base_unit='PCS', low_stock_level=dec(old['reorder_level']),
                    is_active=True,
                )
                prod_map[old['id']] = prod
                ProductUnit.objects.create(
                    product=prod, name='Piece', symbol='PCS',
                    conversion_to_base=Decimal('1'),
                    selling_price=dec(old['selling_price']),
                    is_default=True, is_active=True,
                )
            positive_batches = 0
            for old in snap['inventory_productbatch']:
                quantity = dec(old['quantity_remaining'])
                if quantity == 0:
                    continue  # Historical exhausted batch, no opening stock to recreate.
                if old['branch_id'] not in branch_map:
                    raise CommandError(f'Non-spares batch #{old["id"]} has positive stock.')
                product = prod_map.get(old['product_id'])
                if product is None:
                    raise CommandError(f'Unknown batch product {old["product_id"]}')
                StockBatch.objects.create(
                    product=product,
                    branch=branch_map[old['branch_id']],
                    legacy_novman_id=old['id'],
                    legacy_selling_price=dec(old['selling_price']),
                    source='OPENING',
                    reference=f'Novman opening batch #{old["id"]}',
                    quantity_received_base=quantity,
                    quantity_remaining_base=quantity,
                    unit_cost_base=dec(old['buying_price']),
                    received_at=old['created_at'],
                    created_by=user,
                )
                positive_batches += 1
            if Product.objects.count() != count:
                raise CommandError('Post-import product count mismatch; changes rolled back.')
            target_total = StockBatch.objects.aggregate(v=Sum('quantity_remaining_base'))['v'] or Decimal(0)
            if target_total != expected_total:
                raise CommandError(f'Target aggregate stock {target_total} != source {expected_total}; rolled back.')
            dest_by_branch = defaultdict(Decimal)
            for row in StockBatch.objects.values('product__legacy_novman_id', 'branch__legacy_novman_id').annotate(q=Sum('quantity_remaining_base')):
                dest_by_branch[(row['product__legacy_novman_id'], row['branch__legacy_novman_id'])] += row['q']
            for key in set(expected_by_branch) | set(dest_by_branch):
                if expected_by_branch.get(key, Decimal(0)) != dest_by_branch.get(key, Decimal(0)):
                    raise CommandError(f'Target product/branch mismatch {key}; rolled back.')
            shop = ShopSettings.get_solo()
            shop.name = 'NOVMAN AUTO SPARE PARTS'
            shop.receipt_footer = 'Asante kwa kununua. Karibu tena.'
            shop.save(update_fields=['name', 'receipt_footer'])
        self.stdout.write(self.style.SUCCESS(
            f'IMPORTED: {count} products, {len(cat_map)} original categories, '
            f'{len(supplier_map)} suppliers, {len(branch_map)} spares branches, '
            f'{positive_batches} remaining stock batches, {expected_total} pieces.'
        ))
        self.stdout.write(self.style.WARNING(
            'IMPORTANT: Jane checkout/reporting are NOT branch-aware yet. DO NOT use this POS '
            'for real sales until branch functionality is implemented and tested.'
        ))
