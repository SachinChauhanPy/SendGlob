"""
Tests for the signatures feature: the background-removal pipeline, the
``Signature`` model/lifecycle, the Celery processing task, per-recipient
resolution, signature compositing, and the library views.
"""
import io
import tempfile

from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from PIL import Image, ImageDraw

from documents.models import DocumentTemplate, Signature
from documents.signature_processing import (
    process_signature_bytes, SignatureProcessingError,
)
from documents.tasks import process_signature_task
from documents.utils import resolve_signatures, generate_document_bytes


def _signature_png(size=(400, 200)):
    """A white page with dark strokes — a stand-in for a photographed signature."""
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.line([(20, 150), (120, 40), (220, 150), (320, 40)], fill=(10, 10, 40), width=6)
    d.line([(40, 120), (300, 120)], fill=(15, 15, 45), width=4)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _solid_png(color, size=(300, 150)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _photo_png(size=(640, 480)):
    """A busy, colourful non-signature image (not a signature on a plain page)."""
    import numpy as np
    rng = np.random.default_rng(7)
    arr = rng.integers(0, 255, size=(size[1], size[0], 3), dtype=np.uint8)
    # smooth it a bit so it reads like a photo, not pure noise
    img = Image.fromarray(arr).resize((size[0] // 8, size[1] // 8)).resize(size)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _bg_png():
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buf, "PNG")
    buf.seek(0)
    return SimpleUploadedFile("bg.png", buf.read(), content_type="image/png")


class PipelineTests(TestCase):
    def test_clean_signature_produces_transparent_png(self):
        png, confidence = process_signature_bytes(_signature_png())
        out = Image.open(io.BytesIO(png))
        self.assertEqual(out.mode, "RGBA")
        alpha = out.getchannel("A")
        # Some pixels are fully transparent (background) and some opaque (ink).
        extrema = alpha.getextrema()
        self.assertEqual(extrema[0], 0)
        self.assertGreater(extrema[1], 200)
        self.assertGreaterEqual(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)
        # Cropped tighter than the original canvas.
        self.assertLess(out.width, 400)

    def test_blank_page_is_rejected(self):
        with self.assertRaises(SignatureProcessingError):
            process_signature_bytes(_solid_png("white"))

    def test_near_black_image_is_rejected(self):
        with self.assertRaises(SignatureProcessingError):
            process_signature_bytes(_solid_png((4, 4, 4)))

    def test_unreadable_bytes_raise_domain_error(self):
        with self.assertRaises(SignatureProcessingError):
            process_signature_bytes(b"not an image")

    def test_deterministic_output(self):
        a, _ = process_signature_bytes(_signature_png())
        b, _ = process_signature_bytes(_signature_png())
        self.assertEqual(a, b)

    def test_non_signature_image_is_rejected(self):
        with self.assertRaises(SignatureProcessingError):
            process_signature_bytes(_photo_png())

    def test_mono_mode_recolors_strokes(self):
        png, _ = process_signature_bytes(_signature_png(), ink_color_mode="mono", ink_color="#ff0000")
        out = Image.open(io.BytesIO(png)).convert("RGBA")
        # Find an opaque pixel; it should carry the requested red, not the original ink color.
        found = False
        for x in range(0, out.width, 5):
            for y in range(0, out.height, 5):
                r, g, b, a = out.getpixel((x, y))
                if a > 200:
                    self.assertGreater(r, 180)
                    self.assertLess(g, 80)
                    self.assertLess(b, 80)
                    found = True
                    break
            if found:
                break
        self.assertTrue(found)


class SignatureModelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="s@test.com", password="pw12345678")

    def _make(self, name="Director", status=Signature.STATUS_PENDING):
        return Signature.objects.create(
            user=self.user, name=name,
            original_image=SimpleUploadedFile(f"{name}.png", _signature_png(), content_type="image/png"),
            processing_status=status,
        )

    def test_soft_delete_hides_from_active_only(self):
        sig = self._make()
        sig.delete()
        self.assertFalse(Signature.active.filter(pk=sig.pk).exists())
        self.assertTrue(Signature.objects.filter(pk=sig.pk).exists())

    def test_is_ready_requires_status_and_processed_file(self):
        sig = self._make(status=Signature.STATUS_READY)
        self.assertFalse(sig.is_ready)  # no processed_image yet

    def test_restore_when_name_is_free_succeeds(self):
        sig = self._make(name="Director")
        sig.delete()
        sig.restore()
        self.assertTrue(Signature.active.filter(pk=sig.pk).exists())

    def test_restore_blocked_when_active_name_clashes(self):
        from django.core.exceptions import ValidationError
        old = self._make(name="Director")
        old.delete()
        self._make(name="Director")  # new active signature reuses the name
        with self.assertRaises(ValidationError):
            old.restore()
        self.assertFalse(Signature.active.filter(pk=old.pk).exists())


class ProcessingTaskTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="t@test.com", password="pw12345678")

    def _make(self, raw):
        return Signature.objects.create(
            user=self.user, name="Sig",
            original_image=SimpleUploadedFile("u.png", raw, content_type="image/png"),
        )

    def test_task_marks_ready_and_stores_processed_image(self):
        sig = self._make(_signature_png())
        process_signature_task(sig.id)  # eager in tests
        sig.refresh_from_db()
        self.assertEqual(sig.processing_status, Signature.STATUS_READY)
        self.assertTrue(bool(sig.processed_image))
        self.assertIsNotNone(sig.confidence_score)
        self.assertTrue(sig.is_ready)

    def test_task_fails_gracefully_on_blank(self):
        sig = self._make(_solid_png("white"))
        process_signature_task(sig.id)
        sig.refresh_from_db()
        self.assertEqual(sig.processing_status, Signature.STATUS_FAILED)
        self.assertTrue(sig.error_message)
        self.assertFalse(sig.is_ready)


class ResolutionAndRenderTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="r@test.com", password="pw12345678")
        self.template = DocumentTemplate.objects.create(
            user=self.user, name="Cert", template_file=_bg_png(), output_format="png",
        )
        self.template.text_fields.create(field_name="name", label="Name", x_start=0, x_end=400, y_coordinate=20)
        self.field = self.template.signature_fields.create(
            label="Sig", source_column="signature", x_start=100, x_end=400, y_top=300, max_height=120,
        )
        self.sig = Signature.objects.create(
            user=self.user, name="Alice Sign",
            original_image=SimpleUploadedFile("a.png", _signature_png(), content_type="image/png"),
        )
        process_signature_task(self.sig.id)
        self.sig.refresh_from_db()

    class _Recipient:
        def __init__(self, name, context):
            self.name = name
            self.context_data = context

    def test_resolve_matches_by_name_case_insensitive(self):
        rec = self._Recipient("Alice", {"signature": "alice sign"})
        resolved = resolve_signatures(rec, self.template, self.user)
        self.assertEqual(resolved[self.field.id].pk, self.sig.pk)

    def test_resolve_missing_reference_is_none(self):
        rec = self._Recipient("Bob", {"signature": "nobody"})
        resolved = resolve_signatures(rec, self.template, self.user)
        self.assertIsNone(resolved[self.field.id])

    def test_resolve_blank_reference_is_none(self):
        rec = self._Recipient("Bob", {})
        resolved = resolve_signatures(rec, self.template, self.user)
        self.assertIsNone(resolved[self.field.id])

    def test_signature_changes_rendered_output(self):
        values = {"name": "Alice"}
        without = generate_document_bytes(self.template, values, signatures={self.field.id: None})
        with_sig = generate_document_bytes(self.template, values, signatures={self.field.id: self.sig})
        self.assertNotEqual(without[0], with_sig[0])


class SignatureViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="v@test.com", password="pw12345678")
        self.client.login(email="v@test.com", password="pw12345678")

    def test_upload_creates_and_processes_signature(self):
        resp = self.client.post(reverse("documents:signature_create"), {
            "name": "My Sign", "ink_color_mode": "preserve", "ink_color": "#000000",
            "original_image": SimpleUploadedFile("s.png", _signature_png(), content_type="image/png"),
        })
        self.assertEqual(resp.status_code, 302)
        sig = Signature.active.get(user=self.user, name="My Sign")
        # Eager Celery already processed it.
        self.assertEqual(sig.processing_status, Signature.STATUS_READY)

    def test_duplicate_active_name_rejected(self):
        Signature.objects.create(
            user=self.user, name="Dup",
            original_image=SimpleUploadedFile("d.png", _signature_png(), content_type="image/png"),
        )
        resp = self.client.post(reverse("documents:signature_create"), {
            "name": "Dup", "ink_color_mode": "preserve", "ink_color": "#000000",
            "original_image": SimpleUploadedFile("d2.png", _signature_png(), content_type="image/png"),
        })
        self.assertEqual(resp.status_code, 200)  # re-rendered with error
        self.assertEqual(Signature.active.filter(user=self.user, name="Dup").count(), 1)

    def test_upload_rejects_non_signature_without_creating_record(self):
        resp = self.client.post(reverse("documents:signature_create"), {
            "name": "Photo", "ink_color_mode": "preserve", "ink_color": "#000000",
            "original_image": SimpleUploadedFile("p.png", _photo_png(), content_type="image/png"),
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Signature.active.filter(user=self.user, name="Photo").exists())

    def test_list_view_renders(self):
        resp = self.client.get(reverse("documents:signature_list"))
        self.assertEqual(resp.status_code, 200)


class SignatureEditViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="e@test.com", password="pw12345678")
        self.client.login(email="e@test.com", password="pw12345678")
        self.sig = Signature.objects.create(
            user=self.user, name="Edit Me",
            original_image=SimpleUploadedFile("e.png", _signature_png(), content_type="image/png"),
        )
        process_signature_task(self.sig.id)
        self.sig.refresh_from_db()

    def test_edit_page_renders_when_ready(self):
        resp = self.client.get(reverse("documents:signature_edit", args=[self.sig.id]))
        self.assertEqual(resp.status_code, 200)

    def test_edit_redirects_when_not_ready(self):
        self.sig.processing_status = Signature.STATUS_PENDING
        self.sig.save(update_fields=["processing_status"])
        resp = self.client.get(reverse("documents:signature_edit", args=[self.sig.id]))
        self.assertEqual(resp.status_code, 302)

    def test_save_edit_persists_uploaded_png(self):
        import base64
        buf = io.BytesIO()
        Image.new("RGBA", (60, 30), (0, 0, 0, 0)).save(buf, "PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        resp = self.client.post(reverse("documents:signature_save_edit", args=[self.sig.id]),
                                {"image_data": data_url})
        self.assertEqual(resp.status_code, 302)
        self.sig.refresh_from_db()
        self.assertEqual(self.sig.processing_status, Signature.STATUS_READY)
        # Stored name is randomized (B-047); just assert it landed in the
        # processed bucket as a PNG rather than carrying any caller-supplied name.
        self.assertTrue(self.sig.processed_image.name.startswith("signatures/processed/"))
        self.assertTrue(self.sig.processed_image.name.endswith(".png"))

    def test_save_edit_rejects_bad_payload(self):
        resp = self.client.post(reverse("documents:signature_save_edit", args=[self.sig.id]),
                                {"image_data": "not-a-data-url"})
        self.assertEqual(resp.status_code, 302)  # redirected with an error message

    def test_reprocess_restarts_processing(self):
        resp = self.client.post(reverse("documents:signature_reprocess", args=[self.sig.id]))
        self.assertEqual(resp.status_code, 302)
        self.sig.refresh_from_db()  # eager task re-ran on the valid original
        self.assertEqual(self.sig.processing_status, Signature.STATUS_READY)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class SignatureImageServingTests(TestCase):
    """Access control on the authenticated signature-image endpoint and the
    prompt file deletion on user delete (B-047)."""

    def setUp(self):
        self.owner = get_user_model().objects.create_user(email="owner@test.com", password="pw12345678")
        self.other = get_user_model().objects.create_user(email="other@test.com", password="pw12345678")
        self.sig = self._ready_signature(self.owner, "Owned")

    def _ready_signature(self, user, name):
        sig = Signature.objects.create(
            user=user, name=name,
            original_image=SimpleUploadedFile(f"{name}.png", _signature_png(), content_type="image/png"),
        )
        buf = io.BytesIO()
        Image.new("RGBA", (40, 20), (0, 0, 0, 0)).save(buf, "PNG")
        sig.processed_image.save("p.png", ContentFile(buf.getvalue()), save=False)
        sig.processing_status = Signature.STATUS_READY
        sig.save(update_fields=["processed_image", "processing_status"])
        return sig

    def _url(self, pk, variant):
        return reverse("documents:signature_image", args=[pk, variant])

    def test_owner_can_fetch_both_variants(self):
        self.client.login(email="owner@test.com", password="pw12345678")
        self.assertEqual(self.client.get(self._url(self.sig.id, "processed")).status_code, 200)
        self.assertEqual(self.client.get(self._url(self.sig.id, "original")).status_code, 200)

    def test_other_user_cannot_fetch(self):
        self.client.login(email="other@test.com", password="pw12345678")
        self.assertEqual(self.client.get(self._url(self.sig.id, "processed")).status_code, 404)
        self.assertEqual(self.client.get(self._url(self.sig.id, "original")).status_code, 404)

    def test_unknown_variant_is_404(self):
        self.client.login(email="owner@test.com", password="pw12345678")
        self.assertEqual(self.client.get(self._url(self.sig.id, "bogus")).status_code, 404)

    def test_anonymous_is_redirected_to_login(self):
        self.assertEqual(self.client.get(self._url(self.sig.id, "processed")).status_code, 302)

    def test_stored_paths_are_randomized(self):
        # No part of the uploaded/caller filename leaks into the stored path.
        self.assertTrue(self.sig.original_image.name.startswith("signatures/originals/"))
        self.assertNotIn("Owned", self.sig.original_image.name)
        self.assertNotIn("p.png", self.sig.processed_image.name)

    def test_delete_removes_files_but_keeps_soft_deleted_row(self):
        from django.core.files.storage import default_storage
        orig_name = self.sig.original_image.name
        proc_name = self.sig.processed_image.name
        self.client.login(email="owner@test.com", password="pw12345678")
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(reverse("documents:signature_delete", args=[self.sig.id]))
        self.assertEqual(resp.status_code, 302)
        # Row is soft-deleted (kept for name-history/audit) but file fields cleared.
        self.assertFalse(Signature.active.filter(pk=self.sig.pk).exists())
        self.assertTrue(Signature.objects.filter(pk=self.sig.pk).exists())
        self.sig.refresh_from_db()
        self.assertEqual(self.sig.original_image.name, "")
        self.assertFalse(self.sig.processed_image)
        # Physical files are gone immediately, not retained for the purge window.
        self.assertFalse(default_storage.exists(orig_name))
        self.assertFalse(default_storage.exists(proc_name))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class SignatureEncryptionAtRestTests(TestCase):
    """Signature image bytes are Fernet-encrypted on disk but transparently
    decrypted when opened through storage (B-048)."""

    def test_original_image_is_ciphertext_on_disk_but_readable(self):
        user = get_user_model().objects.create_user(email="enc@test.com", password="pw12345678")
        png = _signature_png()
        sig = Signature.objects.create(
            user=user, name="Enc",
            original_image=SimpleUploadedFile("e.png", png, content_type="image/png"),
        )
        # Raw on-disk bytes are ciphertext, not the original PNG.
        with open(sig.original_image.path, "rb") as fh:
            on_disk = fh.read()
        self.assertFalse(on_disk.startswith(b"\x89PNG"))
        self.assertNotEqual(on_disk, png)
        # Opening through the field decrypts back to the exact original bytes.
        with sig.original_image.open("rb") as fh:
            self.assertEqual(fh.read(), png)
