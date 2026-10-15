from django.test import RequestFactory, SimpleTestCase, TestCase

from billing.models import CreditPack, Plan
from core.pricing import get_public_pricing, is_india_request


class PricingDetectionTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def request(self, **headers):
        return self.factory.get("/", HTTP_HOST="emailops.example", **headers)

    def test_cloudflare_india_header_detected(self):
        self.assertTrue(is_india_request(self.request(HTTP_CF_IPCOUNTRY="IN")))

    def test_non_india_country_header_not_detected(self):
        self.assertFalse(is_india_request(self.request(HTTP_CF_IPCOUNTRY="US")))

    def test_accept_language_india_fallback_detected(self):
        self.assertTrue(is_india_request(
            self.request(HTTP_ACCEPT_LANGUAGE="en-IN,en;q=0.9")))

    def test_local_development_defaults_to_india(self):
        self.assertTrue(is_india_request(
            self.factory.get("/", HTTP_HOST="127.0.0.1:8000")))

    def test_proxy_country_header_global(self):
        self.assertFalse(is_india_request(
            self.request(HTTP_X_VERCEL_IP_COUNTRY="GB")))


class PublicPricingTests(TestCase):
    """get_public_pricing reads the billing catalogue (single source of truth)."""

    def setUp(self):
        self.factory = RequestFactory()
        self.inr_pack = CreditPack.objects.create(
            name='Starter 100', credits=100, price=199, currency='INR')
        self.usd_pack = CreditPack.objects.create(
            name='Starter USD', credits=100, price=5, currency='USD')
        self.inr_plan = Plan.objects.create(
            tier='Creator', price=499, currency='INR', credits_per_cycle=1000)

    def request(self, **headers):
        return self.factory.get("/", HTTP_HOST="emailops.example", **headers)

    def test_india_gets_inr_items(self):
        pricing = get_public_pricing(self.request(HTTP_CF_IPCOUNTRY="IN"))
        self.assertEqual(pricing["currency"], "INR")
        self.assertEqual(pricing["packs"], [self.inr_pack])
        self.assertEqual(pricing["plans"], [self.inr_plan])
        self.assertTrue(pricing["has_items"])

    def test_global_gets_usd_items(self):
        pricing = get_public_pricing(self.request(HTTP_CF_IPCOUNTRY="US"))
        self.assertEqual(pricing["currency"], "USD")
        self.assertEqual(pricing["packs"], [self.usd_pack])
        self.assertEqual(pricing["plans"], [])

    def test_missing_currency_falls_back_to_all_active(self):
        CreditPack.objects.filter(currency='USD').delete()
        pricing = get_public_pricing(self.request(HTTP_CF_IPCOUNTRY="US"))
        # No USD items at all: fall back to the active catalogue rather than
        # rendering an empty page.
        self.assertTrue(pricing["has_items"])
        self.assertEqual(pricing["currency"], "INR")
        self.assertIn(self.inr_pack, pricing["packs"])

    def test_inactive_items_are_excluded(self):
        self.inr_pack.is_active = False
        self.inr_pack.save(update_fields=['is_active'])
        pricing = get_public_pricing(self.request(HTTP_CF_IPCOUNTRY="IN"))
        self.assertNotIn(self.inr_pack, pricing["packs"])

    def test_empty_catalogue_degrades_gracefully(self):
        CreditPack.objects.all().delete()
        Plan.objects.all().delete()
        pricing = get_public_pricing(self.request(HTTP_CF_IPCOUNTRY="IN"))
        self.assertFalse(pricing["has_items"])


