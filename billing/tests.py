import hashlib
import hmac

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from unittest import mock

from billing import accounting, fulfillment, gating, receipts, tasks, wallet, webhooks
from billing.models import (CreditPack, CreditTransaction, CreditWallet, PackOrder, Payment,
                            Plan, Receipt, Refund, Subscription)

User = get_user_model()


def _user(email='u@x.com', **kw):
    return User.objects.create_user(email=email, password='pw', **kw)


@override_settings(BILLING_FREE_SIGNUP_CREDITS=0)
class WalletTests(TestCase):
    def test_credit_and_debit_update_balance_and_lifetimes(self):
        u = _user()
        wallet.credit(u, 100, transaction_type=CreditTransaction.TYPE_PACK_PURCHASE)
        wallet.debit(u, 30, transaction_type=CreditTransaction.TYPE_DEBIT_DOCUMENT)
        w = CreditWallet.objects.get(user=u)
        self.assertEqual(w.balance, 70)
        self.assertEqual(w.lifetime_earned, 100)
        self.assertEqual(w.lifetime_spent, 30)

    def test_balance_after_snapshots(self):
        u = _user()
        t1 = wallet.credit(u, 10, transaction_type=CreditTransaction.TYPE_SIGNUP_GRANT)
        t2 = wallet.debit(u, 4, transaction_type=CreditTransaction.TYPE_DEBIT_DOCUMENT)
        self.assertEqual(t1.balance_after, 10)
        self.assertEqual(t2.balance_after, 6)

    def test_debit_beyond_balance_raises(self):
        u = _user()
        wallet.credit(u, 5, transaction_type=CreditTransaction.TYPE_PACK_PURCHASE)
        with self.assertRaises(wallet.InsufficientCreditsError):
            wallet.debit(u, 6, transaction_type=CreditTransaction.TYPE_DEBIT_DOCUMENT)
        self.assertEqual(wallet.balance(u), 5)

    def test_signup_grant_applied(self):
        with override_settings(BILLING_FREE_SIGNUP_CREDITS=50):
            u = _user('free@x.com')
        self.assertEqual(wallet.balance(u), 50)
        self.assertTrue(CreditTransaction.objects.filter(
            user=u, transaction_type=CreditTransaction.TYPE_SIGNUP_GRANT).exists())


