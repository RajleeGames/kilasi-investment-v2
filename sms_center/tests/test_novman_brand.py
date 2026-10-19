from django.test import SimpleTestCase, TestCase, override_settings

from sms_center.models import SmsCampaign, SmsSenderProfile, SmsTemplate
from sms_center.views import _sender_options
from sms_center.management.commands.novman_sms_brand import NEW_BRAND, OLD_BRAND
from django.core.management import call_command
from django.contrib.auth import get_user_model
from io import StringIO


class BrandTextTests(SimpleTestCase):
    def test_branding_replacements_only_match_company_not_person_name(self):
        self.assertEqual(OLD_BRAND.sub(NEW_BRAND, 'Welcome to Jane Cosmetics!'), 'Welcome to NOVMAN AUTO SPARES!')
        self.assertEqual(OLD_BRAND.sub(NEW_BRAND, 'Hello Jane'), 'Hello Jane')


class SenderSelectionTests(TestCase):
    @override_settings(BEEM_SENDER_ID='NOVMAN')
    def test_environment_novman_overrides_legacy_profile_default(self):
        SmsSenderProfile.objects.create(sender_id='JANE', label='Old sender', is_default=True, is_active=True)
        values = _sender_options()
        self.assertEqual([item['value'] for item in values if item['default']], ['NOVMAN'])

    @override_settings(BEEM_SENDER_ID='NOVMAN')
    def test_saved_novman_sender_not_duplicated(self):
        SmsSenderProfile.objects.create(sender_id='NOVMAN', label='Novman', is_default=True, is_active=True)
        values = _sender_options()
        self.assertEqual([v['value'] for v in values].count('NOVMAN'), 1)
        self.assertEqual([v['value'] for v in values if v['default']], ['NOVMAN'])


class SmsBrandCommandTests(TestCase):
    def test_dry_run_preserves_templates_and_apply_changes_only_templates(self):
        t = SmsTemplate.objects.create(name='Jane Cosmetics Promo', body='Hello {first_name}, Jane Cosmetics welcomes you.', category='Promotion')
        out = StringIO()
        call_command('novman_sms_brand', stdout=out)
        t.refresh_from_db()
        self.assertIn('Jane Cosmetics', t.body)
        call_command('novman_sms_brand', '--apply', stdout=StringIO())
        t.refresh_from_db()
        self.assertIn('NOVMAN AUTO SPARES', t.body)
        self.assertNotIn('Jane Cosmetics', t.name)

    def test_sender_id_is_preserved(self):
        s = SmsSenderProfile.objects.create(sender_id='JANE', label='Old approved sender', is_default=True)
        call_command('novman_sms_brand', '--apply', stdout=StringIO())
        s.refresh_from_db()
        self.assertEqual(s.sender_id, 'JANE')