class LandingPricingRenderTests(TestCase):
    def setUp(self):
        CreditPack.objects.create(name='Starter 100', credits=100, price=199,
                                  currency='INR')
        CreditPack.objects.create(name='Starter USD', credits=100, price=5,
                                  currency='USD')

    def test_landing_renders_inr_for_india(self):
        response = self.client.get("/", HTTP_CF_IPCOUNTRY="IN")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Starter 100")
        self.assertContains(response, "Prices shown in INR.")
        self.assertNotContains(response, "Starter USD")

    def test_landing_renders_usd_for_global(self):
        response = self.client.get("/", HTTP_CF_IPCOUNTRY="US")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Starter USD")
        self.assertContains(response, "Prices shown in USD.")
        self.assertNotContains(response, "Starter 100")

    def test_landing_survives_empty_catalogue(self):
        CreditPack.objects.all().delete()
        response = self.client.get("/", HTTP_CF_IPCOUNTRY="IN")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pricing is being finalised")


import os
import tempfile
from datetime import timedelta

from django.core.files.base import ContentFile
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from django.contrib.auth import get_user_model

from accounts.models import SMTPConfig
from emails.models import EmailCampaign, EmailTemplate, Recipient
from documents.models import DocumentTemplate, Signature
from core.models import PurgeLog
from core.tasks import purge_soft_deleted_records