@override_settings(BILLING_FREE_SIGNUP_CREDITS=0)
class GatingTests(TestCase):
    def test_tier_progression(self):
        u = _user()
        self.assertEqual(gating.get_tier(u)[0], 'free')
        Payment.objects.create(user=u, payment_type=Payment.PACK, razorpay_payment_id='p1',
                               amount=10, currency='INR', status=Payment.CAPTURED)
        self.assertEqual(gating.get_tier(u)[0], 'payg')
        plan = Plan.objects.create(tier='Creator', price=499, credits_per_cycle=1000)
        Subscription.objects.create(user=u, plan=plan, status=Subscription.ACTIVE)
        self.assertEqual(gating.get_tier(u)[0], 'Creator')

    def test_recipient_cap_free_vs_paid(self):
        u = _user()
        self.assertIsNotNone(gating.recipient_cap(u))  # free capped
        plan = Plan.objects.create(tier='Biz', price=1, credits_per_cycle=1)
        Subscription.objects.create(user=u, plan=plan, status=Subscription.ACTIVE)
        self.assertIsNone(gating.recipient_cap(u))  # paid uncapped

    def test_smtp_config_limit_enforced(self):
        from accounts.models import SMTPConfig
        u = _user('smtp_user@x.com')
        self.client.force_login(u)
        SMTPConfig.objects.create(user=u, name='S1', smtp_username='s1@x.com', is_active=True)
        resp = self.client.post(reverse('accounts:smtp_create'), {
            'name': 'S2', 'smtp_host': 'smtp.gmail.com', 'smtp_port': 587,
            'smtp_username': 's2@x.com', 'smtp_password': 'pw', 'use_tls': True,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(SMTPConfig.active.filter(user=u).count(), 1)

    def test_recurring_schedule_gated_by_plan(self):
        from datetime import time
        from accounts.models import SMTPConfig
        from emails.forms import EmailCampaignForm
        from emails.models import EmailTemplate
        u = _user('sched_user@x.com')
        smtp = SMTPConfig.objects.create(user=u, name='s', smtp_username='s@x.com')
        tpl = EmailTemplate.objects.create(user=u, name='t', subject='sub')
        form_free = EmailCampaignForm(
            data={'name': 'C1', 'template': tpl.pk, 'smtp_config': smtp.pk,
                  'schedule_type': 'interval', 'schedule_time': time(9, 0),
                  'interval_days': 1, 'is_recurring': True},
            user=u
        )
        self.assertFalse(form_free.is_valid())
        self.assertIn('is_recurring', form_free.errors)

        plan = Plan.objects.create(tier='Pro', price=999, credits_per_cycle=5000, recurring_scheduling=True)
        Subscription.objects.create(user=u, plan=plan, status=Subscription.ACTIVE)
        form_paid = EmailCampaignForm(
            data={'name': 'C1', 'template': tpl.pk, 'smtp_config': smtp.pk,
                  'schedule_type': 'interval', 'schedule_time': time(9, 0),
                  'interval_days': 1, 'is_recurring': True},
            user=u
        )
        self.assertTrue(form_paid.is_valid())

    def test_campaign_queue_blocks_exceeding_recipient_cap(self):
        from accounts.models import SMTPConfig
        from emails.models import EmailCampaign, Recipient
        u = _user('queue_user@x.com')
        smtp = SMTPConfig.objects.create(user=u, name='c', smtp_username='s@x.com')
        c = EmailCampaign.objects.create(user=u, name='Large', smtp_config=smtp, status='draft')
        for i in range(55):
            Recipient.objects.create(campaign=c, email=f'r{i}@x.com', name=f'R{i}', status='pending')
        self.client.force_login(u)
        resp = self.client.get(reverse('emails:campaign_queue', args=[c.pk]))
        self.assertEqual(resp.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.status, 'draft')


@override_settings(BILLING_FREE_SIGNUP_CREDITS=0)
class WebhookTests(TestCase):
    def _order(self, user, credits=100, price=10, gateway_order_id='order_1'):
        pack = CreditPack.objects.create(name='P', credits=credits, price=price, currency='INR')
        return PackOrder.objects.create(user=user, pack=pack, credits=credits, amount=price,
                                        currency='INR', gateway_order_id=gateway_order_id)

    def _payload(self, pay_id, order_id='order_1', event='payment.captured'):
        return {'event': event, 'payment': {'entity': {
            'id': pay_id, 'order_id': order_id, 'amount': 1000, 'currency': 'INR'}}}

    def test_capture_credits_once_and_is_idempotent(self):
        u = _user()
        order = self._order(u)
        payload = self._payload('pay_1')
        self.assertEqual(webhooks.process_event('evt_1', 'payment.captured', payload), 'processed')
        self.assertEqual(webhooks.process_event('evt_1', 'payment.captured', payload), 'duplicate')
        # A different event id for the same payment (gateway retry) is also a no-op.
        webhooks.process_event('evt_1b', 'payment.captured', payload)
        self.assertEqual(Payment.objects.filter(razorpay_payment_id='pay_1').count(), 1)
        self.assertEqual(wallet.balance(u), 100)
        self.assertTrue(Receipt.objects.filter(payment__razorpay_payment_id='pay_1').exists())
        order.refresh_from_db()
        self.assertEqual(order.status, PackOrder.PAID)
        self.assertEqual(order.payment.description, 'P credit pack')

    def test_capture_uses_order_snapshot_not_current_catalogue(self):
        u = _user()
        order = self._order(u, credits=100, price=10)
        order.pack.credits = 9999  # repriced after the order was opened
        order.pack.save()
        webhooks.process_event('evt_snap', 'payment.captured', self._payload('pay_snap'))
        self.assertEqual(wallet.balance(u), 100)

    def test_capture_for_unknown_order_grants_nothing(self):
        u = _user()
        self._order(u)
        webhooks.process_event('evt_x', 'payment.captured', self._payload('pay_x', 'order_other'))
        self.assertFalse(Payment.objects.filter(razorpay_payment_id='pay_x').exists())
        self.assertEqual(wallet.balance(u), 0)

    def test_late_capture_after_failure_still_credits(self):
        u = _user()
        order = self._order(u)
        webhooks.process_event('evt_f', 'payment.failed', self._payload('pay_late', event='payment.failed'))
        self.assertEqual(Payment.objects.get(razorpay_payment_id='pay_late').status, Payment.FAILED)
        order.refresh_from_db()
        self.assertEqual(order.status, PackOrder.FAILED)
        webhooks.process_event('evt_c', 'payment.captured', self._payload('pay_late'))
        self.assertEqual(Payment.objects.get(razorpay_payment_id='pay_late').status, Payment.CAPTURED)
        self.assertEqual(wallet.balance(u), 100)
        order.refresh_from_db()
        self.assertEqual(order.status, PackOrder.PAID)

    def test_handler_failure_leaves_event_unprocessed_for_retry(self):
        u = _user()
        self._order(u)
        with mock.patch('billing.fulfillment.receipts.issue_receipt', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                webhooks.process_event('evt_retry', 'payment.captured', self._payload('pay_r'))
        self.assertEqual(wallet.balance(u), 0)
        self.assertFalse(Payment.objects.filter(razorpay_payment_id='pay_r').exists())
        self.assertEqual(webhooks.process_event('evt_retry', 'payment.captured',
                                                self._payload('pay_r')), 'processed')
        self.assertEqual(wallet.balance(u), 100)

    def test_partial_then_full_refund_claws_back_proportionally(self):
        u = _user()
        self._order(u, credits=100, price=10)
        webhooks.process_event('evt_cap', 'payment.captured', self._payload('pay_ref'))
        refund = {'refund': {'entity': {'id': 'rfnd_1', 'payment_id': 'pay_ref', 'amount': 400}}}
        webhooks.process_event('evt_rf1', 'refund.processed', refund)
        webhooks.process_event('evt_rf1b', 'refund.processed', refund)  # replay under a new event id
        pay = Payment.objects.get(razorpay_payment_id='pay_ref')
        self.assertEqual(pay.status, Payment.PARTIALLY_REFUNDED)
        self.assertEqual(wallet.balance(u), 60)
        webhooks.process_event('evt_rf2', 'refund.processed', {'refund': {'entity': {
            'id': 'rfnd_2', 'payment_id': 'pay_ref', 'amount': 600}}})
        pay.refresh_from_db()
        self.assertEqual(pay.status, Payment.REFUNDED)
        self.assertEqual(wallet.balance(u), 0)
        self.assertEqual(Refund.objects.filter(payment=pay).count(), 2)
        self.assertEqual(accounting.reconcile(), [])

    def test_refund_after_credits_spent_records_shortfall(self):
        u = _user()
        self._order(u, credits=100, price=10)
        webhooks.process_event('evt_cap2', 'payment.captured', self._payload('pay_spent'))
        wallet.debit(u, 70, transaction_type=CreditTransaction.TYPE_DEBIT_DOCUMENT)
        webhooks.process_event('evt_rf3', 'refund.processed', {'refund': {'entity': {
            'id': 'rfnd_3', 'payment_id': 'pay_spent', 'amount': 1000}}})
        refund = Refund.objects.get(razorpay_refund_id='rfnd_3')
        self.assertEqual((refund.credits_owed, refund.credits_reversed), (100, 30))
        self.assertEqual(wallet.balance(u), 0)
        self.assertEqual(accounting.reconcile(), [])

    @override_settings(RAZORPAY_WEBHOOK_SECRET='whsec')
    def test_webhook_signature_verification(self):
        from billing import gateway
        body = b'{"event":"x"}'
        good = hmac.new(b'whsec', body, hashlib.sha256).hexdigest()
        self.assertTrue(gateway.verify_webhook_signature(body, good))
        self.assertFalse(gateway.verify_webhook_signature(body, 'bad'))

    def test_subscription_charged_creates_payment_receipt_and_credits_once(self):
        u = _user('sub_user@x.com')
        plan = Plan.objects.create(tier='Creator', price=499, currency='INR', credits_per_cycle=1000)
        sub = Subscription.objects.create(user=u, plan=plan, razorpay_subscription_id='sub_123', status=Subscription.PENDING)
        payload = {
            'event': 'subscription.charged',
            'subscription': {'entity': {
                'id': 'sub_123',
                'current_start': 1700000000,
                'current_end': 1702592000,
            }},
            'payment': {'entity': {
                'id': 'pay_sub_1',
                'order_id': 'ord_sub_1',
                'amount': 49900,
                'currency': 'INR',
            }}
        }
        res = webhooks.process_event('evt_sub_1', 'subscription.charged', payload)
        self.assertEqual(res, 'processed')
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.ACTIVE)
        self.assertIsNotNone(sub.current_period_start)
        self.assertIsNotNone(sub.current_period_end)
        self.assertEqual(wallet.balance(u), 1000)
        pay = Payment.objects.filter(razorpay_payment_id='pay_sub_1').first()
        self.assertIsNotNone(pay)
        self.assertEqual(pay.payment_type, Payment.PLAN)
        self.assertTrue(Receipt.objects.filter(payment=pay).exists())

    def test_subscription_and_payment_captured_no_double_credit(self):
        u = _user('sub_user2@x.com')
        plan = Plan.objects.create(tier='Creator', price=499, currency='INR', credits_per_cycle=1000)
        sub = Subscription.objects.create(user=u, plan=plan, razorpay_subscription_id='sub_456', status=Subscription.PENDING)
        # Event 1: subscription.charged
        payload_sub = {
            'event': 'subscription.charged',
            'subscription': {'entity': {'id': 'sub_456', 'current_start': 1700000000, 'current_end': 1702592000}},
            'payment': {'entity': {'id': 'pay_sub_2', 'amount': 49900, 'currency': 'INR'}}
        }
        webhooks.process_event('evt_sub_2', 'subscription.charged', payload_sub)
        self.assertEqual(wallet.balance(u), 1000)

        # Event 2: payment.captured for same payment
        payload_pay = {
            'event': 'payment.captured',
            'payment': {'entity': {
                'id': 'pay_sub_2',
                'subscription_id': 'sub_456',
                'amount': 49900,
                'currency': 'INR',
                'notes': {'user_id': str(u.pk), 'kind': 'plan', 'plan_id': str(plan.pk)}
            }}
        }
        webhooks.process_event('evt_sub_2_pay', 'payment.captured', payload_pay)
        # Still 1000, NOT 2000!
        self.assertEqual(wallet.balance(u), 1000)

    def test_subscription_lifecycle_states(self):
        u = _user('sub_user3@x.com')
        plan = Plan.objects.create(tier='Biz', price=999, currency='INR')
        sub = Subscription.objects.create(user=u, plan=plan, razorpay_subscription_id='sub_789', status=Subscription.ACTIVE)

        webhooks.process_event('evt_halt', 'subscription.halted', {'subscription': {'entity': {'id': 'sub_789'}}})
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.PAST_DUE)

        webhooks.process_event('evt_resume', 'subscription.resumed', {'subscription': {'entity': {'id': 'sub_789'}}})
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.ACTIVE)

        webhooks.process_event('evt_pause', 'subscription.paused', {'subscription': {'entity': {'id': 'sub_789'}}})
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.PAST_DUE)

        webhooks.process_event('evt_done', 'subscription.completed', {'subscription': {'entity': {'id': 'sub_789'}}})
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.EXPIRED)

    def test_renewal_extends_period_and_grants_each_cycle(self):
        u = _user('renew@x.com')
        plan = Plan.objects.create(tier='Creator', price=499, currency='INR', credits_per_cycle=1000)
        sub = Subscription.objects.create(user=u, plan=plan, razorpay_subscription_id='sub_r',
                                          status=Subscription.PENDING)
        for i, (start, end) in enumerate([(1700000000, 1702592000), (1702592000, 1705184000)]):
            webhooks.process_event(f'evt_ch_{i}', 'subscription.charged', {
                'subscription': {'entity': {'id': 'sub_r', 'current_start': start, 'current_end': end}},
                'payment': {'entity': {'id': f'pay_r_{i}', 'amount': 49900, 'currency': 'INR'}}})
        sub.refresh_from_db()
        self.assertEqual(int(sub.current_period_end.timestamp()), 1705184000)
        self.assertEqual(wallet.balance(u), 2000)
        self.assertEqual(Receipt.objects.filter(payment__user=u).count(), 2)


