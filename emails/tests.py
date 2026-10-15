import tempfile

from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import patch
from emails.views import parse_csv_recipients
from emails.models import EmailCampaign, EmailTemplate, Recipient
from accounts.models import SMTPConfig

User = get_user_model()

class CSVProcessingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="testuser@example.com",
            password="testpassword123"
        )
        self.smtp_config = SMTPConfig.objects.create(
            user=self.user,
            name="Test SMTP",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="test@example.com",
            is_active=True
        )
        self.smtp_config.set_app_password("secret-app-password")
        self.smtp_config.save()
        
        self.template = EmailTemplate.objects.create(
            user=self.user,
            name="Test Template",
            subject="Hello {{ name }}",
            body_html="<p>Hello {{ name }}</p>"
        )

    def test_parse_csv_with_missing_values(self):
        """Test that CSV parsing handles rows with missing/None values and empty columns without raising AttributeError."""
        csv_content = (
            "name,email,company\n"
            "Alice,alice@example.com,Google\n"
            "Bob,bob@example.com,\n"  # Missing value in last column
            "Charlie,charlie@example.com\n"  # Fewer columns than header
            ",,\n"  # Completely empty line
        )
        csv_file = SimpleUploadedFile("recipients.csv", csv_content.encode('utf-8'))
        
        recipients = parse_csv_recipients(csv_file)
        
        # Should successfully parse Alice, Bob, and Charlie. The completely empty line should be skipped because of lack of email.
        self.assertEqual(len(recipients), 3)
        self.assertEqual(recipients[0]['name'], 'Alice')
        self.assertEqual(recipients[0]['email'], 'alice@example.com')
        self.assertEqual(recipients[0]['context']['company'], 'Google')
        
        self.assertEqual(recipients[1]['name'], 'Bob')
        self.assertEqual(recipients[1]['email'], 'bob@example.com')
        self.assertEqual(recipients[1]['context']['company'], '')
        
        self.assertEqual(recipients[2]['name'], 'Charlie')
        self.assertEqual(recipients[2]['email'], 'charlie@example.com')
        self.assertEqual(recipients[2]['context'].get('company', ''), '')

    def test_parse_csv_with_duplicate_emails(self):
        """Test that CSV parsing retains duplicate email addresses."""
        csv_content = (
            "name,email\n"
            "Alice,test@example.com\n"
            "Bob,test@example.com\n"
        )
        csv_file = SimpleUploadedFile("recipients.csv", csv_content.encode('utf-8'))
        
        recipients = parse_csv_recipients(csv_file)
        self.assertEqual(len(recipients), 2)
        self.assertEqual(recipients[0]['name'], 'Alice')
        self.assertEqual(recipients[1]['name'], 'Bob')

    def test_campaign_creation_saves_duplicate_emails(self):
        """Test that campaign creation saves all recipients from the CSV, including duplicate emails."""
        # Mock request flow by testing view logic directly or modeling the save logic
        # Let's verify that creating a campaign with multiple duplicate email entries works
        campaign = EmailCampaign.objects.create(
            user=self.user,
            name="Test Duplicate Campaign",
            template=self.template,
            smtp_config=self.smtp_config,
            status="draft"
        )
        
        csv_content = (
            "name,email\n"
            "Alice,test@example.com\n"
            "Bob,test@example.com\n"
        )
        csv_file = SimpleUploadedFile("recipients.csv", csv_content.encode('utf-8'))
        recipients_data = parse_csv_recipients(csv_file)
        
        recipients_to_create = []
        for r in recipients_data:
            recipients_to_create.append(
                Recipient(
                    campaign=campaign,
                    email=r['email'],
                    name=r['name'],
                    context_data=r['context'],
                    status='pending'
                )
            )
        
        Recipient.objects.bulk_create(recipients_to_create)
        campaign.total_emails = len(recipients_to_create)
        campaign.save()
        
        # Verify both recipients are saved
        self.assertEqual(campaign.recipients.count(), 2)
        emails = list(campaign.recipients.values_list('email', flat=True))
        self.assertEqual(emails, ['test@example.com', 'test@example.com'])
        names = list(campaign.recipients.values_list('name', flat=True))
        self.assertEqual(names, ['Alice', 'Bob'])


class EmailOpenTrackingTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="testuser2",
            email="testuser2@example.com",
            password="testpassword123"
        )
        self.smtp_config = SMTPConfig.objects.create(
            user=self.user,
            name="Test SMTP 2",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="test2@example.com",
            is_active=True
        )
        self.smtp_config.set_app_password("secret-app-password")
        self.smtp_config.save()
        self.campaign = EmailCampaign.objects.create(
            user=self.user,
            name="Test Campaign 2",
            smtp_config=self.smtp_config,
            status="draft"
        )
        self.recipient = Recipient.objects.create(
            campaign=self.campaign,
            email="recipient@example.com",
            name="Recipient Name",
            status="sent"
        )

    def test_email_open_track_success(self):
        """Test that requesting the tracking pixel updates the opened_at field."""
        from django.urls import reverse
        self.assertIsNone(self.recipient.opened_at)
        
        # Request the pixel using reverse URL lookup
        response = self.client.get(reverse('emails:email_open_track', kwargs={'tracking_uuid': self.recipient.tracking_uuid}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/gif')
        
        # Refresh from database and check opened_at
        self.recipient.refresh_from_db()
        self.assertIsNotNone(self.recipient.opened_at)

    @patch('emails.views.send_campaign_emails_task.delay')
    def test_campaign_restart_success(self, mock_delay):
        """Test that restarting a campaign resets its status, stats, and recipient logs."""
        from django.utils import timezone
        from django.urls import reverse
        # Setup campaign as completed
        self.campaign.status = 'completed'
        self.campaign.sent_emails = 1
        self.campaign.failed_emails = 0
        self.campaign.save()
        
        # Setup recipient as sent and opened
        self.recipient.status = 'sent'
        self.recipient.opened_at = timezone.now()
        self.recipient.save()
        
        # Authenticate test client
        self.client.force_login(self.user)
        
        # Post to restart URL using reverse URL lookup
        response = self.client.post(reverse('emails:campaign_restart', kwargs={'pk': self.campaign.id}))
        self.assertEqual(response.status_code, 302)  # Should redirect back to detail view
        
        # Verify Celery task was triggered
        mock_delay.assert_called_once_with(self.campaign.id)
        
        # Refresh from database and verify campaign resets
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'queued')
        self.assertEqual(self.campaign.sent_emails, 0)
        self.campaign.failed_emails = 0
        self.assertIsNone(self.campaign.started_at)
        self.assertIsNone(self.campaign.completed_at)
        
        # Refresh from database and verify recipient resets
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.status, 'pending')
        self.assertIsNone(self.recipient.opened_at)
        self.assertIsNone(self.recipient.sent_at)
        self.assertEqual(self.recipient.retry_count, 0)


class CampaignEditAndSoftDeleteTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser_edit",
            email="testuser_edit@example.com",
            password="testpassword123"
        )
        self.client.force_login(self.user)
        self.smtp_config = SMTPConfig.objects.create(
            user=self.user,
            name="Test SMTP",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="test@example.com",
            is_active=True
        )
        self.smtp_config.set_app_password("secret-app-password")
        self.smtp_config.save()
        
        self.template = EmailTemplate.objects.create(
            user=self.user,
            name="Test Template",
            subject="Hello {{ name }}",
            body_html="<p>Hello {{ name }}</p>"
        )

        self.campaign = EmailCampaign.objects.create(
            user=self.user,
            name="Draft Campaign",
            template=self.template,
            smtp_config=self.smtp_config,
            status="draft"
        )

    def test_soft_deletion(self):
        """Test soft delete mechanism for SMTPConfig, EmailTemplate and EmailCampaign."""
        # 1. Soft delete template
        self.template.delete()
        self.template.refresh_from_db()
        self.assertIsNotNone(self.template.deleted_at)
        
        # Verify it doesn't show in active but shows in objects
        self.assertFalse(EmailTemplate.active.filter(id=self.template.id).exists())
        self.assertTrue(EmailTemplate.objects.filter(id=self.template.id).exists())
        
        # Verify campaign relationship to template still resolves (doesn't throw or get set to None since objects is default manager)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.template, self.template)

    def test_active_locks(self):
        """Test that editing or deleting template/SMTP config is blocked when they are referenced by an active campaign."""
        from django.urls import reverse
        # Set campaign to active
        self.campaign.status = 'sending'
        self.campaign.save()

        # Try editing template (should redirect / block)
        response = self.client.post(reverse('emails:template_edit', kwargs={'pk': self.template.id}), {'name': 'New Name'})
        self.assertEqual(response.status_code, 302)
        
        # Try deleting template
        response = self.client.post(reverse('emails:template_delete', kwargs={'pk': self.template.id}))
        self.assertEqual(response.status_code, 302)
        # Template should still be active
        self.template.refresh_from_db()
        self.assertIsNone(self.template.deleted_at)

        # Try deleting campaign (blocked since it's active)
        response = self.client.post(reverse('emails:campaign_delete', kwargs={'pk': self.campaign.id}))
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertIsNone(self.campaign.deleted_at)

    def test_campaign_edit_draft_full(self):
        """Test editing a draft campaign with full edits and recipient replacing/appending."""
        from django.urls import reverse
        # 1. Add some initial recipients
        r1 = Recipient.objects.create(campaign=self.campaign, email="r1@example.com", name="R1")
        self.campaign.total_emails = 1
        self.campaign.save()

        # 2. Post append recipients
        response = self.client.post(reverse('emails:campaign_edit', kwargs={'pk': self.campaign.id}), {
            'name': 'Updated Draft Name',
            'smtp_config': self.smtp_config.id,
            'template': self.template.id,
            'quick_recipients': 'r2@example.com, r3@example.com',
            'recipient_handling': 'append',
        })
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.name, 'Updated Draft Name')
        self.assertEqual(self.campaign.recipients.count(), 3)

        # 3. Post replace recipients
        response = self.client.post(reverse('emails:campaign_edit', kwargs={'pk': self.campaign.id}), {
            'name': 'Replaced Draft Name',
            'smtp_config': self.smtp_config.id,
            'template': self.template.id,
            'quick_recipients': 'r4@example.com',
            'recipient_handling': 'replace',
        })
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.recipients.count(), 1)
        self.assertEqual(self.campaign.recipients.first().email, 'r4@example.com')

    def test_campaign_edit_non_draft_limited(self):
        """Test editing non-draft campaigns (completed, failed, cancelled) only allows modifying name and SMTP config."""
        from django.urls import reverse
        
        for status in ['completed', 'failed', 'cancelled']:
            campaign = EmailCampaign.objects.create(
                user=self.user,
                name=f"Campaign {status}",
                template=self.template,
                smtp_config=self.smtp_config,
                status=status
            )
            
            new_template = EmailTemplate.objects.create(user=self.user, name=f"New Template {status}", subject="Subj", body_html="Body")
            new_smtp = SMTPConfig.objects.create(user=self.user, name=f"New SMTP {status}", smtp_host="smtp.new.com", smtp_port=25, smtp_username="new@smtp.com", is_active=True)
            new_smtp.set_app_password("secret")
            new_smtp.save()

            response = self.client.post(reverse('emails:campaign_edit', kwargs={'pk': campaign.id}), {
                'name': f'New {status} Name',
                'smtp_config': new_smtp.id,
                'template': new_template.id,
                'quick_recipients': 'ignored@example.com',
                'recipient_handling': 'replace',
            })
            self.assertEqual(response.status_code, 302)
            campaign.refresh_from_db()
            
            # Name and SMTP config must be updated
            self.assertEqual(campaign.name, f'New {status} Name')
            self.assertEqual(campaign.smtp_config, new_smtp)
            # Template and recipients must NOT be updated
            self.assertEqual(campaign.template, self.template)
            self.assertEqual(campaign.recipients.filter(email='ignored@example.com').count(), 0)

    @patch('documents.tasks.generate_documents_task.delay')
    def test_campaign_restart_resets_files_and_stats(self, mock_task_delay):
        """Test that restarting a document campaign resets stats, resets recipients, clears old document files, and triggers regeneration."""
        from django.urls import reverse
        from documents.models import DocumentTemplate
        from django.core.files.uploadedfile import SimpleUploadedFile

        doc_file = SimpleUploadedFile("dummy.png", b"file_content", content_type="image/png")
        doc_template = DocumentTemplate.objects.create(
            user=self.user,
            name="Restart Doc Template",
            template_file=doc_file,
        )

        doc_campaign = EmailCampaign.objects.create(
            user=self.user,
            name="Restart Doc Campaign",
            template=self.template,
            smtp_config=self.smtp_config,
            status="completed",
            is_document_campaign=True,
            document_template=doc_template,
        )

        r = Recipient.objects.create(
            campaign=doc_campaign,
            email="restart@example.com",
            name="Restart Recipient",
            document_file="generated_documents/2026/06/02/restart.pdf",
            status="sent",
        )

        response = self.client.post(reverse('emails:campaign_restart', kwargs={'pk': doc_campaign.id}))
        self.assertEqual(response.status_code, 302)

        doc_campaign.refresh_from_db()
        self.assertEqual(doc_campaign.status, 'queued')
        self.assertEqual(doc_campaign.sent_emails, 0)
        self.assertEqual(doc_campaign.failed_emails, 0)
        self.assertEqual(doc_campaign.total_emails, 1)

        r.refresh_from_db()
        self.assertEqual(r.status, 'pending')
        self.assertFalse(r.document_file)

        # Regeneration task must be triggered with auto_send=True
        mock_task_delay.assert_called_once_with(doc_campaign.id, doc_template.id, True)

    def test_soft_deleted_campaigns_do_not_lock_resources(self):
        """Test that editing or deleting template/SMTP config is NOT blocked when referenced by a soft-deleted campaign (even if status is sending/queued)."""
        from django.urls import reverse
        
        # Set campaign to active but then soft delete it
        self.campaign.status = 'sending'
        self.campaign.save()
        self.campaign.delete()  # Soft delete
        self.campaign.refresh_from_db()
        self.assertIsNotNone(self.campaign.deleted_at)

        # Now, try editing template (should succeed/redirect but not block)
        response = self.client.post(reverse('emails:template_edit', kwargs={'pk': self.template.id}), {
            'name': 'Updated Template Name',
            'subject': 'Updated Subject',
            'layout': 'simple',
            'brand_color': '#0078D4',
        })
        self.assertEqual(response.status_code, 302)
        self.template.refresh_from_db()
        self.assertEqual(self.template.name, 'Updated Template Name')
        
        # Try deleting template - should succeed (soft delete)
        response = self.client.post(reverse('emails:template_delete', kwargs={'pk': self.template.id}))
        self.assertEqual(response.status_code, 302)
        self.template.refresh_from_db()
        self.assertIsNotNone(self.template.deleted_at)

        # Try editing SMTP config - should succeed
        response = self.client.post(reverse('accounts:smtp_edit', kwargs={'pk': self.smtp_config.id}), {
            'name': 'Updated SMTP Name',
            'smtp_host': 'smtp.updated.com',
            'smtp_port': 587,
            'smtp_username': 'updated@example.com',
        })
        self.assertEqual(response.status_code, 302)
        self.smtp_config.refresh_from_db()
        self.assertEqual(self.smtp_config.name, 'Updated SMTP Name')

        # Try deleting SMTP config - should succeed (soft delete)
        response = self.client.post(reverse('accounts:smtp_delete', kwargs={'pk': self.smtp_config.id}))
        self.assertEqual(response.status_code, 302)
        self.smtp_config.refresh_from_db()
        self.assertIsNotNone(self.smtp_config.deleted_at)

    def test_soft_deleted_campaign_actions_return_404(self):
        """Guardrail: user-facing campaign views must resolve via EmailCampaign.active,
        so a soft-deleted campaign is unreachable by URL (regression for B-031).
        Catches any view that regresses to the unfiltered objects manager."""
        from django.urls import reverse

        self.campaign.delete()  # soft delete
        self.campaign.refresh_from_db()
        self.assertIsNotNone(self.campaign.deleted_at)

        for name in ('campaign_detail', 'campaign_queue', 'campaign_cancel', 'campaign_restart'):
            url = reverse(f'emails:{name}', kwargs={'pk': self.campaign.pk})
            response = self.client.post(url)
            self.assertEqual(response.status_code, 404, name)

        # GET-only endpoints (B-050: status API leaked recipient rows after soft delete).
        for name in ('campaign_status_api',):
            url = reverse(f'emails:{name}', kwargs={'pk': self.campaign.pk})
            response = self.client.get(url)
            self.assertEqual(response.status_code, 404, name)


class LogoSizeAdjustmentTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="testuser@example.com",
            password="testpassword123"
        )
        self.client.force_login(self.user)

    def test_rendering_logo_size(self):
        from emails import email_designs
        
        # Test standard simple layout with height=80
        html_simple, _ = email_designs.render(
            'simple',
            {'heading': 'Hello'},
            logo_url='https://example.com/logo.png',
            logo_size=80
        )
        self.assertIn('height="80"', html_simple)
        self.assertIn('max-height:96px', html_simple)  # 80 * 1.2 = 96
        
        # Test announcement layout (0.9 * logo_size = 72)
        html_ann, _ = email_designs.render(
            'announcement',
            {'heading': 'Hello'},
            logo_url='https://example.com/logo.png',
            logo_size=80
        )
        self.assertIn('height="72"', html_ann)
        self.assertIn('max-height:86px', html_ann)  # 72 * 1.2 = 86.4 -> int is 86
        
        # Test newsletter layout (0.75 * logo_size = 60)
        html_news, _ = email_designs.render(
            'newsletter',
            {'heading': 'Hello'},
            logo_url='https://example.com/logo.png',
            logo_size=80
        )
        self.assertIn('height="60"', html_news)
        self.assertIn('max-height:72px', html_news)  # 60 * 1.2 = 72

    def test_email_template_form_save(self):
        from emails.forms import EmailTemplateForm
        
        form_data = {
            'name': 'Test Form Template',
            'subject': 'Form Subject',
            'layout': 'simple',
            'brand_color': '#ff0000',
            'logo_url': 'https://example.com/logo.png',
            'logo_size': 95,
            'heading': 'Welcome Heading',
            'body': 'Welcome body copy.',
        }
        form = EmailTemplateForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        template = form.save(commit=False)
        template.user = self.user
        template.save()
        
        self.assertEqual(template.logo_size, 95)
        self.assertIn('height="95"', template.body_html)
        self.assertIn('max-height:114px', template.body_html)

    def test_template_preview_endpoint(self):
        from django.urls import reverse
        
        preview_data = {
            'layout': 'simple',
            'brand_color': '#00ff00',
            'logo_url': 'https://example.com/preview_logo.png',
            'logo_size': '120',
            'heading': 'Preview Hello',
            'body': 'Preview body text',
        }
        response = self.client.post(reverse('emails:template_preview'), data=preview_data)
        self.assertEqual(response.status_code, 200)
        response_html = response.content.decode()
        self.assertIn('height="120"', response_html)
        self.assertIn('max-height:144px', response_html)

    def test_template_clone_copies_logo_size(self):
        from django.urls import reverse
        
        # Create template with custom logo size
        original = EmailTemplate.objects.create(
            user=self.user,
            name="Original Template",
            subject="Original Subj",
            layout="simple",
            brand_color="#0000ff",
            logo_url="https://example.com/orig.png",
            logo_size=110,
            body_html="<img src='orig.png' height='110'>"
        )
        
        response = self.client.post(reverse('emails:template_clone', kwargs={'pk': original.id}))
        self.assertEqual(response.status_code, 302)
        
        # Get cloned template
        clone = EmailTemplate.objects.get(name="Original Template (copy)", user=self.user)
        self.assertEqual(clone.logo_size, 110)


class CampaignActionValidationTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="testuser@example.com",
            password="testpassword123"
        )
        self.client.force_login(self.user)
        self.smtp_config = SMTPConfig.objects.create(
            user=self.user,
            name="Test SMTP",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="test@example.com",
            is_active=True
        )
        self.template = EmailTemplate.objects.create(
            user=self.user,
            name="Test Template",
            subject="Subj",
            body_html="Body"
        )
        self.campaign = EmailCampaign.objects.create(
            user=self.user,
            name="Test Campaign",
            smtp_config=self.smtp_config,
            template=self.template,
            status="draft"
        )

    def test_queue_zero_recipients_blocked(self):
        from django.urls import reverse
        # Campaign has 0 recipients by default
        response = self.client.post(reverse('emails:campaign_queue', kwargs={'pk': self.campaign.id}))
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'draft')  # remains draft

    def test_resume_already_sent_blocked(self):
        from django.urls import reverse
        # Add recipient and mark as sent
        Recipient.objects.create(campaign=self.campaign, email="test@test.com", status="sent")
        self.campaign.status = "cancelled"
        self.campaign.save()
        
        response = self.client.post(reverse('emails:campaign_queue', kwargs={'pk': self.campaign.id}))
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'cancelled')  # remains cancelled

    def test_cancel_completed_warning(self):
        from django.urls import reverse
        from django.contrib.messages import get_messages
        
        self.campaign.status = "completed"
        self.campaign.save()
        
        response = self.client.post(reverse('emails:campaign_cancel', kwargs={'pk': self.campaign.id}))
        self.assertEqual(response.status_code, 302)
        
        # Check messages
        messages = [m.message for m in get_messages(response.wsgi_request)]
        self.assertIn("Campaign cannot be cancelled because it is in 'completed' status.", messages)

    def test_restart_zero_recipients_blocked(self):
        from django.urls import reverse
        self.campaign.status = "completed"
        self.campaign.save()
        
        response = self.client.post(reverse('emails:campaign_restart', kwargs={'pk': self.campaign.id}))
        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, 'completed')  # remains completed


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class RecipientDocumentServingTests(TestCase):
    """Access control on the authenticated generated-document endpoint (B-047)."""

    def setUp(self):
        self.owner = User.objects.create_user(email="docowner@test.com", password="pw12345678")
        self.other = User.objects.create_user(email="docother@test.com", password="pw12345678")
        self.smtp = SMTPConfig.objects.create(
            user=self.owner, name="s", smtp_username="s@t.com", is_active=True,
        )
        self.campaign = EmailCampaign.objects.create(
            user=self.owner, name="docs", smtp_config=self.smtp, status="completed",
            is_document_campaign=True,
        )
        self.recipient = Recipient.objects.create(campaign=self.campaign, email="r@t.com", name="R")
        self.recipient.document_file.save("doc.pdf", ContentFile(b"%PDF-1.4 test"), save=True)

    def _url(self):
        return reverse("emails:recipient_document", kwargs={"pk": self.recipient.id})

    def test_owner_can_download(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self._url()).status_code, 200)

    def test_other_user_cannot_download(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_anonymous_is_redirected(self):
        self.assertEqual(self.client.get(self._url()).status_code, 302)

    def test_document_path_is_randomized(self):
        # Recipient name/id and the caller filename must not leak into the path.
        name = self.recipient.document_file.name
        self.assertTrue(name.startswith("generated_documents/"))
        self.assertTrue(name.endswith(".pdf"))
        self.assertNotEqual(name.split("/")[-1], "doc.pdf")

