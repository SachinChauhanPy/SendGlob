"""Unit tests for the pure schedule-computation service (emails/scheduling.py)."""
import os
import tempfile
from datetime import datetime, time, timedelta, timezone as dt_timezone

from django.test import TestCase

from emails.models import EmailCampaign
from emails.scheduling import compute_next_run, describe_schedule
from unittest.mock import patch
from django.test import override_settings
from django.utils import timezone
from django.core import mail
from django.contrib.auth import get_user_model
from accounts.models import SMTPConfig
from emails.models import Recipient
from emails.tasks import (
    arm_next_cycle, trigger_scheduled_campaigns, notify_upcoming_scheduled_campaigns,
)

UTC = dt_timezone.utc


def _c(**kwargs):
    """Build an unsaved campaign carrying only schedule attributes."""
    return EmailCampaign(**kwargs)


class ComputeNextRunTests(TestCase):
    def setUp(self):
        # A fixed Monday 10:00 UTC reference (2026-06-22 is a Monday).
        self.now = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
        self.assertEqual(self.now.weekday(), 0)  # guard: Monday

    def test_none_returns_none(self):
        self.assertIsNone(compute_next_run(_c(schedule_type='none'), now=self.now))

    def test_interval_first_run_exactly_n_days_out(self):
        c = _c(schedule_type='interval', schedule_time=time(9, 0), interval_days=3)
        nr = compute_next_run(c, base=self.now, now=self.now)
        self.assertEqual(nr.date(), (self.now + timedelta(days=3)).date())
        self.assertEqual((nr.hour, nr.minute), (9, 0))

    def test_interval_rolls_forward_when_base_in_past(self):
        c = _c(schedule_type='interval', schedule_time=time(9, 0), interval_days=7)
        base = self.now - timedelta(days=30)
        nr = compute_next_run(c, base=base, now=self.now)
        self.assertGreater(nr, self.now)
        self.assertEqual((nr - datetime.combine(base.date(), time(9, 0), tzinfo=UTC)).days % 7, 0)

    def test_interval_missing_days_returns_none(self):
        c = _c(schedule_type='interval', schedule_time=time(9, 0), interval_days=None)
        self.assertIsNone(compute_next_run(c, now=self.now))

    def test_monthly_future_day_this_month(self):
        c = _c(schedule_type='monthly', schedule_time=time(9, 0), day_of_month=25)
        nr = compute_next_run(c, now=self.now)
        self.assertEqual((nr.year, nr.month, nr.day), (2026, 6, 25))

    def test_monthly_past_day_rolls_to_next_month(self):
        c = _c(schedule_type='monthly', schedule_time=time(9, 0), day_of_month=10)
        nr = compute_next_run(c, now=self.now)
        self.assertEqual((nr.year, nr.month, nr.day), (2026, 7, 10))

    def test_monthly_clamps_to_last_day_of_short_month(self):
        c = _c(schedule_type='monthly', schedule_time=time(9, 0), day_of_month=31)
        feb = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)  # 2026 not a leap year
        nr = compute_next_run(c, now=feb)
        self.assertEqual((nr.year, nr.month, nr.day), (2026, 2, 28))

    def test_weekly_same_day_but_time_passed_goes_next_week(self):
        # Monday 09:00 with now Monday 10:00 -> next Monday.
        c = _c(schedule_type='weekly', schedule_time=time(9, 0), day_of_week=0)
        nr = compute_next_run(c, now=self.now)
        self.assertEqual(nr.weekday(), 0)
        self.assertEqual(nr.date(), (self.now + timedelta(days=7)).date())

    def test_weekly_same_day_time_future_is_today(self):
        c = _c(schedule_type='weekly', schedule_time=time(18, 0), day_of_week=0)
        nr = compute_next_run(c, now=self.now)
        self.assertEqual(nr.date(), self.now.date())
        self.assertEqual((nr.hour, nr.minute), (18, 0))

    def test_weekly_future_weekday(self):
        c = _c(schedule_type='weekly', schedule_time=time(9, 0), day_of_week=3)  # Thursday
        nr = compute_next_run(c, now=self.now)
        self.assertEqual(nr.weekday(), 3)
        self.assertGreater(nr, self.now)


