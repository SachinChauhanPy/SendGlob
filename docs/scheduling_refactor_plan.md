# Scheduling Refactor Plan

Status: Implemented (2026-06-22)
Author: planning pass, 2026-06-22
Scope: replace the current `recurrence` model on `EmailCampaign` with a three-mode
schedule specification, add a pre-run editing window with an email reminder, and
update every dependent layer (tasks, forms, views, templates, admin, tests).

---

## 1. Goals

Replace today's `none / daily / weekly / monthly` recurrence with three explicit
schedule modes, each requiring a time of day and each able to run once or repeat:

1. **Period of time (interval).** First run is exactly N days from creation at the
   chosen time, then every N days while recurring is on.
2. **Day of month.** Runs on one chosen date of the month (1 to 31) at the chosen
   time. Repeats monthly while recurring is on.
3. **Day of week.** Runs on one chosen weekday at the chosen time. Repeats weekly
   while recurring is on.

A single `is_recurring` toggle decides repeat vs one-shot for any of the three.
There is no separate absolute one-off mode (decision: three modes only). All three
require a time. Day selection is single (one weekday, one date), per decision.

### Recurring lifecycle decision

When a recurring campaign finishes a cycle the next instance is armed in advance
carrying its recipients, template and content forward. The owner gets an email
reminder before the next run and may edit recipients, template or content up to one
day before the run. After that freeze point the instance locks and runs as armed.

---

## 2. Current state (what we are changing)

- `EmailCampaign.scheduled_at` (DateTimeField), `recurrence`
  (`none/daily/weekly/monthly`), `next_run_at`, `parent_campaign`.
- `'scheduled'` status, included in `LOCKED_STATUSES = ('scheduled','queued','sending')`,
  so a scheduled campaign is fully locked the moment it is scheduled.
- `emails/tasks.py`:
  - `_next_occurrence(base, recurrence, now)` steps by day/week/month.
  - `_spawn_next_recurrence(campaign)` clones the campaign WITHOUT recipients on
    completion (latent bug: recurring repeats currently send to nobody).
  - `trigger_scheduled_campaigns()` Beat task (every minute) promotes due
    `scheduled` campaigns to `queued` and dispatches the worker.
- Forms in `emails/forms.py` and `documents/forms.py` expose `scheduled_at` +
  `recurrence` with identical `clean()` rules.
- Views set `status='scheduled'` and `next_run_at` on create; `campaign_restart`
  and `campaign_unschedule` clear the schedule fields.
- UI: `templates/emails/campaign_form.html`,
  `templates/documents/document_campaign_form.html`, plus dashboard / list /
  detail render schedule info.
- Environment: `USE_TZ=True`, `TIME_ZONE="UTC"`, no per-user timezone field,
  `EMAIL_BACKEND` defaults to console.

---

## 3. Data model

New fields on `EmailCampaign` (keep `next_run_at`, `parent_campaign`, `'scheduled'`
status):

```
SCHEDULE_NONE     = 'none'
SCHEDULE_INTERVAL = 'interval'    # every N days
SCHEDULE_MONTHLY  = 'monthly'     # one day of month
SCHEDULE_WEEKLY   = 'weekly'      # one day of week
SCHEDULE_CHOICES  = [...]

schedule_type   = CharField(choices=SCHEDULE_CHOICES, default=SCHEDULE_NONE)
schedule_time   = TimeField(null=True, blank=True)        # required when scheduled
is_recurring    = BooleanField(default=False)             # repeat vs one-shot

interval_days   = PositiveIntegerField(null=True, blank=True)   # interval mode
day_of_month    = PositiveSmallIntegerField(null=True, blank=True)  # 1..31, monthly
day_of_week     = PositiveSmallIntegerField(null=True, blank=True)  # 0=Mon..6=Sun, weekly

reminder_sent_at = DateTimeField(null=True, blank=True)   # pre-run notification guard
```