@override_settings(BILLING_FREE_SIGNUP_CREDITS=0)
class ReceiptTests(TestCase):
    def _payment(self, u, pid):
        return Payment.objects.create(user=u, payment_type=Payment.PACK,
                                      razorpay_payment_id=pid, amount=10, currency='INR',
                                      status=Payment.CAPTURED, credits_granted=100,
                                      paid_at=timezone.now())

    def test_sequential_gap_free_numbering(self):
        u = _user()
        r1 = receipts.issue_receipt(self._payment(u, 'p1'))
        r2 = receipts.issue_receipt(self._payment(u, 'p2'))
        year = timezone.now().year
        self.assertEqual(r1.number, f"SG-{year}-000001")
        self.assertEqual(r2.number, f"SG-{year}-000002")

    def test_issue_receipt_idempotent(self):
        u = _user()
        p = self._payment(u, 'p3')
        r1 = receipts.issue_receipt(p)
        r2 = receipts.issue_receipt(p)
        self.assertEqual(r1.pk, r2.pk)

    def test_receipt_download_ownership(self):
        owner = _user('owner@x.com')
        other = _user('other@x.com')
        staff = _user('staff@x.com', is_staff=True)
        r = receipts.issue_receipt(self._payment(owner, 'p4'))
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('billing:receipt_download', args=[r.pk])).status_code, 404)
        self.client.force_login(owner)
        self.assertEqual(self.client.get(reverse('billing:receipt_download', args=[r.pk])).status_code, 200)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse('billing:receipt_download', args=[r.pk])).status_code, 200)


