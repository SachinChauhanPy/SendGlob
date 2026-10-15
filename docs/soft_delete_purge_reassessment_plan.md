# Soft Delete and Purge Reassessment: Implementation Plan

Status: done - Phases 1, 3, and 5 complete (2026-06-23), full suite 123/123 green; Phases 2 and 4 deferred as tracked debt (B-045, B-046)
Discovery date: 2026-06-23
Affected areas: core, emails, documents

## Context

A reassessment of the soft-delete and purge mechanism after the recent
campaign-lifecycle, scheduling, documents, and file-cleanup changes. The core of
the system is sound: centralized transaction-safe file cleanup
(`core/file_cleanup.py`), the orphan-reconciliation command
(`find_orphan_media`), the dependency-ordered and reference-safe purge, the
`PurgeLog` audit trail, and the scheduler's `deleted_at__isnull=True`
compare-and-set are all correct and consistent.

This plan addresses four findings the reassessment surfaced, in priority order.
Only Finding 1 is a live bug; the rest are hardening and prevention.

## Findings recap

### Finding 1 (Medium, live): soft-deleted draft campaigns are never purged

`core/tasks.purge_soft_deleted_records` only hard-deletes campaigns whose status
is in `PURGEABLE_CAMPAIGN_STATUSES = {completed, failed, cancelled}`. But
`campaign_delete` permits deleting a `draft` campaign, which keeps
`status='draft'` and only sets `deleted_at`. The purge task never touches it, so:

* its `Recipient` rows, `CampaignAttachment` files, and generated `document_file`s
  are never reclaimed (database and storage leak);
* the soft-deleted draft still counts in `_purge_unreferenced`'s
  `obj.campaigns.exists()` check (unfiltered `objects`), so the `EmailTemplate`,
  `DocumentTemplate`, and `SMTPConfig` it referenced can never become
  unreferenced and never purge either, even after the user soft-deleted them.

Deleting a draft is the most common delete path, so this leaks routinely.

Note the related asymmetry this resolves: edit-locking asks "is this template
referenced by a live campaign?" via `locked_filter()` and correctly ignores
soft-deleted campaigns (`test_soft_deleted_campaigns_do_not_lock_resources`),
while purge asks the same question via `campaigns.exists()` and counts them. Once
abandoned drafts are purged first, the template frees up on the same run and the
two views of "referenced" reconverge.

### Finding 2 (Low, latent): `restore()` can violate a partial unique constraint

`Signature` has `UniqueConstraint(fields=['user','name'], condition=deleted_at
IS NULL)`. `SoftDeleteModel.restore()` does a blind `deleted_at=None` save with
no guard, so restoring a signature whose name was reused by a new active record
raises `IntegrityError`. `restore()` currently has no Python call sites, so this
is a landmine for a future "restore" feature rather than a live bug.

### Finding 3 (Informational): unfiltered `objects` default manager is a recurring footgun

`objects` (unfiltered) is the default manager and `active` is the filtered one.
Every user-facing query must remember `.active`; forgetting it produced B-031 and
B-033. The design is defensible (keeps FK and admin lookups intact) but the
burden recurs. A guardrail is cheaper than flipping the default, which would risk
admin and FK behavior.

### Finding 4 (Informational): purge does O(N) per-row deletes plus an N+1 reference check

`for obj in ...: obj.hard_delete()` plus a per-row `campaigns.exists()`. This is
required to fire cascade and file-cleanup signals and to catch `ProtectedError`,
and it is a weekly retention-bounded job, so it is acceptable now. The volume
ceiling should be documented so it is revisited if the soft-deleted backlog grows.

## Implementation plan

### Phase 1: Close the campaign purge gap (Finding 1)

Make `LOCKED_STATUSES` the single source of truth for purge eligibility instead
of a separate terminal-status allowlist. A soft-deleted campaign is safe to
physically remove unless it is in-flight or scheduled.

1. In `core/tasks.py`, replace the `PURGEABLE_CAMPAIGN_STATUSES` allowlist with an
   exclusion of `EmailCampaign.LOCKED_STATUSES`:

   ```python
   eligible = (
       EmailCampaign.objects
       .filter(deleted_at__lt=cutoff)
       .exclude(status__in=EmailCampaign.LOCKED_STATUSES)
   )
   ```

   Remove the now-dead `PURGEABLE_CAMPAIGN_STATUSES` constant and update the
   module docstring to state the rule as "purge any soft-deleted campaign that is
   not locked (queued/sending/scheduled)."

