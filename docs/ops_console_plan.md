# SendGlob - Operations & Admin Console Plan

**Status:** Proposed
**Date:** 2026-06-30
**Build order:** This console is built **before** the billing module. Billing
(`docs/billing_accounting_profile_plan.md`) builds on the credit primitives this
console introduces.

**Purpose:** A staff-only operations console that lets the operator monitor every
transaction and activity in the system, see rule-based insights for scaling the
product and servers, and take full, audited control of the live system, including
granting free credits to any user.

---

## 1. Goals and non-goals

Goals:

- Observe system health and every meaningful activity in near real time.
- Surface scaling signals and threshold alerts so capacity decisions are made
  before reliability suffers.
- **Tell the operator when and by how much to upgrade the AWS EC2 host**: which
  resource is the bottleneck (CPU, RAM, or disk), which lever to pull (resize the
  instance vs grow the EBS volume vs move a service off the box), and a concrete
  recommended target.
- Give the operator full control: grant/adjust credits, manage users, control any
  campaign, and a global send kill switch.
- Record every operator action in an immutable audit log.

Non-goals (this phase):

- AI-generated insight summaries and statistical anomaly detection (designed for
  later; metrics + threshold alerts ship first).
- External observability stacks (Sentry/Prometheus/Flower). We read what we
  already store (`django_celery_results`, app models) and expose it ourselves.
- End-user billing UI (separate plan, built after this).

---

## 2. Approach (decided)

- **Custom staff console + keep Django admin.** A new staff-only app provides
  dashboards, insights, and control actions. Django admin is kept for raw
  per-record inspection and CRUD. Clear separation: console = operate, admin =
  inspect.
- **Insights: metrics + threshold alerts** first, structured so an AI summary
  layer can be added later without rework.
- **Control: full operator control**, every action permission-gated and
  audit-logged.

---

## 3. App layout

New Django app `opsconsole/`, mounted at `/ops/` and restricted to staff.

```
opsconsole/
├── apps.py
├── models.py            # AdminAuditLog, MetricSnapshot, OperationalFlag, AlertEvent
├── access.py            # staff_required decorator + mixin, permission checks
├── metrics.py           # metric collectors (queue, tasks, storage, db, usage)
├── insights.py          # threshold rules + simple growth projections
├── controls.py          # audited control actions (grant credits, suspend, kill switch...)
├── activity.py          # unified activity feed aggregation
├── tasks.py             # Beat task: periodic MetricSnapshot collection + alert eval
├── views.py             # dashboard, health, activity, users, campaigns, controls, audit
├── urls.py
├── templates/opsconsole/
└── tests*.py
```

Credit primitives pulled forward into a new `billing/` app now (only these two
models plus the grant action), because granting credits requires them:

- `CreditWallet` (balance per user)
- `CreditTransaction` (append-only ledger)

The rest of billing (packs, plans, Razorpay, receipts) follows in the billing
plan. This avoids building credit logic twice.

---

## 4. Access control and safety

- `/ops/` requires `is_staff`; destructive actions additionally require
  `is_superuser` or membership in an `ops_operators` group. Backend-authoritative,
  never UI-only.
- Every control action: requires a typed **reason**, writes an `AdminAuditLog`
  row, and for destructive actions shows a double-confirm.
- **Impersonation ("view as user")** is read-mostly, time-boxed, visibly bannered,
  and logged on start and stop. It must never silently perform writes as the user.
- The console is isolated from the end-user app routes and from public media.
- Rate-limit the control endpoints; protect with CSRF (existing middleware).

---

## 5. Data sources (mostly already present)