Remove `recurrence` and `scheduled_at` from active use. `next_run_at` becomes the
single computed source of truth for "when does this fire next". Model-level
`clean()` enforces the per-mode field requirements (see validation).

Helper methods on the model:
- `schedule_is_configured` -> bool
- `is_within_edit_window(now)` -> bool (now < next_run_at - EDIT_FREEZE)
- refined `is_locked` (see section 5)

Constants in settings: `SCHEDULE_EDIT_FREEZE_HOURS = 24`,
`SCHEDULE_REMINDER_LEAD_HOURS = 24` (same anchor; reminder fires at the freeze
boundary).

---

## 4. Schedule computation service (Strategy pattern)

New module `emails/scheduling.py`. One small strategy per mode behind a single
entry point. This is pure and deterministic so it can be unit tested in isolation,
which matters because every certificate/email cycle depends on it.

```
def compute_next_run(campaign, *, reference) -> datetime | None
```

- `interval`: first run = `reference.date() + interval_days` at `schedule_time`;
  subsequent = previous run + `interval_days`. Roll forward until strictly in the
  future relative to `now`.
- `monthly`: next calendar month occurrence of `day_of_month` at `schedule_time`.
  Clamp `day_of_month` to the last valid day for short months (e.g. 31 in Feb ->
  28/29). Default decision: clamp, not skip (predictable, never silently skips a
  month). Document this.
- `weekly`: next `day_of_week` at `schedule_time` (today counts only if the time is
  still in the future).
- All times are interpreted in `settings.TIME_ZONE` (UTC today), combined with the
  target date, then stored as an aware UTC datetime in `next_run_at`. See section 12
  on adding a user timezone later.

A matching `describe_schedule(campaign) -> str` produces human text for the UI and
the reminder email ("Every 3 days at 09:00 UTC", "Monthly on the 15th at 09:00").

---

## 5. Locking and the editing window

This changes a documented invariant, so it is called out explicitly.

Today `'scheduled'` is fully locked. The new requirement allows editing a scheduled
(especially recurring next) instance until one day before its run. New rule, applied
backend-first and mirrored in the UI:

- A campaign is **editable** when `status == 'draft'`, or when `status == 'scheduled'`
  and `now < next_run_at - SCHEDULE_EDIT_FREEZE_HOURS`.
- A campaign is **locked** when `status in ('queued','sending')`, or when
  `status == 'scheduled'` and inside the freeze window
  (`now >= next_run_at - SCHEDULE_EDIT_FREEZE_HOURS`).

Implementation: replace direct `status in LOCKED_STATUSES` checks and the
`is_locked` property with a time-aware `is_locked` / `is_editable` pair. Update the
SMTP-config and template protection queries (in `accounts/views.py`,
`documents/views.py`) so a config/template is only locked while a dependent campaign
is actually inside its freeze window or in flight, not merely scheduled far out.
Keep `'scheduled'` out of any "promote race" risk: freeze (24h) is far larger than
the one-minute Beat tick, so a campaign can never be edited and promoted at once.

