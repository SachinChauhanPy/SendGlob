# Feature Plan — Reusable Signatures & Document Signing

**Status:** Proposed
**Date:** 2026-06-08
**Scope:** `documents` app only (legacy `certificates` stays text-only)
**Owner:** TBD

---

## 1. Goal

Let a user upload a signature photo, have its background removed automatically into a clean transparent PNG, save it as a **reusable signature** in a personal library, and place **per-recipient signatures** (sourced from the campaign CSV) onto generated documents alongside the existing text fields.

This delivers three capabilities:

1. **Signature ingestion** — upload a photo, run a signature-specialized background-removal pipeline, store a clean transparent PNG.
2. **Reusable signature library** — user-owned, soft-deleted, lockable assets that survive in historical records.
3. **Document signing** — `DocumentTemplate` gains *signature placement fields* (image boxes) in addition to text fields; each recipient's signature is resolved from a CSV column at generation time.

---

## 2. Design principles followed

This plan is written to the repository's standing invariants (`CLAUDE.md`):

- **Soft delete is mandatory.** The new `Signature` and `DocumentSignatureField` models follow the existing `SoftDeleteModel` pattern so historical campaigns keep valid references.
- **Reuse before create.** The renderer extends `documents/utils.py` rather than forking it. Font/image helpers stay single-source. The CSV/column-mapping plumbing already used for text fields is reused for signature columns.
- **Background workers do the work.** Background removal and bulk generation run in Celery, never in the request cycle.
- **Active-campaign locking.** Signatures and signature fields referenced by `queued`/`sending` campaigns are immutable, validated server-side.
- **Coordinate behavior preserved.** Signature placement reuses the same `x_start`/`x_end`/`y_coordinate` geometry contract as `DocumentTextField`; existing text rendering is untouched.
- **Theme-aware UI.** All new templates use Bootstrap theme utilities (`bg-body`, `text-body`, `border-secondary`) — no hardcoded light styling.

---

## 3. Background removal — the reliability-first pipeline

The objective is the *most trusted* result for the specific domain of **pen-on-paper signatures**, not generic object segmentation. For this domain a carefully tuned classical computer-vision pipeline is more reliable than a general ML matting model (U²-Net/rembg), which tends to soften thin strokes, drop faint pen marks, and hallucinate edges. The plan therefore makes a **specialized deterministic pipeline the primary engine**, with an **optional ML fallback** for genuinely hard inputs (colored/cluttered backgrounds).

### 3.1 Why classical-first beats ML-first here

- Signatures are high-contrast line art on a roughly uniform light ground — the ideal case for adaptive thresholding.
- We need to preserve **stroke anti-aliasing** (soft edges) to avoid a jagged "cut-out" look, which a soft alpha matte from ink intensity gives us directly.
- Determinism matters for a "trusted" tool: the same photo always yields the same signature. No model drift, no 170 MB download, no GPU.
- Thin/faint strokes that ML matting erases are retained because we threshold on local ink darkness, not learned saliency.

### 3.2 Pipeline stages (primary engine)

Implemented in `documents/signature_processing.py` using **Pillow + NumPy + SciPy + OpenCV (headless)**.

1. **Load & orient.** Decode, apply EXIF orientation, downscale only if a dimension exceeds a safety cap (e.g. 3000 px) to bound memory; keep a high-res copy for the final matte.
2. **Illumination normalization (flat-field).** Estimate the paper background via a large-kernel morphological opening / median blur, then divide the grayscale image by this background estimate. This removes shadows, uneven phone-camera lighting, and paper tint — the single biggest reliability win on real photos.
3. **Ink probability map.** Convert the normalized image to an *ink darkness* map (1 − normalized luminance). This is a continuous 0–1 signal, the basis for a soft alpha matte.
4. **Adaptive binarization for the mask seed.** Run **Sauvola/Niblack local thresholding** (handles local contrast far better than a single global Otsu) to get a binary ink/no-ink seed mask. Otsu is computed too and used as a global sanity bound.
5. **Connected-component denoise.** Label components; drop specks below an area threshold and those whose size/aspect indicates dust or punch-hole/line-rule fragments. Keep the dominant signature stroke cluster(s). Optionally remove long straight ruled lines via a directional morphology pass before component analysis.
6. **Soft alpha compositing.** Final alpha = ink-probability map **masked** by the denoised binary seed (probability inside ink regions, 0 outside). This yields crisp boundaries with preserved anti-aliasing instead of a hard 1-bit cutout.
7. **Color handling.** Two output modes:
   - **Preserve ink color** (default): keep original RGB where alpha > 0 (true blue/black pen color), transparent elsewhere.
   - **Monochrome/recolor**: flatten to a single configurable ink color (useful for consistent branding on certificates).