| Signal | Source |
|---|---|
| Task success/failure/latency | `django_celery_results` TaskResult (already enabled, `django-db` backend) |
| Queue depth / worker liveness | Celery `inspect` (active/reserved/stats) + Redis ping |
| Beat / scheduling health | scheduling models + last-run timestamps; stale-send recovery state |
| Email activity | `EmailCampaign`, `EmailRecipient` statuses (sent/failed/pending) |
| Document activity | document generation tasks + generated-document records |
| Signups / logins / active users | `CustomUser`, auth signals |
| Data lifecycle | `PurgeLog` (existing audit), soft-deleted counts |
| Storage / DB growth | filesystem usage of media subtrees; `db.sqlite3` size |
| **Host CPU / RAM / disk / load / swap** | `psutil` reading the live EC2 host (the collector runs on the box) |
| **Current EC2 instance type & region** | EC2 Instance Metadata Service (IMDS, `169.254.169.254`), cached; manual override in settings |
| **t-class CPU credit balance** (if burstable) | optional CloudWatch `CPUCreditBalance` via boto3 |
| Credits | `CreditWallet`, `CreditTransaction` (new) |
| Payments/revenue | billing models (later phase) |

Admin/monitoring queries use the **unfiltered `objects` manager** deliberately, so
the operator can see soft-deleted records too. This is the sanctioned use of
`objects` per the project's manager rules.

---

## 6. Console modules

### 6.1 Operations dashboard (landing)

At-a-glance health: worker up/down, queue depth, task failure rate (last hour /
24h), emails sent vs failed today, documents generated today, signups today,
active alerts, and the global send switch state. Each tile links to its detail
view.

### 6.2 System health

Celery worker status and heartbeat, queue depth trend, task throughput and latency
by task name, failure breakdown with recent tracebacks (from TaskResult), Redis
connectivity, Beat schedule freshness, and stale/stuck campaign detection (already
recovered by a task; surface it here too).

### 6.3 Activity monitor

A unified, filterable feed of system activity: campaign state changes, send
batches, document generations, user signups/logins, credit grants and debits, and
(later) payments. Filter by user, type, time. This is the "monitor each
transaction and each activity" surface. Backed by existing per-recipient and
per-task records plus the credit ledger and audit log.

### 6.4 EC2 capacity planning and scaling insights

This is the primary reason for the console. SendGlob runs on a single AWS EC2
instance with an attached EBS volume, so the console must answer three questions
for each resource: **is it a problem now, when will it become one, and what
exactly should I change.**

The console knows the **current instance type** (auto-detected via IMDS, e.g.
`t3.small` = 2 vCPU / 2 GB, overridable in settings) and the **EBS volume size**,
so every recommendation is concrete, not abstract.

**CPU** (host CPU %, load average, and on burstable t-class the CPU credit
balance). Pillow document rendering and email batches are the main consumers.
- Sustained high CPU -> the instance is undersized. Recommend the next compute
  step (e.g. `t3.small` -> `t3.medium`, or move to a compute-optimized `c`-class
  if CPU-bound while RAM is fine).
- On t2/t3, a **draining CPU credit balance** is the real culprit even when raw
  CPU % looks moderate; the console flags this distinctly and recommends either a
  larger burstable size, enabling unlimited mode, or a non-burstable class.

**RAM** (used / available, plus swap activity). Concurrent Celery workers and
large image compositing drive memory.
- Sustained high RAM use or any swapping -> recommend the next memory step
  (e.g. `t3.small` 2 GB -> `t3.medium` 4 GB; if RAM-bound but CPU is idle,
  recommend a memory-optimized `r`-class). Swap-in activity is treated as a
  near-critical signal because it degrades latency badly.

**Disk / storage** (EBS volume usage, broken down by `db.sqlite3`, media
subtrees, and logs).
- Disk is the one resource you grow **without changing the instance**: the console
  recommends a specific new EBS size (online resize + filesystem extend, no
  downtime) rather than an instance change.
- It also separates causes: media growth -> tune retention/purge; DB growth ->
  the SQLite ceiling signal (plan the Postgres move, eventually onto its own
  instance or RDS).

**Sizing recommendation logic.** A small reference table of common instance types
(vCPU, RAM, family) lets the console translate "85% RAM sustained on t3.small"
into "recommended: t3.medium (4 GB)". Rules distinguish the lever:
- CPU/RAM pressure -> **resize the instance** (stop, change type, start; brief
  downtime) and name the target type.
- Disk pressure -> **grow the EBS volume** (online) and name the target GB.
- When a single resource is pushed by one service (Redis memory, Celery CPU, DB
  size), recommend **moving that service off the box** (separate worker instance,
  ElastiCache, or RDS) instead of endlessly growing one instance.

