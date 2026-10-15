"""Tests for the public pages app: pricing unification, public shell, legal
pages, contact form, ops-console messages, and SEO endpoints."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from billing.models import CreditPack, Plan

from .models import ContactMessage


def _user(email='pages@x.com'):
    return get_user_model().objects.create_user(email=email, password='pw12345678')


class PricingPageTests(TestCase):
    def setUp(self):
        self.pack = CreditPack.objects.create(name='Starter 100', credits=100,
                                              price=199, currency='INR')
        self.plan = Plan.objects.create(tier='Creator', price=499, currency='INR',
                                        credits_per_cycle=1000)

    def test_guest_gets_pricing_page(self):
        resp = self.client.get(reverse('pages:pricing'), HTTP_CF_IPCOUNTRY='IN')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Starter 100')
        self.assertContains(resp, 'Creator')

    def test_guest_buy_cta_goes_through_signup(self):
        resp = self.client.get(reverse('pages:pricing'), HTTP_CF_IPCOUNTRY='IN')
        signup = reverse('account_signup')
        self.assertContains(resp, f'{signup}?next=')
        self.assertContains(resp, f'buy%3Dpack-{self.pack.pk}')
        self.assertNotContains(resp, f'id="buy-pack-{self.pack.pk}"')

    def test_authenticated_buy_cta_posts_to_checkout(self):
        self.client.force_login(_user())
        resp = self.client.get(reverse('pages:pricing'), HTTP_CF_IPCOUNTRY='IN')
        self.assertContains(
            resp, reverse('billing:checkout_pack', args=[self.pack.pk]))
        self.assertContains(
            resp, reverse('billing:checkout_plan', args=[self.plan.pk]))

    def test_valid_buy_param_triggers_auto_checkout(self):
        self.client.force_login(_user())
        resp = self.client.get(reverse('pages:pricing'),
                               {'buy': f'pack-{self.pack.pk}'},
                               HTTP_CF_IPCOUNTRY='IN')
        self.assertContains(resp, f"getElementById('buy-pack-{self.pack.pk}')")

    def test_malformed_buy_param_is_ignored(self):
        self.client.force_login(_user())
        resp = self.client.get(reverse('pages:pricing'),
                               {'buy': '"><script>alert(1)</script>'},
                               HTTP_CF_IPCOUNTRY='IN')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'getElementById')

    def test_buy_param_ignored_for_guests(self):
        resp = self.client.get(reverse('pages:pricing'),
                               {'buy': f'pack-{self.pack.pk}'},
                               HTTP_CF_IPCOUNTRY='IN')
        self.assertNotContains(resp, 'getElementById')

    def test_empty_catalogue_never_500s(self):
        CreditPack.objects.all().delete()
        Plan.objects.all().delete()
        resp = self.client.get(reverse('pages:pricing'), HTTP_CF_IPCOUNTRY='IN')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Pricing is being finalised')

    def test_landing_and_pricing_render_identical_items(self):
        landing = self.client.get('/', HTTP_CF_IPCOUNTRY='IN')
        pricing = self.client.get(reverse('pages:pricing'), HTTP_CF_IPCOUNTRY='IN')
        for needle in ['Starter 100', 'Creator', 'Prices shown in INR.']:
            self.assertContains(landing, needle)
            self.assertContains(pricing, needle)


class BillingRedirectTests(TestCase):
    """The old billing listings 301 to /pricing/; checkout endpoints remain."""

    def test_packs_redirects_permanently(self):
        resp = self.client.get(reverse('billing:packs'))
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp['Location'], reverse('pages:pricing'))

    def test_plans_redirects_permanently(self):
        resp = self.client.get(reverse('billing:plans'))
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp['Location'], reverse('pages:pricing'))


class PublicPagesRenderTests(TestCase):
    """Every public route returns 200, extends the shared shell, and the footer
    links every legal page (Razorpay activation requires global visibility)."""

    PUBLIC_ROUTES = ['pages:about', 'pages:security', 'pages:contact',
                     'pages:terms', 'pages:privacy', 'pages:refund',
                     'pages:acceptable_use']

    def test_all_public_routes_render_with_shared_shell(self):
        for name in self.PUBLIC_ROUTES:
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, name)
            self.assertContains(resp, 'landing-nav', msg_prefix=name)
            self.assertContains(resp, 'landing-footer', msg_prefix=name)

    def test_footer_links_every_public_and_legal_page(self):
        resp = self.client.get(reverse('pages:about'))
        for name in ['pages:pricing'] + self.PUBLIC_ROUTES:
            self.assertContains(resp, f'href="{reverse(name)}"', msg_prefix=name)

    def test_legal_pages_carry_review_notice_and_effective_date(self):
        for name in ['pages:terms', 'pages:privacy', 'pages:refund',
                     'pages:acceptable_use']:
            resp = self.client.get(reverse(name))
            self.assertContains(resp, 'not legal advice', msg_prefix=name)
            self.assertContains(resp, 'Effective date:', msg_prefix=name)

    def test_landing_uses_shared_header_and_footer(self):
        resp = self.client.get('/')
        self.assertContains(resp, 'landing-nav')
        self.assertContains(resp, f'href="{reverse("pages:terms")}"')


@mock.patch('pages.forms.validate_turnstile', return_value=True)
class ContactFormTests(TestCase):
    def setUp(self):
        # The maintenance-mode flag is cached (opsconsole.flags, 5s TTL) and the
        # cache is not rolled back with the DB between tests, so a preceding
        # MaintenanceMiddlewareTests run can leak "maintenance on" into these
        # POSTs and 503 them (B-055). Clear it explicitly.
        from opsconsole import flags
        flags.invalidate(flags.MAINTENANCE)

    def _post(self, **overrides):
        data = {'name': 'Asha', 'email': 'asha@example.com',
                'subject': 'Hello', 'message': 'A question about plans.',
                'website': '', 'cf-turnstile-response': 'tok'}
        data.update(overrides)
        return self.client.post(reverse('pages:contact'), data)

    def test_valid_submission_stores_row_and_notifies_operator(self, _):
        resp = self._post()
        self.assertRedirects(resp, reverse('pages:contact'))
        msg = ContactMessage.objects.get()
        self.assertEqual(msg.email, 'asha@example.com')
        self.assertEqual(msg.status, ContactMessage.STATUS_NEW)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('SendGlob contact', mail.outbox[0].subject)

    def test_notification_failure_never_loses_the_message(self, _):
        with mock.patch('pages.views.send_mail', side_effect=OSError('smtp down')):
            resp = self._post()
        self.assertRedirects(resp, reverse('pages:contact'))
        self.assertEqual(ContactMessage.objects.count(), 1)

    def test_honeypot_rejects_bots(self, _):
        resp = self._post(website='http://spam.example')
        self.assertEqual(resp.status_code, 200)  # re-rendered with errors
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_failed_turnstile_rejects(self, mocked):
        mocked.return_value = False
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_missing_fields_rejected(self, _):
        resp = self._post(email='', message='')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ContactMessage.objects.count(), 0)

    @override_settings(CONTACT_RATE_LIMIT_PER_HOUR=2)
    def test_rate_limit_per_ip(self, _):
        # The limit is read at module import; patch the module constant instead.
        with mock.patch('pages.views.CONTACT_RATE_LIMIT_PER_HOUR', 2):
            self._post()
            self._post()
            resp = self._post()
        self.assertEqual(ContactMessage.objects.count(), 2)
        self.assertRedirects(resp, reverse('pages:contact'))

    def test_authenticated_sender_is_linked(self, _):
        user = _user('sender@x.com')
        self.client.force_login(user)
        self._post()
        self.assertEqual(ContactMessage.objects.get().user, user)


class OpsConsoleMessagesTests(TestCase):
    def setUp(self):
        self.operator = get_user_model().objects.create_user(
            email='ops@x.com', password='pw12345678', is_staff=True,
            is_superuser=True)
        self.msg = ContactMessage.objects.create(
            name='Asha', email='asha@example.com', message='Hi')

    def test_non_staff_gets_404(self):
        self.client.force_login(_user('plain@x.com'))
        self.assertEqual(
            self.client.get(reverse('opsconsole:messages')).status_code, 404)

    def test_staff_sees_messages_with_status_filter(self):
        self.client.force_login(self.operator)
        resp = self.client.get(reverse('opsconsole:messages'))
        self.assertContains(resp, 'asha@example.com')
        resp = self.client.get(reverse('opsconsole:messages'), {'status': 'resolved'})
        self.assertNotContains(resp, 'asha@example.com')

    def test_status_change_is_audited(self):
        from opsconsole.models import AdminAuditLog

        self.client.force_login(self.operator)
        resp = self.client.post(
            reverse('opsconsole:message_set_status', args=[self.msg.pk]),
            {'status': 'resolved'})
        self.assertRedirects(resp, reverse('opsconsole:messages'))
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.status, 'resolved')
        self.assertEqual(self.msg.handled_by, self.operator)
        log = AdminAuditLog.objects.get(action='contact_message_status')
        self.assertEqual(log.detail, {'from': 'new', 'to': 'resolved'})

    def test_invalid_status_rejected(self):
        self.client.force_login(self.operator)
        self.client.post(
            reverse('opsconsole:message_set_status', args=[self.msg.pk]),
            {'status': 'bogus'})
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.status, 'new')


class SeoEndpointTests(TestCase):
    def test_robots_txt(self):
        resp = self.client.get('/robots.txt')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'text/plain')
        body = resp.content.decode()
        self.assertIn('Disallow: /dashboard/', body)
        self.assertIn('Sitemap:', body)

    def test_sitemap_lists_public_routes(self):
        resp = self.client.get('/sitemap.xml')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        for name in ['pages:pricing', 'pages:about', 'pages:security',
                     'pages:terms', 'pages:privacy']:
            self.assertIn(reverse(name), body)
        self.assertNotIn('/dashboard/', body)

    def test_landing_has_meta_and_og(self):
        resp = self.client.get('/')
        self.assertContains(resp, 'name="description"')
        self.assertContains(resp, 'property="og:title"')
        self.assertContains(resp, 'rel="canonical"')
