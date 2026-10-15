import io

from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from PIL import Image

from accounts.models import SMTPConfig
from billing.models import Plan, Subscription
from emails.models import EmailTemplate, EmailCampaign
from documents.models import DocumentTemplate
from documents.forms import DocumentTextFieldFormSet
from documents.utils import resolve_values, generate_document_bytes


def _png_upload():
    buf = io.BytesIO()
    Image.new('RGB', (800, 600), 'white').save(buf, 'PNG')
    buf.seek(0)
    return SimpleUploadedFile('bg.png', buf.read(), content_type='image/png')


class DocumentUITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email='doc@test.com', password='pw12345678')
        self.smtp = SMTPConfig.objects.create(user=self.user, name='c', smtp_username='doc@test.com', is_active=True)
        self.smtp.set_app_password('pw')
        self.smtp.save()
        self.email_tpl = EmailTemplate.objects.create(user=self.user, name='n', subject='Hi', body_html='<p>Hi</p>')
        self.client.login(email='doc@test.com', password='pw12345678')
        self.prefix = DocumentTextFieldFormSet().prefix

    def _create_template(self):
        p = self.prefix
        data = {
            'name': 'Cert', 'output_format': 'pdf', 'template_file': _png_upload(),
            f'{p}-TOTAL_FORMS': '2', f'{p}-INITIAL_FORMS': '0',
            f'{p}-MIN_NUM_FORMS': '1', f'{p}-MAX_NUM_FORMS': '1000',
            f'{p}-0-field_name': 'name', f'{p}-0-label': 'Name', f'{p}-0-x_start': '100',
            f'{p}-0-x_end': '700', f'{p}-0-y_coordinate': '250', f'{p}-0-font_size': '48',
            f'{p}-0-font_color': '#000000', f'{p}-0-alignment': 'center',
            f'{p}-1-field_name': 'course', f'{p}-1-label': 'Course', f'{p}-1-x_start': '100',
            f'{p}-1-x_end': '700', f'{p}-1-y_coordinate': '350', f'{p}-1-font_size': '32',
            f'{p}-1-font_color': '#333', f'{p}-1-alignment': 'center',
        }
        return self.client.post(reverse('documents:create'), data)

    def test_create_template_with_multiple_fields(self):
        resp = self._create_template()
        self.assertEqual(resp.status_code, 302)
        tpl = DocumentTemplate.objects.get(name='Cert')
        self.assertEqual(tpl.user, self.user)
        self.assertEqual(
            list(tpl.text_fields.order_by('id').values_list('field_name', flat=True)),
            ['name', 'course'],
        )

    def test_template_requires_at_least_one_field(self):
        p = self.prefix
        resp = self.client.post(reverse('documents:create'), {
            'name': 'Empty', 'output_format': 'pdf', 'template_file': _png_upload(),
            f'{p}-TOTAL_FORMS': '0', f'{p}-INITIAL_FORMS': '0',
            f'{p}-MIN_NUM_FORMS': '1', f'{p}-MAX_NUM_FORMS': '1000',
        })
        self.assertEqual(resp.status_code, 200)  # re-rendered with errors
        self.assertFalse(DocumentTemplate.objects.filter(name='Empty').exists())

    def test_campaign_page_redirects_without_template(self):
        resp = self.client.get(reverse('documents:campaign_create'))
        self.assertEqual(resp.status_code, 302)  # prerequisite: needs a template

    def test_create_document_campaign_generates_documents(self):
        self._create_template()
        tpl = DocumentTemplate.objects.get(name='Cert')
        csv = SimpleUploadedFile(
            'r.csv', b'email,name,course\nx@t.com,Alice,Django\ny@t.com,Bob,Django\n',
            content_type='text/csv',
        )
        resp = self.client.post(reverse('documents:campaign_create'), {
            'campaign_name': 'Cohort', 'document_template': str(tpl.pk),
            'email_template': str(self.email_tpl.pk), 'smtp_config': str(self.smtp.pk),
            'csv_file': csv,  # auto_send omitted -> False, so no SMTP needed
        })
        self.assertEqual(resp.status_code, 302)
        camp = EmailCampaign.objects.get(name='Cohort')
        self.assertTrue(camp.is_document_campaign)
        self.assertEqual(camp.document_template_id, tpl.pk)
        self.assertEqual(camp.recipients.count(), 2)
        # Eager Celery generated a document for each recipient.
        self.assertEqual(camp.recipients.exclude(document_file='').count(), 2)