**When and how much.** Each metric has a configurable warn/critical threshold and
a **linear growth projection** that converts trend into lead time: "disk crosses
85% in about 3 weeks at current growth; grow the EBS volume from 30 GB to 50 GB"
or "RAM has been above 80% for 6 hours; resize t3.small -> t3.medium." Alerts are
written to `AlertEvent`, shown in the console, and emailed to the operator on new
critical events.

**Other app-level scaling signals** (same alerting mechanism):
- Queue depth / task latency rising -> add a worker (and at higher scale, move
  workers to a separate instance).
- Per-user email volume near Gmail caps (about 100/day via SMTP) -> product
  signal that a user is hitting the ceiling.
- Redis memory and error-rate spikes.

This layer is intentionally rule-based and EC2-specific; an AI summary can later
read the same snapshots without changing the data model.

### 6.5 User management

Search users; view profile, usage, wallet balance, ledger, and campaigns. Actions:
**grant or adjust credits** (reason required, writes a `CreditTransaction` and an
audit row), suspend/reactivate, force-logout, and start an impersonation session.

### 6.6 Campaign control

Find any campaign (including soft-deleted via `objects`); inspect recipients and
delivery stats; pause/resume/cancel; requeue a stuck campaign. All actions respect
the campaign lifecycle invariants and are routed through the existing state
transitions, not raw field edits.

### 6.7 Global controls

Runtime operational flags stored in `OperationalFlag` and honored by the send and
generation pipelines:

- **Global send kill switch** (pause all outbound delivery instantly).
- **Maintenance mode** (block non-staff writes with a friendly page).
- **Global throttle override** (raise/lower the inter-send delay during incidents).

Because these are checked by the workers, they take effect without a redeploy.

### 6.8 Audit log

Read-only view of `AdminAuditLog`: who did what, to whom, when, from where, with
the reason and before/after snapshot. Append-only, never editable.

---

## 7. New models

All four are append-only or operational, **not** `SoftDeleteModel`, and the
audit/alert tables are excluded from the purge.

```python
class AdminAuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                              related_name='ops_actions')
    action = models.CharField(max_length=50)        # grant_credits, suspend_user, kill_switch_on...
    target_type = models.CharField(max_length=50, blank=True)
    target_id = models.CharField(max_length=50, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    detail = models.JSONField(default=dict, blank=True)   # before/after snapshot
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class MetricSnapshot(models.Model):
    metric = models.CharField(max_length=60, db_index=True)   # queue_depth, db_bytes...
    value = models.FloatField()
    captured_at = models.DateTimeField(auto_now_add=True, db_index=True)

class OperationalFlag(models.Model):
    key = models.CharField(max_length=50, unique=True)        # global_send_paused...
    enabled = models.BooleanField(default=False)
    value = models.CharField(max_length=100, blank=True)      # optional numeric/string payload
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

class AlertEvent(models.Model):
    metric = models.CharField(max_length=60)
    level = models.CharField(max_length=10)   # warn / critical
    message = models.CharField(max_length=255)
    value = models.FloatField()
    threshold = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
```

`MetricSnapshot` can be downsampled/pruned on a long retention (it is operational
telemetry, not financial history); `AdminAuditLog` and `AlertEvent` are kept.

---

## 8. Metric collection

A Celery Beat task (`opsconsole.tasks.collect_metrics`, every 5 minutes) writes
`MetricSnapshot` rows and evaluates thresholds, creating `AlertEvent`s and sending
operator email on new critical alerts. Live tiles read the latest snapshot plus a
cheap real-time probe (Celery `inspect`, Redis ping). This keeps dashboard loads
fast and avoids heavy aggregation on every page view.

Host metrics come from **`psutil`** (a new dependency), read on the EC2 box where
the collector runs: `cpu_percent`, `virtual_memory`, `swap_memory`,
`disk_usage` per mount, and `getloadavg`. The **current instance type / region**
is read once from IMDS (`http://169.254.169.254/latest/meta-data/`) and cached,
with a settings override for local/dev. **CPU credit balance** for burstable
instances is optional and read via **CloudWatch (boto3)** only if AWS credentials
and the metric are available; absence degrades gracefully (the rest still works).
No external agent is required; everything runs in-process on the instance.