@override_settings(SOFT_DELETE_PURGE_RETENTION_DAYS=90)
class PurgeSoftDeletedTests(TestCase):
    """Business rules for core.tasks.purge_soft_deleted_records."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(email='purge@test.com', password='pw12345678')
        self.old = timezone.now() - timedelta(days=200)
        self.recent = timezone.now() - timedelta(days=10)

    def _smtp(self, name, deleted=None):
        s = SMTPConfig.objects.create(user=self.user, name=name, smtp_username=f'{name}@t.com', is_active=False)
        s.set_app_password('pw')
        s.save()
        if deleted:
            SMTPConfig.objects.filter(pk=s.pk).update(deleted_at=deleted)
        return s

    def _campaign(self, name, status, deleted, smtp, template=None):
        c = EmailCampaign.objects.create(user=self.user, name=name, status=status, smtp_config=smtp, template=template)
        EmailCampaign.objects.filter(pk=c.pk).update(deleted_at=deleted)
        return c

    def test_eligible_terminal_campaign_is_purged_with_recipients(self):
        c = self._campaign('done', 'completed', self.old, self._smtp('s'))
        Recipient.objects.create(campaign=c, email='a@t.com', name='A')
        summary = purge_soft_deleted_records()
        self.assertEqual(summary['campaigns'], 1)
        self.assertFalse(EmailCampaign.objects.filter(pk=c.pk).exists())
        self.assertEqual(Recipient.objects.count(), 0)  # cascade

    def test_active_state_campaign_is_never_purged(self):
        for status in ('queued', 'sending', 'scheduled'):
            c = self._campaign(f'active-{status}', status, self.old, self._smtp(f'sm-{status}'))
            purge_soft_deleted_records()
            self.assertTrue(EmailCampaign.objects.filter(pk=c.pk).exists(), status)

    def test_recent_soft_deleted_campaign_is_kept(self):
        c = self._campaign('fresh', 'completed', self.recent, self._smtp('s'))
        purge_soft_deleted_records()
        self.assertTrue(EmailCampaign.objects.filter(pk=c.pk).exists())

    def test_template_referenced_by_kept_campaign_is_skipped(self):
        smtp = self._smtp('s')
        t = EmailTemplate.objects.create(user=self.user, name='t', subject='s', body_html='<p>x</p>')
        EmailTemplate.objects.filter(pk=t.pk).update(deleted_at=self.old)
        # referenced by an active (queued) campaign that won't be purged
        self._campaign('keep', 'queued', self.old, smtp, template=t)
        summary = purge_soft_deleted_records()
        self.assertEqual(summary['email_templates'], 0)
        self.assertTrue(EmailTemplate.objects.filter(pk=t.pk).exists())

    def test_dependency_order_frees_template_and_smtp(self):
        smtp = self._smtp('s', deleted=self.old)
        t = EmailTemplate.objects.create(user=self.user, name='t', subject='s', body_html='<p>x</p>')
        EmailTemplate.objects.filter(pk=t.pk).update(deleted_at=self.old)
        self._campaign('done', 'completed', self.old, smtp, template=t)
        summary = purge_soft_deleted_records()
        # campaign purged first, which frees both the template and the smtp config
        self.assertEqual(summary['campaigns'], 1)
        self.assertEqual(summary['email_templates'], 1)
        self.assertEqual(summary['smtp_configs'], 1)
        self.assertFalse(EmailTemplate.objects.filter(pk=t.pk).exists())
        self.assertFalse(SMTPConfig.objects.filter(pk=smtp.pk).exists())

    def test_unreferenced_document_templates_purged(self):
        d = DocumentTemplate.objects.create(user=self.user, name='d', template_file='x.png')
        DocumentTemplate.objects.filter(pk=d.pk).update(deleted_at=self.old)
        summary = purge_soft_deleted_records()
        self.assertEqual(summary['document_templates'], 1)

    def test_soft_deleted_signature_purged(self):
        s = Signature.objects.create(user=self.user, name='old-sig', original_image='originals/x.png')
        Signature.objects.filter(pk=s.pk).update(deleted_at=self.old)
        summary = purge_soft_deleted_records()
        self.assertEqual(summary['signatures'], 1)
        self.assertFalse(Signature.objects.filter(pk=s.pk).exists())

    def test_recent_signature_is_kept(self):
        s = Signature.objects.create(user=self.user, name='fresh-sig', original_image='originals/y.png')
        Signature.objects.filter(pk=s.pk).update(deleted_at=self.recent)
        summary = purge_soft_deleted_records()
        self.assertEqual(summary['signatures'], 0)
        self.assertTrue(Signature.objects.filter(pk=s.pk).exists())

    def test_purge_writes_audit_log(self):
        self._campaign('done', 'completed', self.old, self._smtp('s'))
        purge_soft_deleted_records()
        self.assertEqual(PurgeLog.objects.count(), 1)
        log = PurgeLog.objects.latest('ran_at')
        self.assertEqual(log.retention_days, 90)
        self.assertEqual(log.campaigns_purged, 1)

    def test_soft_deleted_draft_campaign_is_purged(self):
        # A draft soft-deleted past retention is abandoned (never sent) and must
        # be reclaimed along with its recipients. Regression for the gap where
        # only terminal statuses were purgeable, leaking drafts forever.
        c = self._campaign('abandoned', 'draft', self.old, self._smtp('s'))
        Recipient.objects.create(campaign=c, email='a@t.com', name='A')
        summary = purge_soft_deleted_records()
        self.assertEqual(summary['campaigns'], 1)
        self.assertFalse(EmailCampaign.objects.filter(pk=c.pk).exists())
        self.assertEqual(Recipient.objects.count(), 0)  # cascade

    def test_draft_purge_frees_referenced_template_and_smtp(self):
        # A soft-deleted draft used to pin its template and SMTP config from ever
        # purging (the reference check counts unfiltered campaigns). Purging the
        # draft first must free both on the same run.
        smtp = self._smtp('s', deleted=self.old)
        t = EmailTemplate.objects.create(user=self.user, name='t', subject='s', body_html='<p>x</p>')
        EmailTemplate.objects.filter(pk=t.pk).update(deleted_at=self.old)
        self._campaign('abandoned', 'draft', self.old, smtp, template=t)
        summary = purge_soft_deleted_records()
        self.assertEqual(summary['campaigns'], 1)
        self.assertEqual(summary['email_templates'], 1)
        self.assertEqual(summary['smtp_configs'], 1)
        self.assertFalse(EmailTemplate.objects.filter(pk=t.pk).exists())
        self.assertFalse(SMTPConfig.objects.filter(pk=smtp.pk).exists())


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class FileCleanupSignalTests(TestCase):
    """Behavior of the centralized, transaction-safe cleanup in core.file_cleanup."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(email='fc@test.com', password='pw12345678')

    def _make_signature(self, content=b'data'):
        sig = Signature(user=self.user, name='sig')
        sig.original_image.save('orig.png', ContentFile(content), save=True)
        return sig

    def test_hard_delete_removes_file_on_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            sig = self._make_signature()
        path = sig.original_image.path
        self.assertTrue(os.path.exists(path))
        with self.captureOnCommitCallbacks(execute=True):
            sig.hard_delete()
        self.assertFalse(os.path.exists(path))

    def test_soft_delete_keeps_file(self):
        with self.captureOnCommitCallbacks(execute=True):
            sig = self._make_signature()
        path = sig.original_image.path
        with self.captureOnCommitCallbacks(execute=True):
            sig.delete()  # soft delete: file must be retained
        self.assertTrue(os.path.exists(path))

    def test_replacing_file_deletes_old(self):
        with self.captureOnCommitCallbacks(execute=True):
            sig = self._make_signature()
        old_path = sig.original_image.path
        with self.captureOnCommitCallbacks(execute=True):
            sig.original_image.save('new.png', ContentFile(b'new'), save=True)
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(os.path.exists(sig.original_image.path))

    def test_non_file_update_keeps_file(self):
        with self.captureOnCommitCallbacks(execute=True):
            sig = self._make_signature()
        path = sig.original_image.path
        with self.captureOnCommitCallbacks(execute=True):
            sig.processing_status = Signature.STATUS_READY
            sig.save(update_fields=['processing_status', 'updated_at'])
        self.assertTrue(os.path.exists(path))

    def test_rolled_back_delete_keeps_file(self):
        with self.captureOnCommitCallbacks(execute=True):
            sig = self._make_signature()
        path = sig.original_image.path
        pk = sig.pk  # delete() clears instance.pk, so capture it first
        try:
            with transaction.atomic():
                sig.hard_delete()
                raise RuntimeError('boom')
        except RuntimeError:
            pass
        self.assertTrue(os.path.exists(path))
        self.assertTrue(Signature.objects.filter(pk=pk).exists())