class DocumentRenderTests(TestCase):
    def test_resolve_values_uses_name_and_csv_columns(self):
        user = get_user_model().objects.create_user(email='r@test.com', password='pw12345678')
        tpl = DocumentTemplate.objects.create(user=user, name='t', template_file=_png_upload())
        tpl.text_fields.create(field_name='name', label='Name', x_start=0, x_end=100, y_coordinate=10)
        tpl.text_fields.create(field_name='course', label='Course', x_start=0, x_end=100, y_coordinate=40)

        class R:
            name = 'Alice'
            context_data = {'course': 'Django 101'}
        values = resolve_values(R(), tpl, {})
        self.assertEqual(values['name'], 'Alice')
        self.assertEqual(values['course'], 'Django 101')
        content, ext = generate_document_bytes(tpl, values)
        self.assertEqual(ext, 'pdf')
        self.assertTrue(content[:4] == b'%PDF')


class GlobalTypographyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email='typo@test.com', password='pw12345678')

    def _template(self, use_global, **global_kwargs):
        tpl = DocumentTemplate.objects.create(
            user=self.user, name='t', template_file=_png_upload(),
            use_global_typography=use_global, **global_kwargs,
        )
        # Two fields with deliberately different per-field typography.
        tpl.text_fields.create(field_name='name', label='Name', x_start=0, x_end=400,
                               y_coordinate=100, font_size=60, font_color='#ff0000', alignment='left')
        tpl.text_fields.create(field_name='course', label='Course', x_start=0, x_end=400,
                               y_coordinate=200, font_size=20, font_color='#00ff00', alignment='right')
        return tpl

    def test_global_typography_overrides_per_field(self):
        values = {'name': 'Alice', 'course': 'Django'}
        per_field = self._template(False)
        glob = self._template(True, global_font_size=30, global_font_color='#000000', global_alignment='center')
        from documents.utils import generate_document_bytes
        a, _ = generate_document_bytes(per_field, values)
        b, _ = generate_document_bytes(glob, values)
        # Global mode renders with different size/color/alignment, so output differs.
        self.assertNotEqual(a, b)

    def test_create_template_with_global_typography_via_view(self):
        self.client.login(email='typo@test.com', password='pw12345678')
        p = DocumentTextFieldFormSet().prefix
        resp = self.client.post(reverse('documents:create'), {
            'name': 'Global Cert', 'output_format': 'pdf', 'template_file': _png_upload(),
            'use_global_typography': 'on', 'global_font_size': '64',
            'global_font_color': '#123456', 'global_alignment': 'right',
            f'{p}-TOTAL_FORMS': '1', f'{p}-INITIAL_FORMS': '0',
            f'{p}-MIN_NUM_FORMS': '1', f'{p}-MAX_NUM_FORMS': '1000',
            f'{p}-0-field_name': 'name', f'{p}-0-label': 'Name', f'{p}-0-x_start': '0',
            f'{p}-0-x_end': '400', f'{p}-0-y_coordinate': '100', f'{p}-0-font_size': '48',
            f'{p}-0-font_color': '#000000', f'{p}-0-alignment': 'center',
        })
        self.assertEqual(resp.status_code, 302)
        tpl = DocumentTemplate.objects.get(name='Global Cert')
        self.assertTrue(tpl.use_global_typography)
        self.assertEqual(tpl.global_font_size, 64)
        self.assertEqual(tpl.global_alignment, 'right')