8. **Auto-crop & pad.** Crop to the alpha bounding box with a small transparent margin so placement math is predictable and the stored asset is tight.
9. **Encode.** Save as optimized transparent **PNG** (lossless, alpha). Store the processed result plus retain the original upload for re-processing if parameters change.

### 3.3 Quality signals & guardrails

- Compute a **confidence score** (e.g. ink coverage ratio, mean alpha contrast, component count). Low confidence flags the signature in the UI as "review recommended" and can auto-suggest the ML fallback.
- Hard guard: if ink coverage is ~0 (blank/over-exposed) or ~100 % (under-exposed/dark photo), fail gracefully with an actionable message rather than storing garbage.
- All parameters (illumination kernel size, Sauvola window, speck area, margin) live in one config dataclass so tuning is centralized and testable.

### 3.4 Optional ML fallback (opt-in, phase 2+)

For colored or busy backgrounds where thresholding underperforms, expose an opt-in path using **`rembg` (U²-Net)** *behind a feature flag*. It is **not** a default dependency: kept out of the base install to avoid the torch/onnx footprint, loaded lazily only when a user explicitly requests "enhanced removal." The classical pipeline remains the default and the source of truth.

### 3.5 New dependencies

Add to `pyproject.toml` (managed via `uv`):

- `numpy` — array math for the matte.
- `scipy` — connected-component labeling, morphology.
- `opencv-python-headless` — Sauvola/adaptive threshold, fast morphology (headless = no GUI/X11 deps, server-safe).
- *(optional, flagged)* `rembg` + `onnxruntime` — only if the ML fallback is enabled.

> If we want to minimize the dependency surface, steps 4–5 can be implemented in NumPy/SciPy alone and OpenCV dropped; OpenCV is recommended for speed and a battle-tested adaptive-threshold implementation. Decide at build time.

---

## 4. Data model

All new persistent models live in the `documents` app.

### 4.1 `Signature` (reusable library asset)

```python
class Signature(SoftDeleteModel):
    user = FK(AUTH_USER_MODEL, related_name='signatures', on_delete=CASCADE)
    name = CharField(max_length=150)                      # user label, unique-ish per user
    original_image = ImageField(upload_to='signatures/originals/')   # raw upload, kept for re-processing
    processed_image = ImageField(upload_to='signatures/processed/', null=True, blank=True)  # transparent PNG
    ink_color_mode = CharField(choices=[('preserve','Preserve'),('mono','Single color')], default='preserve')
    ink_color = CharField(max_length=50, default='#000000')   # used when mode == 'mono'
    processing_status = CharField(choices=[('pending','Pending'),('processing','Processing'),
                                           ('ready','Ready'),('failed','Failed')], default='pending')
    confidence_score = FloatField(null=True, blank=True)
    error_message = TextField(blank=True, default='')
    created_at / updated_at
```

- Mirrors the `SMTPConfig` ownership pattern (user-owned, soft-deleted reusable asset).
- `objects` (all) vs `active` (non-deleted) managers inherited from `SoftDeleteModel`. UI/selectors use `active`; FK integrity uses `objects`.
- Physical-file cleanup signals (`post_delete`, `pre_save`) added exactly like the existing models so replaced/hard-deleted files are removed from disk.

### 4.2 `DocumentSignatureField` (placement on a template)

```python
class DocumentSignatureField(models.Model):
    template = FK(DocumentTemplate, related_name='signature_fields', on_delete=CASCADE)
    label = CharField(max_length=100)                 # UI label, e.g. "Participant Signature"
    source_column = SlugField(max_length=100)         # CSV column resolving the per-recipient signature
    x_start = IntegerField(); x_end = IntegerField()  # horizontal placement box
    y_top = IntegerField()                            # top of the signature box
    max_height = IntegerField(default=120)            # signature scaled to fit box, preserving aspect
    alignment = CharField(choices=ALIGNMENT_CHOICES, default='center')  # within x_start..x_end
```

