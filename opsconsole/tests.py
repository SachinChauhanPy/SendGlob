from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from opsconsole import insights
from opsconsole.access import is_operator
from opsconsole.models import AdminAuditLog, AlertEvent, MetricSnapshot

User = get_user_model()

OPS_URL_NAMES = ['dashboard', 'health', 'activity', 'capacity', 'audit']

_PROBE_PATCHES = {
    'opsconsole.metrics.worker_status': {'ok': False, 'workers': [], 'count': 0},
    'opsconsole.metrics.broker_status': {'ok': False, 'detail': 'test'},
    'opsconsole.metrics.instance_identity': {'instance_type': 't3.small', 'region': None, 'source': 'settings'},
}


def _patch_probes():
    return [mock.patch(t, return_value=v) for t, v in _PROBE_PATCHES.items()]


class AccessControlTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(email='staff@x.com', password='pw', is_staff=True)
        self.normal = User.objects.create_user(email='user@x.com', password='pw')
        for p in _patch_probes():
            p.start()
            self.addCleanup(p.stop)

    def test_anonymous_blocked(self):
        for name in OPS_URL_NAMES:
            self.assertEqual(self.client.get(reverse(f'opsconsole:{name}')).status_code, 404, name)

    def test_non_staff_blocked_with_404(self):
        self.client.force_login(self.normal)
        for name in OPS_URL_NAMES:
            self.assertEqual(self.client.get(reverse(f'opsconsole:{name}')).status_code, 404, name)

    def test_staff_allowed(self):
        self.client.force_login(self.staff)
        for name in OPS_URL_NAMES:
            self.assertEqual(self.client.get(reverse(f'opsconsole:{name}')).status_code, 200, name)


class OperatorGateTests(TestCase):
    def test_operator_requires_superuser_or_group(self):
        plain_staff = User.objects.create_user(email='s@x.com', password='pw', is_staff=True)
        superuser = User.objects.create_superuser(email='su@x.com', password='pw')
        grouped = User.objects.create_user(email='g@x.com', password='pw', is_staff=True)
        grouped.groups.add(Group.objects.create(name='ops_operators'))
        self.assertFalse(is_operator(plain_staff))
        self.assertTrue(is_operator(superuser))
        self.assertTrue(is_operator(grouped))


class AuditLogTests(TestCase):
    def test_record_writes_row_with_target(self):
        actor = User.objects.create_user(email='a@x.com', password='pw', is_staff=True)
        target = User.objects.create_user(email='t@x.com', password='pw')
        row = AdminAuditLog.record(actor=actor, action='grant_credits', target=target,
                                   reason='goodwill', detail={'amount': 10}, ip_address='10.0.0.1')
        self.assertEqual(AdminAuditLog.objects.count(), 1)
        self.assertEqual(row.target_type, 'customuser')
        self.assertEqual(row.target_id, str(target.pk))
        self.assertEqual(row.detail['amount'], 10)


class ThresholdEvaluationTests(TestCase):
    def test_high_metric_breaches_above(self):
        breaches = insights.evaluate_thresholds({'host_ram_percent': 95.0})
        self.assertEqual(len(breaches), 1)
        self.assertEqual(breaches[0]['level'], 'critical')

    def test_lower_is_worse_metric_breaches_below(self):
        # CPU credit balance: low is bad.
        breaches = insights.evaluate_thresholds({'cpu_credit_balance': 5.0})
        self.assertEqual(breaches[0]['metric'], 'cpu_credit_balance')
        self.assertEqual(breaches[0]['level'], 'critical')

    def test_healthy_metric_no_breach(self):
        self.assertEqual(insights.evaluate_thresholds({'host_ram_percent': 40.0}), [])

    def test_none_values_ignored(self):
        self.assertEqual(insights.evaluate_thresholds({'host_ram_percent': None}), [])


