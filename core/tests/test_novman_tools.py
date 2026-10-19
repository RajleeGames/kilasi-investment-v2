"""Safety tests for Novman V2 admin tools."""
from io import BytesIO
import zipfile

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from core.models import Branch, Sale


class NovmanAdminToolTests(TestCase):
    def setUp(self):
        self.a = Branch.objects.create(name='MAJENGO')
        self.b = Branch.objects.create(name='SIRONGA')
        self.admin = User.objects.create_superuser('manager', 'manager@example.com', 'testpass01!')
        self.cashier = User.objects.create_user('seller', password='testpass01!')
        self.cashier.profile.role = 'cashier'
        self.cashier.profile.branch = self.a
        self.cashier.profile.save()

    def test_cashier_denied_admin_tools(self):
        self.client.force_login(self.cashier)
        for name in ('branch_manage', 'data_tools', 'backup_download', 'export_all_zip'):
            with self.subTest(url=name):
                response = self.client.get(reverse(name))
                self.assertNotEqual(response.status_code, 200)
        self.assertNotEqual(self.client.get(reverse('export_csv', args=['sales'])).status_code, 200)

    def test_admin_can_add_branch_without_copying_stock(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('branch_manage'), {'action':'create', 'name': 'MOSHI TOWN'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Branch.objects.filter(name='MOSHI TOWN', is_active=True).exists())

    def test_legacy_store_name_removed_from_export(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session['novman_branch_id'] = self.a.pk
        session.save()
        response = self.client.get(reverse('export_csv', args=['stock']))
        self.assertEqual(response.status_code, 200)
        self.assertIn('novman_branch_', response['Content-Disposition'])
        self.assertNotIn('Msumari', response['Content-Disposition'])

    def test_branch_sales_export_does_not_leak_other_branch(self):
        Sale.objects.create(branch=self.a, cashier=self.admin, grand_total=1200)
        other = Sale.objects.create(branch=self.b, cashier=self.admin, grand_total=9000)
        self.client.force_login(self.admin)
        session = self.client.session
        session['novman_branch_id'] = self.a.pk
        session.save()
        response = self.client.get(reverse('export_csv', args=['sales']), {'start': '2000-01-01', 'end':'2099-12-31'})
        self.assertEqual(response.status_code, 200)
        csv_text = response.content.decode('utf-8-sig')
        self.assertIn('1200', csv_text)
        self.assertNotIn(other.receipt_no, csv_text)

    def test_empty_branch_cannot_be_deactivated_if_last(self):
        self.b.is_active = False
        self.b.save()
        self.client.force_login(self.admin)
        self.client.post(reverse('branch_manage'), {'action':'deactivate','branch_id':self.a.id})
        self.a.refresh_from_db()
        self.assertTrue(self.a.is_active)


class NovmanBackupTests(TransactionTestCase):
    def test_full_backup_zip_contains_db_and_manifest(self):
        Branch.objects.create(name='MAJENGO')
        Branch.objects.create(name='SIRONGA')
        admin = User.objects.create_superuser('backupadmin', 'backup@example.com', 'testpass01!')
        self.client.force_login(admin)
        response = self.client.get(reverse('backup_download'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('novman_auto_spares_full_backup_', response['Content-Disposition'])
        content = b''.join(response.streaming_content)
        response.close()
        with zipfile.ZipFile(BytesIO(content)) as archive:
            self.assertIn('database/db.sqlite3', archive.namelist())
            self.assertIn('manifest.json', archive.namelist())
            self.assertIn('RESTORE_README.txt', archive.namelist())
            self.assertIn(b'MAJENGO', archive.read('manifest.json'))
            self.assertIn(b'SIRONGA', archive.read('manifest.json'))