- Geometry mirrors `DocumentTextField` (same alignment semantics between `x_start`/`x_end`), so the placement UI and preview reuse existing patterns. Signature images scale to fit `max_height` and the box width while preserving aspect ratio.
- Note this is a placement spec, **not** `SoftDeleteModel` — same choice as `DocumentTextField` (cascade with its template; the template itself is soft-deleted).

### 4.3 Per-recipient resolution semantics (CSV-driven)

The user chose **per-recipient signatures from CSV**. The CSV column named by `source_column` holds a **signature reference** resolved against the user's `Signature` library:

- **Primary mode — reference by name:** the cell contains the `name` of a `Signature` the user already uploaded. At generation, `resolve_signature(recipient, field)` looks up `Signature.active.get(user=campaign.user, name=value)`.
- **Bulk-upload helper (phase 3):** a "Signature pack" uploader accepts a ZIP of images; each is processed through the pipeline and a `Signature` row is created keyed by filename stem, so a CSV column of filenames maps cleanly to a large recipient set.
- Missing/blank/unresolved reference → the field renders nothing and the recipient is **not** failed (consistent with how absent text columns render empty today). Optionally flag in a per-recipient warning log.

> Rationale: CSV cannot carry binary, and arbitrary external image URLs are a security risk (SSRF/abuse). Resolving against the user's own library keeps everything reusable, auditable, and offline. URL-based sourcing is intentionally excluded.

---

## 5. Rendering integration

Extend `documents/utils.py` (single source of truth — do **not** fork):

- New helper `_render_signature(base_img, signature_field, signature, ...)`:
  1. Open the signature's `processed_image` (transparent PNG).
  2. Scale to fit (`max_height`, box width) preserving aspect ratio.
  3. Compute `target_x` from `alignment` within `x_start..x_end` (reuse the exact text-alignment math).
  4. Alpha-composite onto the base image at `(target_x, y_top)` using the signature's alpha channel.
- `generate_document_image(template, values, signatures)` gains a `signatures` map (`source_column -> Signature`) and, after drawing all text fields, iterates `template.signature_fields.all()` and composites each resolved signature.
- **Mode safety:** the base template image is promoted to `RGBA` before compositing so alpha is honored; the existing PDF branch already flattens RGBA onto white, so PDF output stays correct.
- `resolve_values` gets a sibling `resolve_signatures(recipient, template, campaign)` that builds the `source_column -> Signature` map. `save_document_for_recipient` calls both.

Existing text-only templates are unaffected: `signature_fields` is simply empty.

---

## 6. Celery tasks

- **`process_signature_task(signature_id)`** — runs the background-removal pipeline, writes `processed_image`, sets `processing_status`, `confidence_score`, `error_message`. Triggered on upload and on re-process. Bounded memory, idempotent, fails gracefully (status `failed` + message).
- **Document generation** — `documents/tasks.py:generate_documents_task` already iterates recipients; extend `save_document_for_recipient` so signatures resolve per recipient inside that existing loop. No new bulk task needed. A guard verifies every referenced `Signature` is `ready` before generation; unresolved/`failed` signatures are skipped per §4.3.

---

## 7. Forms, views, URLs, templates

### Signature library (`documents` app or a small `signatures` submodule)
- `SignatureUploadForm` — name, image, ink-color mode/color. On save → create `Signature` (status `pending`) → enqueue `process_signature_task`.
- Views: `signature_list`, `signature_create` (upload), `signature_detail` (shows original vs processed side by side + confidence + re-process button), `signature_reprocess`, `signature_delete` (soft delete, blocked if locked — see §8).
- List shows processing status with a live/poll refresh until `ready`.

### Template editor
- Add a **signature fields** inline formset (`DocumentSignatureFieldFormSet`) beside the existing text-field formset in `document_form.html`, reusing the add-row JS pattern already present.
- `document_preview` view extended: render placeholder boxes (or a sample signature) at each signature field's coordinates so users verify placement, exactly like sample text today.

### Campaign create
- `DocumentCampaignForm` help text updated: CSV needs `email` + the column(s) named by each signature field's `source_column`. Validation warns if a template signature field's column is absent from the uploaded CSV header.

All templates theme-aware (Bootstrap utilities, light/dark).

---

