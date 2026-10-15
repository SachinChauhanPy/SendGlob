"""Regression tests for bugs resolved from docs/bugs.md (B-001, B-002, B-003, B-008, B-010, B-022)."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model

from accounts.models import SMTPConfig
from emails.models import EmailCampaign, EmailTemplate, Recipient
from emails import tasks

User = get_user_model()


class BugfixTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="bugfix@example.com", email="bugfix@example.com", password="pw12345678"
        )
        self.smtp = SMTPConfig.objects.create(
            user=self.user, name="SMTP", smtp_host="smtp.example.com",
            smtp_port=587, smtp_username="bugfix@example.com", is_active=True,
        )
        self.smtp.set_app_password("app-pass")
        self.smtp.save()
        self.template = EmailTemplate.objects.create(
            user=self.user, name="T", subject="Hi {{ name }}", body_html="<p>Hi {{ name }}</p>"
        )
        self.campaign = EmailCampaign.objects.create(
            user=self.user, name="C", template=self.template, smtp_config=self.smtp, status="draft"
        )


class TrackingPixelTests(BugfixTestBase):
    def test_pixel_uses_reverse_not_hardcoded_path(self):
        """B-001: pixel URL must come from reverse(), matching the live /dashboard/ mount."""
        r = Recipient.objects.create(campaign=self.campaign, email="a@x.com", name="A", status="pending")
        expected_path = reverse('emails:email_open_track', kwargs={'tracking_uuid': r.tracking_uuid})
        pixel = tasks.build_tracking_pixel("https://app.example.com", r.tracking_uuid)
        self.assertIn(f'src="https://app.example.com{expected_path}"', pixel)
        # The pre-fix hardcoded literal /track/<uuid> (without the /dashboard prefix) must not appear.
        self.assertNotIn('src="https://app.example.com/track/', pixel)


class FinalizeAndStaleTests(BugfixTestBase):
    def test_finalize_does_not_complete_while_sending(self):
        """B-003: a recipient stuck in 'sending' must keep the campaign out of 'completed'."""
        self.campaign.status = 'sending'
        self.campaign.save()
        Recipient.objects.create(campaign=self.campaign, email="s@x.com", name="S", status="sending")
        more = tasks._finalize_if_complete(self.campaign)
        self.campaign.refresh_from_db()
        self.assertNotEqual(self.campaign.status, 'completed')
        self.assertFalse(more)  # nothing claimable right now (only 'sending')

    def test_finalize_completes_when_all_terminal(self):
        self.campaign.status = 'sending'
        self.campaign.save()
        Recipient.objects.create(campaign=self.campaign, email="d@x.com", name="D", status="sent")
        more = tasks._finalize_if_complete(self.campaign)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'completed')
        self.assertFalse(more)

    def test_reset_stale_sending_recovers_old_only(self):
        """B-003: stale 'sending' rows are recovered; recently-claimed ones are left alone."""
        stale = Recipient.objects.create(campaign=self.campaign, email="old@x.com", name="O", status="sending")
        fresh = Recipient.objects.create(campaign=self.campaign, email="new@x.com", name="N", status="sending")
        old_time = timezone.now() - tasks.STALE_SENDING_THRESHOLD - timedelta(minutes=1)
        # Bypass auto_now to backdate the stale row.
        Recipient.objects.filter(pk=stale.pk).update(updated_at=old_time)
        recovered = tasks.reset_stale_sending(self.campaign)
        self.assertEqual(recovered, 1)
        stale.refresh_from_db(); fresh.refresh_from_db()
        self.assertEqual(stale.status, 'retrying')
        self.assertEqual(fresh.status, 'sending')


class ClaimBatchTests(BugfixTestBase):
    def test_claim_orders_by_id_and_flips_to_sending(self):
        """B-002/B-008: batch is claimed in id order and flipped to 'sending' with fresh updated_at."""
        ids = [
            Recipient.objects.create(campaign=self.campaign, email=f"{i}@x.com", name=str(i), status="pending").id
            for i in range(5)
        ]
        claimed = tasks._claim_recipient_batch(self.campaign, batch_size=3)
        self.assertEqual([r.id for r in claimed], sorted(ids)[:3])
        for r in claimed:
            r.refresh_from_db()
            self.assertEqual(r.status, 'sending')


class CampaignQueueMessageTests(BugfixTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        Recipient.objects.create(campaign=self.campaign, email="r@x.com", name="R", status="pending")

    def test_already_sending_gives_feedback(self):
        """B-010: queueing an already-active campaign returns an informational message."""
        self.campaign.status = 'sending'
        self.campaign.save()
        resp = self.client.post(reverse('emails:campaign_queue', kwargs={'pk': self.campaign.pk}), follow=True)
        msgs = [m.message for m in resp.context['messages']]
        self.assertTrue(any("already being sent" in m for m in msgs))


class CampaignSmtpDropdownTests(BugfixTestBase):
    def setUp(self):
        super().setUp()
        # Create an inactive SMTP configuration for the same user
        self.smtp_inactive = SMTPConfig.objects.create(
            user=self.user, name="SMTP Inactive", smtp_host="smtp-inactive.example.com",
            smtp_port=587, smtp_username="inactive@example.com", is_active=False,
        )
        self.smtp_inactive.set_app_password("inactive-pass")
        self.smtp_inactive.save()

        # Create another user and their SMTP configuration to verify isolation
        self.other_user = User.objects.create_user(
            username="other@example.com", email="other@example.com", password="pw12345678"
        )
        self.other_smtp = SMTPConfig.objects.create(
            user=self.other_user, name="Other SMTP", smtp_host="smtp-other.example.com",
            smtp_port=587, smtp_username="other@example.com", is_active=True,
        )
        self.other_smtp.set_app_password("other-pass")
        self.other_smtp.save()

    def test_campaign_form_includes_all_user_smtp_configs(self):
        """B-022: EmailCampaignForm must include both active and inactive SMTP configurations belonging to the user, but not other users' configurations."""
        from emails.forms import EmailCampaignForm
        form = EmailCampaignForm(user=self.user)
        smtp_queryset = form.fields['smtp_config'].queryset

        self.assertIn(self.smtp, smtp_queryset)
        self.assertIn(self.smtp_inactive, smtp_queryset)
        self.assertNotIn(self.other_smtp, smtp_queryset)

        # Preselected default should be the active one
        self.assertEqual(form.fields['smtp_config'].initial, self.smtp)

    def test_campaign_edit_form_includes_all_user_smtp_configs(self):
        """B-022: EmailCampaignEditForm must include both active and inactive SMTP configurations belonging to the user."""
        from emails.forms import EmailCampaignEditForm
        form = EmailCampaignEditForm(instance=self.campaign, user=self.user)
        smtp_queryset = form.fields['smtp_config'].queryset

        self.assertIn(self.smtp, smtp_queryset)
        self.assertIn(self.smtp_inactive, smtp_queryset)
        self.assertNotIn(self.other_smtp, smtp_queryset)

    def test_document_campaign_form_includes_all_user_smtp_configs(self):
        """B-022: DocumentCampaignForm must include both active and inactive SMTP configurations belonging to the user."""
        from documents.forms import DocumentCampaignForm
        form = DocumentCampaignForm(user=self.user)
        smtp_queryset = form.fields['smtp_config'].queryset

        self.assertIn(self.smtp, smtp_queryset)
        self.assertIn(self.smtp_inactive, smtp_queryset)
        self.assertNotIn(self.other_smtp, smtp_queryset)

        # Preselected default should be the active one
        self.assertEqual(form.fields['smtp_config'].initial, self.smtp)

    def test_campaign_creation_prerequisite_check_with_inactive_only(self):
        """B-022: Users should be allowed to access campaign creation views even if they only have inactive SMTP configurations configured."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from documents.models import DocumentTemplate

        # Deactivate all configurations for testing
        self.smtp.is_active = False
        self.smtp.save()

        self.client.force_login(self.user)

        # Verify emails campaign creation view responds with 200 (does not redirect to smtp_create)
        resp = self.client.get(reverse('emails:campaign_create'))
        self.assertEqual(resp.status_code, 200)

        # Verify documents campaign creation view responds with 200 (with at least one template created)
        doc_file = SimpleUploadedFile("dummy_doc.png", b"file_content", content_type="image/png")
        DocumentTemplate.objects.create(
            user=self.user, name="Doc Template", template_file=doc_file
        )
        resp = self.client.get(reverse('documents:campaign_create'))
        self.assertEqual(resp.status_code, 200)