@override_settings(BILLING_FREE_SIGNUP_CREDITS=0)
class DocumentMeteringTests(TestCase):
    def _campaign(self, user, n_recipients):
        from accounts.models import SMTPConfig
        from documents.models import DocumentTemplate
        from emails.models import EmailCampaign, Recipient
        smtp = SMTPConfig.objects.create(user=user, smtp_username='s@x.com', encrypted_password='x')
        dt = DocumentTemplate.objects.create(
            user=user, name='T',
            template_file=SimpleUploadedFile('t.png', b'\x89PNG\r\n', content_type='image/png'))
        c = EmailCampaign.objects.create(user=user, name='C', smtp_config=smtp, status='queued',
                                         is_document_campaign=True, document_template=dt)
        for i in range(n_recipients):
            Recipient.objects.create(campaign=c, email=f'r{i}@x.com', name=f'R{i}', status='pending')
        return c, dt

    def test_debits_one_per_success_and_stops_when_out_of_credits(self):
        from documents.tasks import generate_documents_task
        u = _user()
        wallet.credit(u, 2, transaction_type=CreditTransaction.TYPE_PACK_PURCHASE)
        c, dt = self._campaign(u, 3)
        with mock.patch('documents.tasks.save_document_for_recipient') as save:
            save.return_value = None
            generate_documents_task(c.id, dt.id, False)
        # Exactly 2 documents charged (balance was 2); third stopped out of credits.
        self.assertEqual(wallet.balance(u), 0)
        self.assertEqual(CreditTransaction.objects.filter(
            user=u, transaction_type=CreditTransaction.TYPE_DEBIT_DOCUMENT).count(), 2)
        from emails.models import Recipient
        self.assertTrue(Recipient.objects.filter(campaign=c, error_message__icontains='out of credits').exists())


@override_settings(BILLING_FREE_SIGNUP_CREDITS=0)
class AccountingTests(TestCase):
    def test_monthly_revenue_and_cost_floor(self):
        u = _user()
        Payment.objects.create(user=u, payment_type=Payment.PACK, razorpay_payment_id='r1',
                               amount=500, currency='INR', status=Payment.CAPTURED,
                               paid_at=timezone.now())
        now = timezone.now()
        rev = accounting.monthly_revenue(now.year, now.month)
        self.assertEqual(rev['by_currency'].get('INR'), 500.0)
        with override_settings(BILLING_INFRA_COST_FLOOR=300):
            net = accounting.cost_floor_net(now.year, now.month)
        self.assertTrue(net['above_floor'])
        self.assertEqual(net['net'], 200.0)

    def test_reconcile_catches_all_discrepancies(self):
        from billing.models import WebhookEvent
        u = _user('reconcile_user@x.com')
        # 1. Missing receipt for captured payment
        Payment.objects.create(user=u, payment_type=Payment.PACK, razorpay_payment_id='p_rec_1',
                               amount=100, currency='INR', status=Payment.CAPTURED, paid_at=timezone.now())
        # 2. Refunded payment without refund ledger reversal
        Payment.objects.create(user=u, payment_type=Payment.PACK, razorpay_payment_id='p_ref_1',
                               amount=100, currency='INR', status=Payment.REFUNDED,
                               credits_granted=50, paid_at=timezone.now())
        # 3. Webhook captured without local payment
        WebhookEvent.objects.create(event_id='wh_unmatched', event_type='payment.captured',
                                    payload={'payment': {'entity': {'id': 'pay_ghost_1'}}},
                                    processed_at=timezone.now())
        # 4. Wallet balance mismatch
        w = CreditWallet.objects.get(user=u)
        w.balance = 9999  # Corrupt balance
        w.save(update_fields=['balance'])

        issues = accounting.reconcile()
        self.assertTrue(any('p_rec_1' in s for s in issues))
        self.assertTrue(any('p_ref_1' in s for s in issues))
        self.assertTrue(any('pay_ghost_1' in s for s in issues))
        self.assertTrue(any('wallet balance mismatch' in s for s in issues))

    def test_reconcile_command_fix_missing_receipts(self):
        from django.core.management import call_command
        u = _user('fix_user@x.com')
        p = Payment.objects.create(user=u, payment_type=Payment.PACK, razorpay_payment_id='p_fix_1',
                                   amount=100, currency='INR', status=Payment.CAPTURED, paid_at=timezone.now())
        self.assertFalse(Receipt.objects.filter(payment=p).exists())
        call_command('reconcile_payments', fix_missing_receipts=True)
        self.assertTrue(Receipt.objects.filter(payment=p).exists())


@override_settings(BILLING_FREE_SIGNUP_CREDITS=0)
class PurgeGuardrailTests(TestCase):
    def test_financial_records_survive_purge(self):
        from core.tasks import purge_soft_deleted_records
        u = _user()
        wallet.credit(u, 10, transaction_type=CreditTransaction.TYPE_PACK_PURCHASE)
        p = Payment.objects.create(user=u, payment_type=Payment.PACK, razorpay_payment_id='pp',
                                   amount=10, currency='INR', status=Payment.CAPTURED,
                                   paid_at=timezone.now())
        r = receipts.issue_receipt(p)
        purge_soft_deleted_records()
        self.assertTrue(CreditTransaction.objects.filter(user=u).exists())
        self.assertTrue(Payment.objects.filter(pk=p.pk).exists())
        self.assertTrue(Receipt.objects.filter(pk=r.pk).exists())