## 8. Validation & lifecycle invariants

- **Active-campaign locking.** A `Signature` referenced by any `queued`/`sending` campaign cannot be edited, re-processed, or deleted. A `DocumentSignatureField`/`DocumentTemplate` referenced by an active campaign is immutable. Enforced **server-side** (authoritative) and reflected in the UI. Mirrors the existing SMTP/template lock rules.
- **Soft delete everywhere** for `Signature` so completed campaigns keep valid references and historical accuracy is preserved.
- **Graceful failure.** Upload of an unprocessable image → `failed` status + clear message, never a 500. Generation with a missing signature reference → empty render, recipient not failed.
- **Zero-/partial-data protection.** Generation guards already in place for recipients extend naturally; add a pre-flight check that referenced signatures are `ready`.
- **Purge audit.** Add `signatures_purged` to `core.PurgeLog` and include `Signature` in the soft-deleted purge job for parity with other models.

---

## 9. Migrations

1. `documents` migration adding `Signature`, `DocumentSignatureField`.
2. `core` migration adding `PurgeLog.signatures_purged`.
3. No data migration needed (additive). Existing templates gain an empty `signature_fields` relation.

Commands (per `CLAUDE.md`): `uv run python manage.py makemigrations` → `migrate`.

---

## 10. Testing

**Background-removal pipeline (unit):**
- Synthetic fixtures: clean scan, shadowed phone photo, faint pen, colored ink, blank page, near-black photo, ruled-line paper.
- Assert: alpha bounding box is non-trivial, ink coverage within sane bounds, anti-aliasing preserved (soft alpha values present), specks removed, deterministic output (hash stable across runs).
- Guardrails: blank/over-dark inputs return `failed`, not garbage.

**Models / lifecycle:**
- Soft delete hides from `active`, retained in `objects`; locked signature/field cannot be edited or deleted while an active campaign references it (UI + backend).
- File-cleanup signals remove processed/original files on replace and hard-delete.

**Rendering:**
- Signature composited at correct coordinates/alignment, scaled within box, alpha honored; PDF output flattens correctly; text-only templates unchanged (regression).

**Resolution:**
- CSV name → correct `Signature`; missing/unknown reference renders empty and does not fail the recipient.

**State transitions:** `pending → processing → ready/failed` happy and failure paths.

Run: `uv run python manage.py test`.

---

## 11. Security & operational notes

- Validate uploaded image content type and decoded dimensions; cap pixel count to prevent decompression-bomb DoS (Pillow `MAX_IMAGE_PIXELS`).
- No external URL fetching for signatures (avoids SSRF) — library references only.
- Processed/original files are user-scoped; enforce ownership in every view (`Signature.active.get(user=request.user, ...)`).
- Signatures are sensitive personal data — same encryption-at-rest considerations as other media; document the storage location and retention in the soft-delete/purge flow.
- Throttle/queue signature processing so a bulk pack upload cannot starve email/document workers (reuse existing Celery throttling posture).

---

## 12. Phased delivery

- **Phase 1 — Library + pipeline.** `Signature` model, classical removal pipeline, Celery processing, upload/list/detail/re-process/delete UI, soft delete + locking + tests. *Ships standalone value.*
- **Phase 2 — Document signing.** `DocumentSignatureField`, renderer integration, template-editor formset + preview, per-recipient CSV resolution, generation guard, tests.
- **Phase 3 — Scale & polish.** Signature-pack ZIP bulk import, confidence-driven "review" UX, optional `rembg` ML fallback behind a flag, purge-log parity.

---

## 13. Open decisions to confirm before build

1. **OpenCV vs pure NumPy/SciPy** for adaptive thresholding (speed/robustness vs lighter deps) — recommend OpenCV-headless.
2. **Signature-pack ZIP import** in Phase 1 or deferred to Phase 3 (recommend Phase 3).
3. Whether `Signature` lives directly in `documents` or a dedicated `signatures` app (recommend `documents` to keep the rendering engine cohesive and avoid a cross-app dependency; revisit if it grows).

---

## 14. New-bug / tech-debt tracking

Per `CLAUDE.md`, record in `docs/bugs.md` during implementation: any discovered coordinate-rounding inconsistencies between text and signature placement, RGBA/PDF edge cases, and the known encryption discrepancy noted in project memory if it intersects signature storage.
