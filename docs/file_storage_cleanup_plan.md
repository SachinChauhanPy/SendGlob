# Physical File Storage Cleanup: Audit and Implementation Plan

Status: proposed
Discovery date: 2026-06-22
Affected areas: certificates, documents, emails, core

## Goal

Guarantee that every file written under `MEDIA_ROOT` stays consistent with the
database: a file exists if and only if a live (or intentionally retained)
database record references it. No orphans on disk, no records pointing at
missing files.

## How cleanup works today

Each of three apps defines an identical pair of signal handlers at the bottom of
its `models.py` and wires them per model:

* `cleanup_files_pre_save`: on update, loads the old row and deletes any file
  field whose stored name changed.
* `cleanup_files_post_delete`: on physical delete, deletes every file field.

These are connected in `certificates/models.py`, `documents/models.py`, and
`emails/models.py`.

The critical interaction with soft delete: most file-bearing models inherit
`SoftDeleteModel`, whose `delete()` only sets `deleted_at`. That does NOT fire
`post_delete`, so a user-facing "delete" intentionally retains the file (correct
for historical accuracy). Physical removal happens only when `hard_delete()`
runs, which currently means the weekly `purge_soft_deleted_records` task in
`core/tasks.py`.

## Coverage matrix

| Model | App | File fields | Soft delete | post_delete signal | pre_save signal | Reached by purge |
|---|---|---|---|---|---|---|
| CertificateTemplate | certificates (deprecated) | image, font_file | yes | yes | yes | NO |
| DocumentTemplate | documents | template_file, global_font_file | yes | yes | yes | yes |
| DocumentTextField | documents | font_file | no | yes | yes | yes (cascade) |
| Signature | documents | original_image, processed_image | yes | yes | yes | NO |
| CampaignAttachment | emails | file | no | yes | yes | yes (cascade) |
| Recipient | emails | document_file | no | yes | yes | yes (cascade) |

`accounts` has no file fields. `DocumentSignatureField` has no file field.

## Findings

### Gap 1: Soft-deleted Signature and CertificateTemplate are never purged (storage leak)

`purge_soft_deleted_records` only processes `EmailCampaign`, `EmailTemplate`,
`DocumentTemplate`, and `SMTPConfig`. `Signature` and `CertificateTemplate` are
both `SoftDeleteModel` with file fields, so when a user deletes one the record is
soft-deleted (files retained, by design) but nothing ever hard-deletes it later.
The records and their files accumulate in the database and on disk forever. This
is the clearest case of "some areas still not fixed": the delete path looks fine
but the files are never actually reclaimed.

Note: `Signature` is resolved by name from CSV at generation time and has no
foreign key from `DocumentSignatureField`, so it has no campaign reference to
protect. `CertificateTemplate` belongs to the deprecated `certificates` app and
has no live references either.

### Gap 2: Cleanup logic is duplicated in three apps (single-source-of-truth violation)

The same two functions are copy-pasted in `certificates/models.py`,
`documents/models.py`, and `emails/models.py`. CLAUDE.md mandates a single
source of truth. Any correctness fix (Gap 3) or behavior change must currently
be edited in three places, which is exactly how apps drift out of sync.

### Gap 3: File deletions are not transaction-safe

Both signals delete the file from disk immediately, inside the surrounding
database transaction. If that transaction later rolls back:

* on delete: the row is restored but the file is already gone, leaving a record
  pointing at a missing file (a historical-accuracy violation);
* on update: the old file is gone but the new name was never committed.

Deletions should be deferred with `transaction.on_commit` so the file is removed
only after the database change is durable.

### Gap 4: pre_save cost and bulk-update blind spot

`cleanup_files_pre_save` issues an extra `SELECT` on every save of these models,
including saves that touch no file field. `Recipient` is saved repeatedly during
sending and status updates, so this is real per-send overhead. Separately, any
`QuerySet.update()` bypasses `pre_save` entirely, so a future bulk file
replacement would silently orphan. No such bulk update exists today, but the
rule should be documented and the signal should early-exit when no file field is
in `update_fields`.