class TimezoneSchedulingTests(TestCase):
    """B-038: schedule times are interpreted in the campaign owner's timezone."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email='tz@t.com', password='pw12345678', timezone='Asia/Kolkata'
        )
        self.smtp = SMTPConfig.objects.create(
            user=self.user, name='c', smtp_username='tz@t.com', is_active=True
        )

    def test_interval_time_interpreted_in_user_timezone(self):
        # 09:00 in Asia/Kolkata (UTC+5:30, no DST) is 03:30 UTC.
        now = datetime(2026, 6, 22, 0, 0, tzinfo=UTC)
        c = EmailCampaign(
            user=self.user, smtp_config=self.smtp, schedule_type='interval',
            schedule_time=time(9, 0), interval_days=1,
        )
        nr = compute_next_run(c, base=now, now=now).astimezone(UTC)
        self.assertEqual((nr.hour, nr.minute), (3, 30))

    def test_describe_uses_user_timezone_label(self):
        c = EmailCampaign(
            user=self.user, smtp_config=self.smtp, schedule_type='weekly',
            schedule_time=time(9, 0), day_of_week=0, is_recurring=True,
        )
        self.assertIn('Asia/Kolkata', describe_schedule(c))

    def test_invalid_timezone_falls_back_to_utc(self):
        self.user.timezone = 'Not/AZone'
        c = EmailCampaign(
            user=self.user, smtp_config=self.smtp, schedule_type='interval',
            schedule_time=time(9, 0), interval_days=1,
        )
        now = datetime(2026, 6, 22, 0, 0, tzinfo=UTC)
        nr = compute_next_run(c, base=now, now=now).astimezone(UTC)
        self.assertEqual((nr.hour, nr.minute), (9, 0))


class DescribeScheduleTests(TestCase):
    def test_interval_once(self):
        c = _c(schedule_type='interval', schedule_time=time(9, 0), interval_days=1, is_recurring=False)
        self.assertEqual(describe_schedule(c), "Every 1 day at 09:00 UTC (once)")

    def test_interval_recurring_plural(self):
        c = _c(schedule_type='interval', schedule_time=time(9, 0), interval_days=3, is_recurring=True)
        self.assertEqual(describe_schedule(c), "Every 3 days at 09:00 UTC")

    def test_weekly_label(self):
        c = _c(schedule_type='weekly', schedule_time=time(9, 30), day_of_week=2, is_recurring=True)
        self.assertEqual(describe_schedule(c), "Weekly on Wednesday at 09:30 UTC")

    def test_monthly_label(self):
        c = _c(schedule_type='monthly', schedule_time=time(8, 0), day_of_month=15, is_recurring=True)
        self.assertEqual(describe_schedule(c), "Monthly on day 15 at 08:00 UTC")

    def test_none_is_empty(self):
        self.assertEqual(describe_schedule(_c(schedule_type='none')), "")


class RecurrenceLifecycleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email='r@t.com', password='pw12345678')
        self.smtp = SMTPConfig.objects.create(
            user=self.user, name='c', smtp_username='r@t.com', is_active=True
        )
        self.smtp.set_app_password('pw')
        self.smtp.save()

    def _campaign(self, **kw):
        defaults = dict(
            user=self.user, name='C', smtp_config=self.smtp, status='completed',
            schedule_type='weekly', schedule_time=time(9, 0), day_of_week=0,
            is_recurring=True, completed_at=timezone.now(),
        )
        defaults.update(kw)
        return EmailCampaign.objects.create(**defaults)

    def test_arm_next_cycle_carries_recipients(self):
        c = self._campaign()
        Recipient.objects.create(campaign=c, email='a@t.com', name='A', status='sent')
        Recipient.objects.create(campaign=c, email='b@t.com', name='B', status='sent')
        child = arm_next_cycle(c)
        self.assertIsNotNone(child)
        self.assertEqual(child.status, 'scheduled')
        self.assertTrue(child.is_recurring)
        self.assertIsNotNone(child.next_run_at)
        self.assertEqual(child.recipients.count(), 2)
        self.assertTrue(all(r.status == 'pending' for r in child.recipients.all()))
        self.assertEqual(child.parent_campaign, c)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_arm_next_cycle_copies_attachments_independently(self):
        from django.core.files.base import ContentFile
        from emails.models import CampaignAttachment
        c = self._campaign()
        Recipient.objects.create(campaign=c, email='a@t.com', name='A', status='sent')
        att = CampaignAttachment(campaign=c, filename='report.pdf')
        att.file.save('report.pdf', ContentFile(b'PDF-BYTES'), save=True)

        child = arm_next_cycle(c)
        self.assertEqual(child.attachments.count(), 1)
        child_att = child.attachments.first()
        # The copy owns a distinct file path, so deleting one leaves the other.
        self.assertNotEqual(child_att.file.name, att.file.name)
        self.assertTrue(os.path.exists(child_att.file.path))
        self.assertEqual(child_att.file.read(), b'PDF-BYTES')

        source_path = att.file.path
        child_path = child_att.file.path  # cleanup clears file.name, so capture now
        with self.captureOnCommitCallbacks(execute=True):
            child_att.delete()
        self.assertFalse(os.path.exists(child_path))
        self.assertTrue(os.path.exists(source_path))  # source untouched

    def test_arm_next_cycle_non_recurring_returns_none(self):
        c = self._campaign(is_recurring=False)
        self.assertIsNone(arm_next_cycle(c))

    def test_arm_next_cycle_idempotent(self):
        c = self._campaign()
        Recipient.objects.create(campaign=c, email='a@t.com', name='A', status='sent')
        self.assertIsNotNone(arm_next_cycle(c))
        self.assertIsNone(arm_next_cycle(c))
        self.assertEqual(c.recurrence_children.count(), 1)

    @patch('emails.tasks.send_campaign_emails_task.delay')
    def test_trigger_promotes_due_scheduled(self, mock_delay):
        c = self._campaign(status='scheduled', next_run_at=timezone.now() - timedelta(minutes=1))
        Recipient.objects.create(campaign=c, email='a@t.com', name='A', status='pending')
        promoted = trigger_scheduled_campaigns()
        c.refresh_from_db()
        self.assertEqual(c.status, 'queued')
        self.assertGreaterEqual(promoted, 1)
        mock_delay.assert_called_once_with(c.id)

    @patch('emails.tasks.send_campaign_emails_task.delay')
    def test_trigger_demotes_zero_recipient_scheduled_to_draft(self, mock_delay):
        """B-051 defense in depth: a due scheduled campaign with no recipients is
        demoted to draft and disarmed instead of being promoted to queued."""
        c = self._campaign(status='scheduled', next_run_at=timezone.now() - timedelta(minutes=1))
        promoted = trigger_scheduled_campaigns()
        c.refresh_from_db()
        self.assertEqual(c.status, 'draft')
        self.assertIsNone(c.next_run_at)
        self.assertEqual(promoted, 0)
        mock_delay.assert_not_called()

    @patch('emails.tasks.send_campaign_emails_task.delay')
    def test_trigger_ignores_future(self, mock_delay):
        c = self._campaign(status='scheduled', next_run_at=timezone.now() + timedelta(days=2))
        trigger_scheduled_campaigns()
        c.refresh_from_db()
        self.assertEqual(c.status, 'scheduled')
        mock_delay.assert_not_called()

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_reminder_only_within_lead_window(self):
        # Far out: editable, no reminder yet.
        far = self._campaign(status='scheduled', next_run_at=timezone.now() + timedelta(days=3))
        self.assertTrue(far.is_editable)
        self.assertEqual(notify_upcoming_scheduled_campaigns(), 0)
        far.refresh_from_db()
        self.assertIsNone(far.reminder_sent_at)
        # Within the lead/freeze window: locked, reminder sent exactly once.
        near = self._campaign(status='scheduled', next_run_at=timezone.now() + timedelta(hours=2))
        self.assertFalse(near.is_editable)
        self.assertTrue(near.is_locked)
        self.assertEqual(notify_upcoming_scheduled_campaigns(), 1)
        near.refresh_from_db()
        self.assertIsNotNone(near.reminder_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.user.email, mail.outbox[0].to)
        self.assertEqual(notify_upcoming_scheduled_campaigns(), 0)  # idempotent