class ProjectionTests(TestCase):
    def _seed(self, metric, points):
        # points: list of (days_ago, value). captured_at is auto_now_add, so
        # backdate via update after creation.
        now = timezone.now()
        for days_ago, value in points:
            row = MetricSnapshot.objects.create(metric=metric, value=value)
            MetricSnapshot.objects.filter(pk=row.pk).update(
                captured_at=now - timedelta(days=days_ago))

    def test_rising_metric_yields_positive_lead_time(self):
        self._seed('disk_percent', [(6, 50), (4, 60), (2, 70), (0, 80)])
        days = insights.project_metric('disk_percent', 90.0)
        self.assertIsNotNone(days)
        self.assertGreater(days, 0)

    def test_flat_metric_returns_none(self):
        self._seed('disk_percent', [(6, 50), (4, 50), (2, 50), (0, 50)])
        self.assertIsNone(insights.project_metric('disk_percent', 90.0))

    def test_insufficient_history_returns_none(self):
        self._seed('disk_percent', [(1, 50)])
        self.assertIsNone(insights.project_metric('disk_percent', 90.0))


class SizingRecommendationTests(TestCase):
    def test_ram_bound_recommends_memory_optimized(self):
        recs = insights.sizing_recommendations(
            {'host_ram_percent': 95.0, 'host_cpu_percent': 20.0, 'host_swap_percent': 0.0},
            {'instance_type': 't3.small', 'region': None})
        ram = next(r for r in recs if r['resource'] == 'RAM')
        self.assertEqual(ram['lever'], 'instance')
        self.assertTrue(ram['target'].startswith('r5'))

    def test_cpu_bound_non_burstable_recommends_compute(self):
        recs = insights.sizing_recommendations(
            {'host_cpu_percent': 95.0, 'host_ram_percent': 30.0},
            {'instance_type': 'm5.large', 'region': None})
        cpu = next(r for r in recs if r['resource'] == 'CPU')
        self.assertTrue(cpu['target'].startswith('c5'))

    def test_disk_recommends_ebs_growth_in_gb(self):
        recs = insights.sizing_recommendations(
            {'disk_percent': 88.0, 'disk_total_gb': 30.0},
            {'instance_type': 't3.small', 'region': None})
        disk = next(r for r in recs if r['resource'] == 'Disk')
        self.assertEqual(disk['lever'], 'ebs')
        self.assertIn('GB', disk['target'])

    def test_healthy_system_no_recommendations(self):
        recs = insights.sizing_recommendations(
            {'host_cpu_percent': 10.0, 'host_ram_percent': 20.0, 'disk_percent': 30.0},
            {'instance_type': 't3.small', 'region': None})
        self.assertEqual(recs, [])


class CollectorTests(TestCase):
    @mock.patch('opsconsole.metrics.cpu_credit_balance', return_value=None)
    @mock.patch('opsconsole.metrics.queue_depth', return_value=None)
    def test_collect_metrics_writes_snapshots(self, _qd, _cc):
        from opsconsole.tasks import collect_metrics
        result = collect_metrics()
        self.assertGreater(result['snapshots'], 0)
        self.assertGreater(MetricSnapshot.objects.count(), 0)

    @mock.patch('opsconsole.tasks._email_operator')
    def test_alert_dedup_no_duplicate_while_active(self, _email):
        from opsconsole.tasks import _raise_alert_if_new
        breach = {'metric': 'host_ram_percent', 'level': 'critical', 'value': 95.0,
                  'threshold': 90.0, 'message': 'ram critical'}
        self.assertTrue(_raise_alert_if_new(AlertEvent, breach))
        self.assertFalse(_raise_alert_if_new(AlertEvent, breach))
        self.assertEqual(AlertEvent.objects.filter(metric='host_ram_percent').count(), 1)

    @mock.patch('opsconsole.tasks._email_operator')
    def test_alert_reraises_after_acknowledge(self, _email):
        from opsconsole.tasks import _raise_alert_if_new
        breach = {'metric': 'disk_percent', 'level': 'warn', 'value': 85.0,
                  'threshold': 80.0, 'message': 'disk warn'}
        self.assertTrue(_raise_alert_if_new(AlertEvent, breach))
        AlertEvent.objects.filter(metric='disk_percent').update(acknowledged_at=timezone.now())
        self.assertTrue(_raise_alert_if_new(AlertEvent, breach))
        self.assertEqual(AlertEvent.objects.filter(metric='disk_percent').count(), 2)


class AcknowledgeAlertTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_superuser(email='op@x.com', password='pw')
        self.plain_staff = User.objects.create_user(email='ps@x.com', password='pw', is_staff=True)
        self.alert = AlertEvent.objects.create(
            metric='host_cpu_percent', level='critical', message='cpu', value=95.0, threshold=92.0)

    def test_non_operator_cannot_acknowledge(self):
        self.client.force_login(self.plain_staff)
        resp = self.client.post(reverse('opsconsole:acknowledge_alert', args=[self.alert.pk]))
        self.assertEqual(resp.status_code, 404)
        self.alert.refresh_from_db()
        self.assertIsNone(self.alert.acknowledged_at)

    def test_operator_acknowledges_and_audits(self):
        self.client.force_login(self.operator)
        resp = self.client.post(reverse('opsconsole:acknowledge_alert', args=[self.alert.pk]),
                                {'reason': 'investigated'})
        self.assertEqual(resp.status_code, 302)
        self.alert.refresh_from_db()
        self.assertIsNotNone(self.alert.acknowledged_at)
        self.assertEqual(self.alert.acknowledged_by, self.operator)
        self.assertEqual(AdminAuditLog.objects.filter(action='acknowledge_alert').count(), 1)


# --- Phase C: controls, credits, middleware, impersonation -------------------
class _Mixin:
    def _user(self, email, **kw):
        return User.objects.create_user(email=email, password='pw', **kw)

    def _operator(self, email='op@x.com'):
        return User.objects.create_superuser(email=email, password='pw')

    def tearDown(self):
        from opsconsole import flags
        flags.invalidate_all()
        super().tearDown()


from django.test import override_settings


@override_settings(BILLING_FREE_SIGNUP_CREDITS=0)
class GrantCreditsTests(_Mixin, TestCase):
    def test_grant_creates_single_ledger_row_and_audit(self):
        from billing.models import CreditTransaction
        from opsconsole import controls
        op = self._operator()
        u = self._user('c@x.com')
        ok, _msg = controls.grant_credits(actor=op, user=u, amount=50, reason='promo')
        self.assertTrue(ok)
        u.wallet.refresh_from_db()
        self.assertEqual(u.wallet.balance, 50)
        self.assertEqual(CreditTransaction.objects.filter(user=u).count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(action='grant_credits').count(), 1)

    def test_negative_adjust_cannot_overdraw(self):
        from opsconsole import controls
        op = self._operator()
        u = self._user('c2@x.com')
        ok, _ = controls.grant_credits(actor=op, user=u, amount=-5, reason='x')
        self.assertFalse(ok)
        u.wallet.refresh_from_db()
        self.assertEqual(u.wallet.balance, 0)

    def test_wallet_created_on_signup(self):
        u = self._user('w@x.com')
        self.assertTrue(hasattr(u, 'wallet'))


class UserControlTests(_Mixin, TestCase):
    def test_suspend_and_reactivate(self):
        from opsconsole import controls
        op = self._operator()
        u = self._user('s@x.com')
        ok, _ = controls.set_user_active(actor=op, user=u, active=False, reason='abuse')
        u.refresh_from_db()
        self.assertTrue(ok)
        self.assertFalse(u.is_active)
        self.assertEqual(AdminAuditLog.objects.filter(action='suspend_user').count(), 1)
        controls.set_user_active(actor=op, user=u, active=True, reason='resolved')
        u.refresh_from_db()
        self.assertTrue(u.is_active)

    def test_cannot_suspend_self(self):
        from opsconsole import controls
        op = self._operator()
        ok, _ = controls.set_user_active(actor=op, user=op, active=False, reason='x')
        self.assertFalse(ok)


class KillSwitchTests(_Mixin, TestCase):
    def test_send_loop_stops_when_paused(self):
        from opsconsole import controls
        op = self._operator()
        controls.set_flag(actor=op, key='global_send_paused', enabled=True, reason='incident')
        with mock.patch('emails.tasks.send_campaign_emails') as send_batch:
            from emails.tasks import send_entire_campaign
            send_entire_campaign(123)
        send_batch.assert_not_called()

    def test_send_loop_runs_when_not_paused(self):
        with mock.patch('emails.tasks.send_campaign_emails', return_value=False) as send_batch, \
             mock.patch('emails.tasks.EmailCampaign') as Camp:
            Camp.objects.get.return_value = mock.Mock(status='queued')
            from emails.tasks import send_entire_campaign
            send_entire_campaign(1)
        send_batch.assert_called_once()