@override_settings(BILLING_FREE_SIGNUP_CREDITS=50)
class BillingUIRenderTests(TestCase):
    def setUp(self):
        self.user = _user('ui@x.com')
        self.client.force_login(self.user)
        self.pack = CreditPack.objects.create(name='Starter 100', credits=100,
                                               price=199, currency='INR', display_order=1)
        self.plan = Plan.objects.create(tier='Creator', price=499, currency='INR',
                                        credits_per_cycle=1000, recurring_scheduling=True)

    def test_all_get_pages_render(self):
        # packs/plans are now permanent redirects to the public /pricing/ page
        # (pricing unification); see pages.tests.BillingRedirectTests.
        for name in ['dashboard', 'history', 'manage_subscription', 'profile']:
            resp = self.client.get(reverse(f'billing:{name}'))
            self.assertEqual(resp.status_code, 200, name)

    def test_checkout_pack_renders(self):
        # This is the page that raised VariableDoesNotExist on item.tier.
        resp = self.client.post(reverse('billing:checkout_pack', args=[self.pack.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Starter 100')
        self.assertContains(resp, '199')

    @override_settings(RAZORPAY_KEY_ID='rzp_test_x', RAZORPAY_KEY_SECRET='sek')
    def test_checkout_live_mode_renders_razorpay_button(self):
        # With keys + SDK, checkout runs live: renders the Razorpay checkout, not
        # the manual notice. Patch order creation so no network call is made.
        with mock.patch('billing.gateway.is_live', return_value=True), \
             mock.patch('billing.gateway.create_order',
                        return_value={'id': 'order_live_1', 'amount': 19900}):
            resp = self.client.post(reverse('billing:checkout_pack', args=[self.pack.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'checkout.razorpay.com')
        self.assertContains(resp, 'rzp_test_x')
        self.assertNotContains(resp, 'Manual payment')

    def test_checkout_pack_opens_order_with_snapshot(self):
        self.client.post(reverse('billing:checkout_pack', args=[self.pack.pk]))
        order = PackOrder.objects.get(user=self.user)
        self.assertEqual((order.credits, order.amount, order.status), (100, 199, PackOrder.CREATED))
        self.assertIsNone(order.gateway_order_id)  # manual mode
        resp = self.client.get(reverse('billing:dashboard'))
        self.assertContains(resp, order.short_reference)

    @override_settings(RAZORPAY_KEY_ID='rzp_test_x', RAZORPAY_KEY_SECRET='sek')
    def test_checkout_gateway_failure_does_not_render_broken_checkout(self):
        from billing import gateway
        with mock.patch('billing.gateway.create_order', side_effect=gateway.GatewayError('down')):
            resp = self.client.post(reverse('billing:checkout_pack', args=[self.pack.pk]))
        self.assertRedirects(resp, reverse('billing:dashboard'))
        self.assertEqual(PackOrder.objects.get(user=self.user).status, PackOrder.FAILED)

    @override_settings(RAZORPAY_KEY_ID='rzp_test_x', RAZORPAY_KEY_SECRET='sek')
    def test_checkout_complete_verifies_signature_and_fulfils_once(self):
        order = PackOrder.objects.create(user=self.user, pack=self.pack, credits=100, amount=199,
                                         currency='INR', gateway_order_id='order_cc')
        start = wallet.balance(self.user)
        data = {'razorpay_order_id': 'order_cc', 'razorpay_payment_id': 'pay_cc',
                'razorpay_signature': 'forged'}
        self.client.post(reverse('billing:checkout_complete'), data)
        self.assertEqual(wallet.balance(self.user), start)

        data['razorpay_signature'] = hmac.new(b'sek', b'order_cc|pay_cc', hashlib.sha256).hexdigest()
        self.client.post(reverse('billing:checkout_complete'), data)
        self.client.post(reverse('billing:checkout_complete'), data)
        self.assertEqual(wallet.balance(self.user), start + 100)
        # The webhook arriving afterwards is a no-op.
        webhooks.process_event('evt_cc', 'payment.captured', {'payment': {'entity': {
            'id': 'pay_cc', 'order_id': 'order_cc', 'amount': 19900, 'currency': 'INR'}}})
        self.assertEqual(wallet.balance(self.user), start + 100)
        order.refresh_from_db()
        self.assertEqual(order.status, PackOrder.PAID)

    @override_settings(RAZORPAY_KEY_ID='rzp_test_x', RAZORPAY_KEY_SECRET='sek')
    def test_checkout_complete_rejects_another_users_order(self):
        other = _user('other_cc@x.com')
        PackOrder.objects.create(user=other, pack=self.pack, credits=100, amount=199,
                                 currency='INR', gateway_order_id='order_theirs')
        sig = hmac.new(b'sek', b'order_theirs|pay_t', hashlib.sha256).hexdigest()
        self.client.post(reverse('billing:checkout_complete'), {
            'razorpay_order_id': 'order_theirs', 'razorpay_payment_id': 'pay_t',
            'razorpay_signature': sig})
        self.assertFalse(Payment.objects.filter(razorpay_payment_id='pay_t').exists())

    @override_settings(RAZORPAY_KEY_ID='rzp_test_x', RAZORPAY_KEY_SECRET='sek')
    def test_checkout_complete_activates_subscription(self):
        sub = Subscription.objects.create(user=self.user, plan=self.plan, status=Subscription.PENDING,
                                          razorpay_subscription_id='sub_cc')
        sig = hmac.new(b'sek', b'pay_s|sub_cc', hashlib.sha256).hexdigest()
        self.client.post(reverse('billing:checkout_complete'), {
            'razorpay_subscription_id': 'sub_cc', 'razorpay_payment_id': 'pay_s',
            'razorpay_signature': sig})
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.ACTIVE)
        self.assertIsNotNone(sub.current_period_end)
        self.assertEqual(gating.get_tier(self.user)[0], 'Creator')

    @override_settings(RAZORPAY_KEY_ID='rzp_test_x', RAZORPAY_KEY_SECRET='sek')
    def test_checkout_plan_links_gateway_plan_on_first_use(self):
        with mock.patch('billing.gateway.is_live', return_value=True), \
             mock.patch('billing.gateway.create_plan', return_value='plan_auto') as create, \
             mock.patch('billing.gateway.create_subscription',
                        side_effect=[{'id': 'sub_auto'}, {'id': 'sub_auto2'}]) as create_sub:
            resp = self.client.post(reverse('billing:checkout_plan', args=[self.plan.pk]))
            self.client.post(reverse('billing:checkout_plan', args=[self.plan.pk]))
        self.assertEqual(resp.status_code, 200)
        create.assert_called_once()  # linked once, reused afterwards
        self.assertEqual(create_sub.call_args[0][0], 'plan_auto')
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.razorpay_plan_id, 'plan_auto')
        self.assertEqual(Subscription.objects.get(razorpay_subscription_id='sub_auto').gateway_plan_id,
                         'plan_auto')

    @override_settings(RAZORPAY_KEY_ID='rzp_test_x', RAZORPAY_KEY_SECRET='sek')
    def test_checkout_plan_reports_unavailable_plan_accurately(self):
        from billing import gateway
        with mock.patch('billing.gateway.is_live', return_value=True), \
             mock.patch('billing.gateway.create_plan', side_effect=gateway.GatewayError('401')):
            resp = self.client.post(reverse('billing:checkout_plan', args=[self.plan.pk]), follow=True)
        self.assertContains(resp, 'cannot be purchased online right now')
        self.assertFalse(Subscription.objects.filter(user=self.user).exists())

    def test_checkout_other_plan_blocked_while_subscribed(self):
        other = Plan.objects.create(tier='Business', price=1499, currency='INR')
        Subscription.objects.create(user=self.user, plan=self.plan, status=Subscription.ACTIVE)
        resp = self.client.post(reverse('billing:checkout_plan', args=[other.pk]))
        self.assertRedirects(resp, reverse('billing:manage_subscription'))
        self.assertFalse(Subscription.objects.filter(plan=other).exists())

    def test_checkout_plan_renders_and_creates_pending_subscription(self):
        resp = self.client.post(reverse('billing:checkout_plan', args=[self.plan.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Creator')
        self.assertTrue(Subscription.objects.filter(
            user=self.user, plan=self.plan, status=Subscription.PENDING).exists())

    def test_manage_subscription_with_active_sub(self):
        Subscription.objects.create(user=self.user, plan=self.plan,
                                    status=Subscription.ACTIVE)
        resp = self.client.get(reverse('billing:manage_subscription'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Creator')

    def test_dashboard_shows_balance(self):
        resp = self.client.get(reverse('billing:dashboard'))
        self.assertContains(resp, '50')  # free signup credits balance

    def test_profile_rejects_invalid_country(self):
        resp = self.client.post(reverse('billing:profile'), {'country': '1N'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'two-letter ISO country code')
        self.user.refresh_from_db()
        self.assertEqual(self.user.country, '')

    def test_profile_save(self):
        resp = self.client.post(reverse('billing:profile'),
                                {'full_name': 'Sachin', 'country': 'in', 'city': 'Pune',
                                 'billing_name': 'Sachin', 'address_line1': 'A1'})
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, 'Sachin')
        self.assertEqual(self.user.country, 'IN')
        from billing.models import BillingProfile
        bp = BillingProfile.objects.get(user=self.user)
        self.assertEqual(bp.billing_name, 'Sachin')
        self.assertEqual(bp.country, 'IN')

    def test_cancel_subscription_view(self):
        sub = Subscription.objects.create(user=self.user, plan=self.plan,
                                           razorpay_subscription_id='sub_live_99',
                                           status=Subscription.ACTIVE)
        with mock.patch('billing.gateway.cancel_subscription') as cancel:
            resp = self.client.post(reverse('billing:cancel_subscription'))
        cancel.assert_called_once_with('sub_live_99', cancel_at_period_end=True)
        self.assertEqual(resp.status_code, 302)
        sub.refresh_from_db()
        self.assertTrue(sub.cancel_at_period_end)

    def test_cancel_subscription_gateway_failure_changes_nothing(self):
        from billing import gateway
        sub = Subscription.objects.create(user=self.user, plan=self.plan,
                                          razorpay_subscription_id='sub_live_98',
                                          status=Subscription.ACTIVE)
        with mock.patch('billing.gateway.cancel_subscription',
                        side_effect=gateway.GatewayError('down')):
            self.client.post(reverse('billing:cancel_subscription'))
        sub.refresh_from_db()
        self.assertFalse(sub.cancel_at_period_end)

    def test_checkout_plan_already_subscribed(self):
        Subscription.objects.create(user=self.user, plan=self.plan, status=Subscription.ACTIVE)
        resp = self.client.post(reverse('billing:checkout_plan', args=[self.plan.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('billing:manage_subscription'))


@override_settings(BILLING_FREE_SIGNUP_CREDITS=0)
class GatewayTests(TestCase):
    def test_cancel_subscription_stub(self):
        from billing import gateway
        res = gateway.cancel_subscription('manual_sub_123', cancel_at_period_end=True)
        self.assertTrue(res.get('cancelled'))

    def test_live_gateway_errors_raise_instead_of_faking_an_order(self):
        from billing import gateway
        with mock.patch('billing.gateway._client') as mock_client:
            mock_client.return_value.order.create.side_effect = Exception("API connection timeout")
            with self.assertRaises(gateway.GatewayError):
                gateway.create_order(100, 'INR', 'order_fail')

    def test_manual_mode_returns_marked_stub(self):
        from billing import gateway
        self.assertTrue(gateway.create_order(100, 'INR', 'r')['stub'])
        a = gateway.create_subscription('', 'monthly')['id']
        b = gateway.create_subscription('', 'monthly')['id']
        self.assertTrue(a.startswith('manual_'))
        self.assertNotEqual(a, b)

    @override_settings(RAZORPAY_KEY_SECRET='sek')
    def test_subscription_signature(self):
        from billing import gateway
        sig = hmac.new(b'sek', b'pay_1|sub_1', hashlib.sha256).hexdigest()
        self.assertTrue(gateway.verify_subscription_signature('sub_1', 'pay_1', sig))
        self.assertFalse(gateway.verify_subscription_signature('sub_2', 'pay_1', sig))

    @override_settings(DEBUG=False, RAZORPAY_KEY_ID='rzp_live_x', RAZORPAY_KEY_SECRET='sek',
                       RAZORPAY_WEBHOOK_SECRET='')
    def test_deploy_check_flags_missing_webhook_secret(self):
        from billing.checks import check_gateway_configuration
        ids = {i.id for i in check_gateway_configuration(None)}
        self.assertIn('billing.E003', ids)


@override_settings(BILLING_FREE_SIGNUP_CREDITS=0)
class ExpiryTaskTests(TestCase):
    def test_expires_lapsed_and_stale_but_not_paid_or_in_grace(self):
        u = _user()
        plan = Plan.objects.create(tier='Creator', price=499, currency='INR')
        now = timezone.now()
        lapsed = Subscription.objects.create(user=u, plan=plan, status=Subscription.ACTIVE,
                                             current_period_end=now - timezone.timedelta(days=5))
        in_grace = Subscription.objects.create(user=_user('g@x.com'), plan=plan,
                                               status=Subscription.ACTIVE,
                                               current_period_end=now - timezone.timedelta(days=1))
        stale = Subscription.objects.create(user=u, plan=plan, status=Subscription.PENDING)
        Subscription.objects.filter(pk=stale.pk).update(created_at=now - timezone.timedelta(days=2))
        pack = CreditPack.objects.create(name='P', credits=10, price=1, currency='INR')
        old_order = PackOrder.objects.create(user=u, pack=pack, credits=10, amount=1, currency='INR')
        PackOrder.objects.filter(pk=old_order.pk).update(created_at=now - timezone.timedelta(days=2))

        result = tasks.expire_lapsed_subscriptions(now)
        self.assertEqual(result, {'lapsed': 1, 'abandoned_subscriptions': 1, 'abandoned_orders': 1})
        lapsed.refresh_from_db()
        in_grace.refresh_from_db()
        self.assertEqual(lapsed.status, Subscription.EXPIRED)
        self.assertEqual(in_grace.status, Subscription.ACTIVE)
        self.assertEqual(gating.get_tier(u)[0], 'free')

        # An expired manual order can still be fulfilled once the money arrives.
        fulfillment.fulfill_pack_order(old_order, 'manual_utr1')
        self.assertEqual(wallet.balance(u), 10)


@override_settings(BILLING_FREE_SIGNUP_CREDITS=0)
class ManagementCommandTests(TestCase):
    def test_fulfill_pack_order_command(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        u = _user('manual@x.com')
        pack = CreditPack.objects.create(name='P', credits=100, price=10, currency='INR')
        order = PackOrder.objects.create(user=u, pack=pack, credits=100, amount=10, currency='INR')
        call_command('fulfill_pack_order', order.short_reference, 'UTR123')
        self.assertEqual(wallet.balance(u), 100)
        pay = Payment.objects.get(razorpay_payment_id='manual_UTR123')
        self.assertTrue(pay.is_manual)
        self.assertTrue(Receipt.objects.filter(payment=pay).exists())
        with self.assertRaises(CommandError):
            call_command('fulfill_pack_order', order.short_reference, 'UTR124')
        self.assertEqual(wallet.balance(u), 100)

    def test_activate_plan_command_activates_then_renews_from_period_end(self):
        from django.core.management import call_command
        u = _user('manual_plan@x.com')
        plan = Plan.objects.create(tier='Creator', price=499, currency='INR', credits_per_cycle=1000)
        call_command('activate_plan', u.email, plan.pk, 'UTR1')
        sub = Subscription.objects.get(user=u, status=Subscription.ACTIVE)
        first_end = sub.current_period_end
        call_command('activate_plan', u.email, plan.pk, 'UTR2')
        sub.refresh_from_db()
        self.assertEqual(sub.current_period_start, first_end)
        self.assertEqual(wallet.balance(u), 2000)
        call_command('activate_plan', u.email, plan.pk, 'UTR2')  # replay is a no-op
        self.assertEqual(wallet.balance(u), 2000)

    def test_grant_credits_insufficient_credits_handled(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        u = _user('cli_user@x.com')
        with self.assertRaises(CommandError):
            call_command('grant_credits', u.email, -100)


@override_settings(BILLING_FREE_SIGNUP_CREDITS=0)
class PlanSwitchTests(TestCase):
    def setUp(self):
        self.user = _user('switch@x.com')
        self.client.force_login(self.user)
        self.creator = Plan.objects.create(tier='Creator', price=499, currency='INR',
                                           credits_per_cycle=1000, razorpay_plan_id='plan_c')
        self.business = Plan.objects.create(tier='Business', price=1499, currency='INR',
                                            credits_per_cycle=5000, razorpay_plan_id='plan_b')
        self.sub = Subscription.objects.create(
            user=self.user, plan=self.creator, status=Subscription.ACTIVE,
            razorpay_subscription_id='sub_sw', gateway_plan_id='plan_c',
            current_period_end=timezone.now() + timezone.timedelta(days=10))

    def _charge(self, event_id, pay_id, plan_id):
        webhooks.process_event(event_id, 'subscription.charged', {
            'subscription': {'entity': {'id': 'sub_sw', 'plan_id': plan_id,
                                        'current_start': 1700000000, 'current_end': 1702592000}},
            'payment': {'entity': {'id': pay_id, 'amount': 149900, 'currency': 'INR'}}})

    def test_switch_is_scheduled_with_gateway_and_applied_at_renewal(self):
        with mock.patch('billing.gateway.schedule_plan_change') as edit:
            self.client.post(reverse('billing:change_plan', args=[self.business.pk]))
        edit.assert_called_once_with('sub_sw', 'plan_b')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.scheduled_plan, self.business)
        self.assertEqual(self.sub.plan, self.creator)  # unchanged until renewal
        self.assertEqual(gating.get_tier(self.user)[0], 'Creator')

        self._charge('evt_sw', 'pay_sw', 'plan_b')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.plan, self.business)
        self.assertIsNone(self.sub.scheduled_plan)
        self.assertEqual(self.sub.gateway_plan_id, 'plan_b')
        self.assertEqual(wallet.balance(self.user), 5000)
        self.assertEqual(Payment.objects.get(razorpay_payment_id='pay_sw').plan, self.business)

    def test_subscription_updated_applies_switch(self):
        fulfillment.schedule_plan_change(self.sub, self.business)
        webhooks.process_event('evt_up', 'subscription.updated', {
            'subscription': {'entity': {'id': 'sub_sw', 'plan_id': 'plan_b'}}})
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.plan, self.business)

    def test_renewal_on_old_gateway_plan_keeps_current_plan(self):
        fulfillment.schedule_plan_change(self.sub, self.business)
        self._charge('evt_old', 'pay_old', 'plan_c')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.plan, self.creator)
        self.assertEqual(self.sub.scheduled_plan, self.business)
        self.assertEqual(wallet.balance(self.user), 1000)

    def test_cancel_pending_switch(self):
        fulfillment.schedule_plan_change(self.sub, self.business)
        with mock.patch('billing.gateway.cancel_scheduled_plan_change') as undo:
            self.client.post(reverse('billing:cancel_plan_change'))
        undo.assert_called_once_with('sub_sw')
        self.sub.refresh_from_db()
        self.assertIsNone(self.sub.scheduled_plan)

    def test_switch_gateway_failure_changes_nothing(self):
        from billing import gateway
        with mock.patch('billing.gateway.schedule_plan_change',
                        side_effect=gateway.GatewayError('down')):
            self.client.post(reverse('billing:change_plan', args=[self.business.pk]))
        self.sub.refresh_from_db()
        self.assertIsNone(self.sub.scheduled_plan)

    def test_cancelling_clears_pending_switch(self):
        fulfillment.schedule_plan_change(self.sub, self.business)
        with mock.patch('billing.gateway.cancel_subscription'):
            self.client.post(reverse('billing:cancel_subscription'))
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.cancel_at_period_end)
        self.assertIsNone(self.sub.scheduled_plan)

    def test_switch_refused_when_cancelling_or_other_currency(self):
        usd = Plan.objects.create(tier='Global', price=9, currency='USD', razorpay_plan_id='plan_u')
        with mock.patch('billing.gateway.schedule_plan_change') as edit:
            self.client.post(reverse('billing:change_plan', args=[usd.pk]))
            Subscription.objects.filter(pk=self.sub.pk).update(cancel_at_period_end=True)
            self.client.post(reverse('billing:change_plan', args=[self.business.pk]))
        edit.assert_not_called()

    def test_manage_page_lists_switchable_plans(self):
        resp = self.client.get(reverse('billing:manage_subscription'))
        self.assertContains(resp, reverse('billing:change_plan', args=[self.business.pk]))

    def test_activate_plan_command_applies_manual_switch_at_renewal(self):
        from django.core.management import call_command
        Subscription.objects.filter(pk=self.sub.pk).update(razorpay_subscription_id='manual_sub_sw')
        self.sub.refresh_from_db()
        fulfillment.schedule_plan_change(self.sub, self.business)
        call_command('activate_plan', self.user.email, self.business.pk, 'UTR9')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.plan, self.business)
        self.assertEqual(wallet.balance(self.user), 5000)


@override_settings(BILLING_FREE_SIGNUP_CREDITS=0)
class PlanPriceSyncTests(TestCase):
    def _form(self, plan=None, **changes):
        from django.forms.models import model_to_dict
        from billing.admin import PlanAdminForm
        data = {'tier': 'Creator', 'billing_cycle': 'monthly', 'currency': 'INR', 'price': '499',
                'credits_per_cycle': 1000, 'razorpay_plan_id': '', 'max_smtp_configs': 1,
                'is_active': True, 'display_order': 0}
        if plan is not None:
            data.update({k: v for k, v in model_to_dict(plan).items() if k in data})
        data.update(changes)
        return PlanAdminForm(data=data, instance=plan)

    def test_live_save_creates_gateway_plan_and_reprice_creates_new_one(self):
        with mock.patch('billing.gateway.is_live', return_value=True), \
             mock.patch('billing.gateway.create_plan', side_effect=['plan_v1', 'plan_v2']) as create:
            form = self._form()
            self.assertTrue(form.is_valid(), form.errors)
            plan = form.save()
            self.assertEqual(plan.razorpay_plan_id, 'plan_v1')

            form = self._form(plan, display_order=5)  # non-pricing edit
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.save().razorpay_plan_id, 'plan_v1')

            form = self._form(plan, price='599')
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.save().razorpay_plan_id, 'plan_v2')
        self.assertEqual(create.call_count, 2)

    def test_gateway_failure_blocks_save(self):
        from billing import gateway
        with mock.patch('billing.gateway.is_live', return_value=True), \
             mock.patch('billing.gateway.create_plan', side_effect=gateway.GatewayError('down')):
            self.assertFalse(self._form().is_valid())

    def test_manual_mode_saves_without_gateway(self):
        with mock.patch('billing.gateway.create_plan') as create:
            self.assertTrue(self._form().is_valid())
        create.assert_not_called()

    def test_sync_command_links_unlinked_plans(self):
        from django.core.management import call_command
        plan = Plan.objects.create(tier='Creator', price=499, currency='INR')
        with mock.patch('billing.gateway.is_live', return_value=True), \
             mock.patch('billing.gateway.create_plan', return_value='plan_new'):
            call_command('sync_razorpay_plans')
        plan.refresh_from_db()
        self.assertEqual(plan.razorpay_plan_id, 'plan_new')