No new status value is added (per the "do not add states without explicit
requirements" rule); editability is derived from time.

---

## 6. Recurrence spawn and recipient carryover

Rewrite `_spawn_next_recurrence` (rename to `arm_next_cycle`) so the next instance
carries recipients forward (fixing the empty-clone bug):

- Trigger: on completion of a recurring campaign, if `is_recurring` and no child
  already exists (idempotency via `recurrence_children`).
- Clone campaign config AND copy recipient rows fresh (status reset to `pending`,
  new `tracking_uuid`, cleared sent/opened fields). Attachments are copied by
  reference to the same stored files.
- Set `schedule_*` fields identical to the parent, `status='scheduled'`,
  `next_run_at = compute_next_run(...)`, `parent_campaign = root`.
- The new instance is editable until its freeze point, so the owner can adjust the
  carried-over recipients/template/content before it runs.

Non-recurring scheduled campaigns never arm a successor.

---

## 7. Pre-run reminder and freeze (new Beat task)

New task `notify_and_freeze_upcoming(...)`, scheduled in Beat hourly.

For each `scheduled` campaign with `next_run_at` within
`SCHEDULE_REMINDER_LEAD_HOURS` and `reminder_sent_at is null`:
1. Send the owner an email: campaign name, run time, the schedule description, and a
   direct link to edit, noting edits close at the freeze point.
2. Set `reminder_sent_at = now` (idempotency guard so the reminder is sent once).

After `reminder_sent_at` is set the campaign is inside the freeze window and
`is_editable` is already false, so no extra flag is needed. Sender: use the
project `EMAIL_BACKEND` / `DEFAULT_FROM_EMAIL` (a system notification, independent
of the user's sending SMTP). Recipient: `campaign.user.email`.

`trigger_scheduled_campaigns` is unchanged in shape: it still promotes by
`next_run_at <= now`, status compare-and-set, then dispatches email vs document
worker. Only the field it reads changes (`next_run_at` instead of `scheduled_at`).

---

## 8. Forms and validation

Shared logic for `EmailCampaignForm`, `EmailCampaignEditForm`,
`CertificateCampaignForm` (deprecated app, skip), and `DocumentCampaignForm`.
Extract a `ScheduleFormMixin` in a shared location (reuse before duplicate) holding
the fields and `clean()`:

Fields: `schedule_type` (select), `schedule_time` (time input), `is_recurring`
(checkbox), `interval_days` (number), `day_of_month` (number 1-31), `day_of_week`
(select Mon-Sun).

`clean()` rules:
- `schedule_type == none`: ignore the rest; campaign saved as draft / manual.
- otherwise `schedule_time` is required.
- `interval`: `interval_days >= 1` required.
- `monthly`: `day_of_month` in 1..31 required.
- `weekly`: `day_of_week` in 0..6 required.
- Compute the resulting `next_run_at` and reject if it is not in the future.
- Clear the fields not relevant to the chosen mode so stale values are not stored.

---

## 9. Views

- Create views (`emails/views.py`, `documents/views.py`): when `schedule_type !=
  none` and recipients exist, set `status='scheduled'` and
  `next_run_at = compute_next_run(...)`; otherwise save as draft. Drop the old
  `scheduled_at`/`recurrence` writes.
- Edit view: allow schedule edits while `is_editable`; recompute `next_run_at`.
- `campaign_unschedule`: scheduled -> draft, clear `schedule_*`, `next_run_at`,
  `reminder_sent_at`; allowed only while editable or before run.
- `campaign_restart`: clear `schedule_*`, `next_run_at`, `reminder_sent_at` along
  with the existing resets.
- Replace any `status in LOCKED_STATUSES` guards with the time-aware `is_locked`.

---

## 10. UI

- Both campaign forms: a Scheduling card with a mode selector and three
  conditionally shown field groups (interval days / day-of-month / day-of-week),
  the required time input, and the recurring toggle. Progressive show/hide via a
  small theme-aware script; server-side validation remains authoritative.
- Detail / list / dashboard: render `describe_schedule(campaign)`, the next run
  time, recurring badge, and an "edits close at" hint while in the edit window.
- Theme-aware Bootstrap utilities only (no hardcoded light colors), per project UI
  rules.

---

## 11. Admin

Update `EmailCampaign` admin to surface `schedule_type`, `schedule_time`,
`is_recurring`, the per-mode fields, `next_run_at`, and `reminder_sent_at`
(read-only where system-computed), with list filters on `schedule_type` and
`is_recurring`.

---

## 12. Migration and backward compatibility

One data migration:
- Add the new fields.
- Backfill from existing rows: `daily -> interval, interval_days=1`;
  `weekly -> weekly, day_of_week = scheduled_at.weekday()`;
  `monthly -> monthly, day_of_month = scheduled_at.day`;
  set `schedule_time = scheduled_at.time()` and `is_recurring = (recurrence != none)`.
  A `none` row with a future `scheduled_at` (old one-off) maps to `monthly`
  non-recurring on `scheduled_at.day` at `scheduled_at.time()` (closest of the three
  modes), or is left as a manual draft if already past.
- Recompute `next_run_at` for still-`scheduled` rows.
- Remove `recurrence` and `scheduled_at` in a follow-up migration after the backfill
  is verified (two-step to keep historical rows readable during rollout).

Timezone note: time-of-day is interpreted in `settings.TIME_ZONE` (UTC). Because
there is no per-user timezone, all users currently schedule in UTC. Recommended
follow-up (out of scope here, tracked as tech debt): add `CustomUser.timezone` and
interpret `schedule_time` in the owner's zone before converting to UTC.

---

## 13. Edge cases

- Day-of-month 29/30/31 in short months: clamp to last valid day.
- DST: not applicable while everything is UTC; revisit with per-user timezone.
- Missed runs (worker/Beat downtime): `compute_next_run` rolls forward to the next
  strictly-future slot, so a late campaign never fires in the past and recurring
  chains self-heal (matches existing `_next_occurrence` intent).
- Idempotency: reminder guarded by `reminder_sent_at`; cycle arming guarded by the
  `recurrence_children` relation; promotion guarded by compare-and-set on status.
- Zero-recipient recurring instance after carryover edits: existing zero-recipient
  protection still applies at promotion/send.

---

## 14. Testing

Pure computation (highest value, no DB):
- interval first run = +N days; subsequent = +N; roll-forward when late.
- monthly clamp for Feb/30-day months; same-day-future vs next-month.
- weekly today-if-future vs next-week.
- one-shot vs recurring produces a successor or not.

Lifecycle / integration:
- create -> scheduled sets correct `next_run_at` per mode.
- `trigger_scheduled_campaigns` promotes only due rows and dispatches the right
  worker (email vs document).
- recurring completion arms the next cycle WITH recipients; non-recurring does not.
- reminder task sends once and sets `reminder_sent_at`; freeze makes the campaign
  non-editable.
- editability window: editable before freeze, locked after; SMTP/template
  protection follows the same window.
- migration backfill maps each legacy recurrence correctly.

Run with `CELERY_TASK_ALWAYS_EAGER` (already set for tests). Target: full suite
green plus the new cases.

---

## 15. Suggested phasing

1. Model fields + `emails/scheduling.py` + unit tests (no behavior wired yet).
2. Migration with backfill.
3. Rewrite tasks: `compute_next_run` wiring, `arm_next_cycle`, reminder/freeze task,
   `trigger_scheduled_campaigns` field swap; Beat entry for the reminder task.
4. Time-aware locking (`is_locked`/`is_editable`) and dependent protection checks.
5. Forms mixin + validation; views.
6. Templates + admin.
7. Remove legacy `recurrence`/`scheduled_at` (second migration) once verified.

---

## 16. Decisions captured (from requirements)

- Three modes only; no separate absolute one-off.
- Single day selection for weekly and monthly.
- Interval first run is exactly N days from creation at the chosen time.
- Recurring next cycle carries recipients forward, is editable until 24h before the
  run, and the owner is emailed a reminder at that boundary.
- Time of day required for every mode.

## 17. Open items / defaults taken (confirm if needed)

- Edit freeze and reminder lead are both 24h (single boundary). Adjustable via
  settings.
- Day-of-month overflow clamps to the last valid day rather than skipping.
- Reminder emails use the system `EMAIL_BACKEND`/`DEFAULT_FROM_EMAIL`, not the
  user's sending SMTP.
- Per-user timezone is deferred; everything is UTC for now (tracked as tech debt).