class MaintenanceMiddlewareTests(_Mixin, TestCase):
    def setUp(self):
        from django.test import RequestFactory
        self.rf = RequestFactory()
        from opsconsole import controls
        op = self._operator()
        controls.set_flag(actor=op, key='maintenance_mode', enabled=True, reason='deploy')

    def _mw(self):
        from opsconsole.middleware import MaintenanceModeMiddleware
        return MaintenanceModeMiddleware(lambda r: __import__('django.http', fromlist=['HttpResponse']).HttpResponse('ok'))

    def test_non_staff_write_blocked(self):
        req = self.rf.post('/dashboard/something/')
        req.user = self._user('n@x.com')
        resp = self._mw()(req)
        self.assertEqual(resp.status_code, 503)

    def test_staff_write_allowed(self):
        req = self.rf.post('/dashboard/something/')
        req.user = self._user('st@x.com', is_staff=True)
        resp = self._mw()(req)
        self.assertEqual(resp.status_code, 200)

    def test_read_allowed(self):
        req = self.rf.get('/dashboard/')
        req.user = self._user('r@x.com')
        self.assertEqual(self._mw()(req).status_code, 200)

    def test_exempt_auth_path(self):
        req = self.rf.post('/accounts/logout/')
        req.user = self._user('a@x.com')
        self.assertEqual(self._mw()(req).status_code, 200)


class ImpersonationTests(_Mixin, TestCase):
    def test_start_and_stop_audited(self):
        op = self._operator()
        target = self._user('imp@x.com')
        self.client.force_login(op)
        resp = self.client.post(reverse('opsconsole:impersonate_start', args=[target.pk]),
                                {'reason': 'support'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(AdminAuditLog.objects.filter(action='impersonate_start').count(), 1)
        # While impersonating, request.user is the target on the next request.
        resp2 = self.client.post(reverse('opsconsole:impersonate_stop'))
        self.assertEqual(resp2.status_code, 302)
        self.assertEqual(AdminAuditLog.objects.filter(action='impersonate_stop').count(), 1)

    def test_cannot_impersonate_staff(self):
        op = self._operator()
        other_staff = self._user('os@x.com', is_staff=True)
        self.client.force_login(op)
        self.client.post(reverse('opsconsole:impersonate_start', args=[other_staff.pk]),
                         {'reason': 'x'})
        self.assertEqual(AdminAuditLog.objects.filter(action='impersonate_start').count(), 0)

    def test_write_blocked_while_impersonating(self):
        from django.test import RequestFactory
        from opsconsole.middleware import ImpersonationMiddleware
        target = self._user('t2@x.com')
        op = self._operator()
        rf = RequestFactory()
        req = rf.post('/dashboard/x/')
        req.user = op
        req.session = {ImpersonationMiddleware.SESSION_KEY: {
            'target_id': target.pk, 'operator_id': op.pk,
            'expires': timezone.now().timestamp() + 600}}
        mw = ImpersonationMiddleware(lambda r: __import__('django.http', fromlist=['HttpResponse']).HttpResponse('ok'))
        resp = mw(req)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(req.user, target)


class CampaignControlTests(_Mixin, TestCase):
    def _campaign(self, status='sending'):
        from accounts.models import SMTPConfig
        from emails.models import EmailCampaign
        u = self._user(f'cc{status}@x.com')
        smtp = SMTPConfig.objects.create(user=u, smtp_username='s@x.com', encrypted_password='x')
        return EmailCampaign.objects.create(user=u, name='C', smtp_config=smtp, status=status)

    def test_cancel_valid(self):
        from opsconsole import controls
        op = self._operator()
        c = self._campaign(status='sending')
        ok, _ = controls.cancel_campaign(actor=op, campaign=c, reason='stop')
        c.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(c.status, 'cancelled')

    def test_cancel_invalid_state(self):
        from opsconsole import controls
        op = self._operator()
        c = self._campaign(status='draft')
        ok, _ = controls.cancel_campaign(actor=op, campaign=c, reason='stop')
        self.assertFalse(ok)

    def test_requeue_resets_recipients(self):
        from opsconsole import controls
        from emails.models import Recipient
        op = self._operator()
        c = self._campaign(status='failed')
        Recipient.objects.create(campaign=c, email='a@x.com', name='A', status='failed')
        with mock.patch('emails.tasks.send_campaign_emails_task') as task:
            task.delay = mock.Mock()
            ok, _ = controls.requeue_campaign(actor=op, campaign=c, reason='retry')
        c.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(c.status, 'queued')
        self.assertEqual(Recipient.objects.get(campaign=c).status, 'pending')