### Gap 5: No way to detect files already orphaned on disk

Nothing reconciles `MEDIA_ROOT` against the database. Files orphaned before the
signals existed, or by any future bypass (bulk update, a crash between writing a
file and a rollback), are invisible. A scan-and-report (and optional delete)
management command is needed to actually verify "all files on physical storage
are maintained correctly."

### Gap 6: Redundant manual cleanup in views (dead/duplicate logic)

Several views call `file.delete(save=False)` right before deleting the record
(for example `r.document_file.delete(...)` in `documents/views.py` and
`emails/views.py`, and `attachment.file.delete(...)`). The post_delete signal
already covers physical deletes. Once the centralized helper is the single source
of truth, these manual calls are duplicate logic and should be removed for
clarity (verifying each surrounding delete is a physical delete, not a soft
delete, before removing).

## Implementation plan

### Phase 1: Centralize the cleanup logic (no behavior change)

1. Create `core/file_cleanup.py` with one `register_file_cleanup(*models)` helper
   that connects transaction-safe `pre_save`/`post_delete` handlers (folds in
   Gap 3 and the Gap 4 early-exit).
2. Replace the three duplicated blocks in `certificates/models.py`,
   `documents/models.py`, and `emails/models.py` with a single
   `register_file_cleanup(...)` call each. Remove the old functions and now-unused
   imports.
3. Run the suite to confirm no regression.

### Phase 2: Make deletions transaction-safe (Gap 3)

1. In the centralized handlers, wrap each `file.delete(save=False)` in
   `transaction.on_commit(...)` so files are removed only after commit.
2. Add regression tests: a rolled-back delete keeps the file; a rolled-back file
   replacement keeps the old file; committed paths still remove files.

### Phase 3: Close the purge coverage gap (Gap 1)

1. Extend `purge_soft_deleted_records` to hard-delete soft-deleted `Signature`
   and `CertificateTemplate` rows older than the retention window, reusing the
   existing pattern. No reference check is needed for these two (no protecting
   FK), but keep the `ProtectedError` guard for safety.
2. Add `signatures_purged` and `certificate_templates_purged` counters to
   `PurgeLog` (new migration) and the returned summary.
3. Tests: soft-deleted signature/certificate past retention is purged and its
   files are removed; one inside retention is kept.

### Phase 4: Orphan reconciliation command (Gap 5)

1. Add `core/management/commands/find_orphan_media.py` that walks `MEDIA_ROOT`,
   builds the set of referenced file names from every file field across all
   models (using the default `objects` manager so soft-deleted-but-retained files
   still count as referenced), and reports files on disk with no referencing row.
2. Support `--delete` (default dry-run) and `--older-than DAYS` to avoid removing
   files mid-write. Log a summary; optionally write a `PurgeLog`-style note.
3. Tests with a temporary `MEDIA_ROOT`: a planted orphan is reported, a
   referenced file is not, dry-run deletes nothing.

### Phase 5: Remove redundant manual cleanup (Gap 6)

1. After Phases 1 to 2 are verified, remove the manual `file.delete(save=False)`
   calls in views where the following delete is physical, leaving the signal as
   the single source of truth. Where the surrounding delete is a soft delete,
   leave behavior intact (the file should be retained).
2. Re-run the suite.

### Phase 6: Documentation

Record the resolved items in `docs/bugs.md` and note the centralized helper plus
the orphan command in `docs/architecture.md`.

## Risks

* Certificate rendering and signature generation are coordinate and pipeline
  sensitive; Phase 1 must not change when files are written, only when they are
  removed.
* The orphan command must use the unfiltered `objects` manager so it does not
  treat intentionally retained soft-deleted files as orphans.
* `transaction.on_commit` does not run when autocommit is off without an active
  transaction (for example some management commands); confirm purge and the
  orphan command still remove files in those contexts.

## Suggested sequencing

Phases 1 and 3 deliver the most value first (single source of truth plus the
storage leak fix). Phase 4 is the verification tool that proves the whole system
is consistent. Phases 2, 5, and 6 harden and tidy.