The instance-type reference table (vCPU/RAM/family per type) and the warn/critical
thresholds live in settings so they can be tuned without code changes.

---

## 9. Integration points in existing code

- **Send pipeline** (`emails/tasks.py`): check `OperationalFlag` kill switch and
  throttle override before each batch; respect maintenance mode.
- **Generation pipeline** (`documents/tasks.py`): same kill switch / maintenance
  checks; this is also where billing later hooks credit debits.
- **Auth signals**: feed login/signup activity and create a `CreditWallet` on user
  creation (shared with billing).
- **Settings**: thresholds, snapshot interval, infra cost floor (used later by
  billing's revenue report), operator alert email.

---

## 10. UI

Staff console templates on Bootstrap 5, theme-aware (`bg-body`, `text-body`,
`border-secondary`), visually distinct from the end-user app so context is never
ambiguous. Charts via a lightweight chart library already permitted in the stack.
A persistent header shows worker status and the kill-switch state on every console
page.

---

## 11. Phased execution

**Phase A - Foundation and visibility.**
App scaffold, staff access control, `AdminAuditLog`, ops dashboard shell, system
health (from TaskResult + live counts), and the activity monitor. Read-only.

**Phase B - Insights (the core goal).**
`MetricSnapshot` + Beat collector including **`psutil` host CPU/RAM/disk** and
**IMDS instance-type detection**, trend charts, threshold rules, `AlertEvent`,
growth projections, and the **EC2 sizing recommendations** (resize instance vs
grow EBS vs move a service, with concrete targets). Optional CloudWatch CPU-credit
signal. Operator alert email.

**Phase C - Control.**
`OperationalFlag` kill switch / maintenance / throttle wired into the pipelines;
`CreditWallet` + `CreditTransaction` + **grant credits**; user suspend/reactivate;
campaign pause/resume/cancel/requeue; impersonation. All audited.

**Phase D - Handoff to billing.**
With wallet + ledger + grant in place, proceed to the billing plan (packs, plans,
Razorpay, receipts, revenue report), which reuses these foundations.

---

## 12. Testing

- **Access:** non-staff blocked from `/ops/`; destructive actions blocked for
  non-superusers.
- **Audit:** every control action writes exactly one `AdminAuditLog` row with the
  reason.
- **Kill switch:** when enabled, the send task stops dispatching; when disabled,
  it resumes. Maintenance mode blocks non-staff writes.
- **Grant credits:** updates balance via a single ledger transaction; concurrent
  grants are consistent.
- **Metrics:** collector writes snapshots and is safe to run repeatedly; threshold
  crossing creates one alert (no duplicate spam until acknowledged/reset).
- **Campaign control:** actions go through valid state transitions and are blocked
  when invalid, with clear messages.
- **Impersonation:** start/stop logged; writes as the impersonated user are
  prevented.

---

## 13. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Operator mistake via powerful controls | High | Reason required, double-confirm, full audit, superuser gate on destructive actions |
| Impersonation abuse / privacy | High | Read-mostly, time-boxed, bannered, logged start/stop |
| Kill switch not honored by a code path | High | Single checkpoint in each pipeline; integration test |
| SQLite contention from admin + app load | Medium | Read from snapshots not live aggregation; DB-size alert drives Postgres migration timing |
| Celery inspect unreliable/slow | Medium | Cache probe results briefly; degrade gracefully if a worker is unreachable |
| Metric table growth | Low | Downsample/prune old `MetricSnapshot` on long retention |
| IMDS/CloudWatch unavailable (dev, IMDSv2, no IAM) | Low | Settings override for instance type; CloudWatch optional; host metrics via psutil still work |
| Sizing recommendation wrong/over-aggressive | Medium | Recommendations are advisory only (never auto-resize); require sustained breach over a window, not a single spike |

---

## 14. Open questions

- Exact warn/critical thresholds per metric (start conservative, tune from real
  data).
- Operator alert channel beyond email (later: chat webhook).
- When DB-size/queue trends justify the Postgres move (the alert exists to force
  this decision deliberately).