2. Confirm ordering still holds: campaigns purge in step 1, which frees the
   templates and SMTP configs checked in step 2, so a soft-deleted draft no
   longer pins them.

Rationale: `draft`, `completed`, `failed`, and `cancelled` are all safe to
reclaim once retention elapses. `queued` and `sending` must never be destroyed
(in-flight). `scheduled` stays excluded because Celery Beat can promote it; a
soft-deleted scheduled campaign will not fire (the scheduler filters
`deleted_at__isnull=True`) but is left for a later, explicit decision rather than
purged here, preserving the existing
`test_active_state_campaign_is_never_purged` guarantee.

Tests (add to `core/tests.py` `PurgeSoftDeletedRecordsTests`):

* `test_soft_deleted_draft_campaign_is_purged`: a draft soft-deleted past
  retention is hard-deleted with its recipients.
* `test_draft_purge_frees_referenced_template_and_smtp`: a soft-deleted draft
  referencing a soft-deleted template and SMTP config lets both purge on the same
  run (regression for the pinning behavior).
* Existing `test_active_state_campaign_is_never_purged` (queued/sending/scheduled)
  must stay green.

Residual, documented: a soft-deleted `scheduled` campaign past retention is still
not purged (storage-only, scheduler skips it). Track as a low-priority follow-up.

### Phase 2: Harden `restore()` before it is wired up (Finding 2)

1. Override `restore()` on `Signature` (`documents/models.py`) to detect an active
   sibling with the same `(user, name)` and raise a clear `ValidationError`
   instead of letting the database raise `IntegrityError`. Keep the generic
   `SoftDeleteModel.restore()` for models without the constraint.
2. Defer until a restore UI is actually planned, or, per the no-dead-code
   standard, decide explicitly to keep `restore()` as part of the public
   soft-delete API and cover it with the guard test below.

Tests (only if Phase 2 is implemented):

* restoring a signature whose name is taken by an active sibling raises a
  validation error;
* restoring when the name is free succeeds.

### Phase 3: Manager-default guardrail (Finding 3)

1. Add regression tests asserting user-facing entry points resolve via `.active`:
   requesting a soft-deleted campaign's detail, queue, cancel, and restart URLs
   returns 404 (locks in the B-031 fix), and the same for SMTP and template
   protection paths.
2. Document the rule in `CLAUDE.md` (Manager Usage section): user-facing queries
   use `active`; `objects` is unfiltered and exists only for FK integrity,
   historical reporting, admin, and the purge task.

### Phase 4: Document the purge scale ceiling (Finding 4)

1. Add a short note in `core/tasks.py` (and `docs/`) that the purge is O(N)
   per-row with an N+1 reference check by design (signal cascade plus
   `ProtectedError` handling), acceptable for a weekly retention-bounded job, and
   the point at which batching or chunking should be reconsidered.
2. No code change unless a backlog problem is observed.

### Phase 5: Verification and bug tracking (done)

1. Ran the full suite: `uv run python manage.py test` - 123/123 green
   (2026-06-23), including the three new tests.
2. Recorded the findings in `docs/bugs.md`: B-044 (Finding 1, Medium, Closed),
   B-045 (Finding 2, Low, Open debt), B-046 (Finding 4, Low, Open debt). Phase 3
   guardrail and the CLAUDE.md Manager Usage note are referenced under B-044.
3. Updated this plan's status to done.

## Risks

* The purge eligibility change widens what gets hard-deleted (drafts now qualify).
  The retention window and the `LOCKED_STATUSES` exclusion are the only guards, so
  the new tests must prove queued/sending/scheduled are still never purged.
* `restore()` hardening must not change behavior for models without the partial
  unique constraint.
* Documentation-only phases (3 doc step, 4) carry no runtime risk.

## Suggested sequencing

Phase 1 first; it is the only live leak and its tests also prove the
in-flight-protection guarantee still holds. Phase 3 is cheap prevention and worth
bundling. Phases 2 and 4 are low-priority hardening that can follow.