class FindOrphanMediaCommandTests(TestCase):
    """Reconciliation command: report/delete files on disk with no DB reference.

    Each test gets its own MEDIA_ROOT: TestCase rolls back the DB between tests
    but not the filesystem, so a shared dir would let one test's leftover files
    look like orphans to the next.
    """

    def setUp(self):
        import shutil
        self.tmp = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.tmp)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.user = get_user_model().objects.create_user(email='om@test.com', password='pw12345678')

    def _referenced_signature(self, content=b'data'):
        sig = Signature(user=self.user, name='sig')
        with self.captureOnCommitCallbacks(execute=True):
            sig.original_image.save('referenced.png', ContentFile(content), save=True)
        return sig

    def _plant_orphan(self, name='orphans/loose.bin', content=b'junk'):
        from django.conf import settings
        abs_path = os.path.join(str(settings.MEDIA_ROOT), name)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'wb') as fh:
            fh.write(content)
        return abs_path

    def _run(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('find_orphan_media', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_orphan_without_deleting(self):
        sig = self._referenced_signature()
        orphan = self._plant_orphan()
        output = self._run()
        self.assertIn('orphans/loose.bin', output)
        self.assertTrue(os.path.exists(orphan))           # dry run: untouched
        self.assertTrue(os.path.exists(sig.original_image.path))

    def test_referenced_file_is_not_orphan(self):
        sig = self._referenced_signature()
        output = self._run('--delete')
        self.assertNotIn('referenced.png', output)
        self.assertTrue(os.path.exists(sig.original_image.path))

    def test_delete_removes_orphan(self):
        orphan = self._plant_orphan()
        self._run('--delete')
        self.assertFalse(os.path.exists(orphan))

    def test_soft_deleted_file_is_retained_not_orphan(self):
        sig = self._referenced_signature()
        with self.captureOnCommitCallbacks(execute=True):
            sig.delete()  # soft delete keeps the file and the DB row
        output = self._run('--delete')
        self.assertTrue(os.path.exists(sig.original_image.path))
        self.assertNotIn('referenced.png', output)

    def test_older_than_skips_recent_files(self):
        orphan = self._plant_orphan()
        output = self._run('--delete', '--older-than', '1')
        self.assertTrue(os.path.exists(orphan))           # too recent to delete
        self.assertIn('skipped 1 recent', output)