class SchedulingPersistenceTests(TestCase):
    """Regression: scheduling fields must actually be saved to the campaign."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(email='sch@test.com', password='pw12345678')
        self.smtp = SMTPConfig.objects.create(user=self.user, name='c', smtp_username='sch@test.com', is_active=True)
        self.smtp.set_app_password('pw'); self.smtp.save()
        self.email_tpl = EmailTemplate.objects.create(user=self.user, name='n', subject='Hi', body_html='<p>Hi</p>')
        # Recurring schedules are a plan feature (B-057), so the user needs an
        # active plan that includes it.
        plan = Plan.objects.create(tier='Creator', price=499, currency='INR',
                                   recurring_scheduling=True)
        self.subscription = Subscription.objects.create(user=self.user, plan=plan,
                                                        status=Subscription.ACTIVE)
        self.client.login(email='sch@test.com', password='pw12345678')
        from django.utils import timezone
        from datetime import timedelta
        self.future = (timezone.now() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')

    def _post_recurring_campaign(self):
        return self.client.post(reverse('emails:campaign_create'), {
            'name': 'StdSched', 'template': str(self.email_tpl.pk), 'smtp_config': str(self.smtp.pk),
            'quick_recipients': 'a@t.com',
            'schedule_type': 'weekly', 'schedule_time': '09:00',
            'day_of_week': '2', 'is_recurring': 'on',
        })

    def test_standard_campaign_persists_schedule(self):
        self._post_recurring_campaign()
        c = EmailCampaign.objects.get(name='StdSched')
        self.assertEqual(c.status, 'scheduled')
        self.assertEqual(c.schedule_type, 'weekly')
        self.assertEqual(c.day_of_week, 2)
        self.assertTrue(c.is_recurring)
        self.assertIsNotNone(c.next_run_at)

    def test_free_tier_cannot_create_recurring_campaign(self):
        self.subscription.status = Subscription.EXPIRED
        self.subscription.save(update_fields=['status'])
        resp = self._post_recurring_campaign()
        self.assertEqual(resp.status_code, 200)  # form re-rendered with the error
        self.assertFalse(EmailCampaign.objects.filter(name='StdSched').exists())

    def test_document_campaign_persists_schedule_and_defers_generation(self):
        from django.urls import reverse
        # Create a template first.
        p = DocumentTextFieldFormSet().prefix
        self.client.post(reverse('documents:create'), {
            'name': 'T', 'output_format': 'pdf', 'template_file': _png_upload(),
            f'{p}-TOTAL_FORMS': '1', f'{p}-INITIAL_FORMS': '0',
            f'{p}-MIN_NUM_FORMS': '1', f'{p}-MAX_NUM_FORMS': '1000',
            f'{p}-0-field_name': 'name', f'{p}-0-label': 'N', f'{p}-0-x_start': '0',
            f'{p}-0-x_end': '400', f'{p}-0-y_coordinate': '100', f'{p}-0-font_size': '40',
            f'{p}-0-font_color': '#000000', f'{p}-0-alignment': 'center',
        })
        tpl = DocumentTemplate.objects.get(name='T')
        csv = SimpleUploadedFile('r.csv', b'email,name\nx@t.com,Al\n', content_type='text/csv')
        self.client.post(reverse('documents:campaign_create'), {
            'campaign_name': 'DocSched', 'document_template': str(tpl.pk),
            'email_template': str(self.email_tpl.pk), 'smtp_config': str(self.smtp.pk),
            'csv_file': csv,
            'schedule_type': 'interval', 'schedule_time': '09:00', 'interval_days': '3',
        })
        c = EmailCampaign.objects.get(name='DocSched')
        self.assertEqual(c.status, 'scheduled')
        self.assertEqual(c.schedule_type, 'interval')
        self.assertEqual(c.interval_days, 3)
        self.assertIsNotNone(c.next_run_at)
        self.assertTrue(c.is_document_campaign)
        self.assertEqual(c.recipients.count(), 1)
        # Generation is deferred to the beat task, so nothing is generated yet.
        self.assertEqual(c.recipients.exclude(document_file='').count(), 0)


class TemplateBackgroundServingTests(TestCase):
    """B-049: template backgrounds are served only through the ownership-checked
    documents:template_background view, not the public media URL."""

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(email='owner@t.com', password='pw12345678')
        self.other = User.objects.create_user(email='other@t.com', password='pw12345678')
        self.template = DocumentTemplate.objects.create(
            user=self.owner, name='BG', template_file=_png_upload(),
        )
        self.url = reverse('documents:template_background', kwargs={'pk': self.template.pk})

    def test_owner_can_fetch_background(self):
        self.client.login(email='owner@t.com', password='pw12345678')
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'image/png')

    def test_other_user_gets_404(self):
        self.client.login(email='other@t.com', password='pw12345678')
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_anonymous_redirected_to_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_soft_deleted_template_gets_404(self):
        self.template.delete()  # soft delete
        self.client.login(email='owner@t.com', password='pw12345678')
        self.assertEqual(self.client.get(self.url).status_code, 404)
