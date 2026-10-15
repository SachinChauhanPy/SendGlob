# SendGlob - Claude Development Guide

## Project Philosophy

SendGlob is a production application focused on bulk email delivery, certificate automation, campaign management, and historical reporting.

The primary engineering goals are:

1. Preserve data integrity.
2. Preserve historical accuracy.
3. Maintain campaign lifecycle consistency.
4. Build maintainable and scalable solutions.
5. Keep the codebase clean and easy to understand.

When multiple implementation options exist, prefer the solution that improves maintainability, readability, and long-term stability.

---

# Technology Stack

Backend:

* Python 3.13
* Django 6.x
* SQLite

Background Processing:

* Celery
* Redis

Authentication:

* django-allauth

Email System:

* SMTP-based delivery

Certificate Generation:

* Pillow (PIL)

Frontend:

* Django Templates
* Bootstrap 5

---

# Directory Responsibilities

## core/

Contains:

* Django settings
* URL configuration
* WSGI entrypoint
* Celery initialization
* shared utilities
* abstract models

Core infrastructure belongs here.

---

## accounts/

Responsible for:

* authentication
* user management
* SMTP configurations

---

## emails/

Responsible for:

* email templates
* campaigns
* recipients
* attachments
* email rendering
* email delivery tasks

---

## certificates/

Responsible for:

* certificate templates
* certificate generation
* certificate processing tasks

---

## templates/

Contains:

* Django templates
* reusable UI components

---

## static/

Contains:

* CSS
* JavaScript
* frontend assets

---

# Critical Architectural Invariants

## Soft Delete Is Mandatory

Important business entities use soft deletion.

Historical reports and relationships must remain valid even after records are removed from active use.

Never introduce behavior that breaks historical references.

Prefer soft deletion over physical deletion whenever possible.

---

## Campaign State Is The Source Of Truth

Campaign workflow is state-driven.

Never bypass campaign restrictions.

Always validate state transitions.

Assume background workers may be operating on active campaigns.

---

## Historical Accuracy Has Priority

Completed campaigns represent historical records.

Protect:

* recipient history
* delivery statistics
* attachments
* templates
* campaign relationships

Historical accuracy is more important than edit flexibility.

---

# Manager Usage

Models implementing soft deletion expose:

```python
objects
```

Returns all records.

Used for:

* historical reporting
* foreign-key integrity
* administrative access

Use:

```python
active
```

for:

* dashboards
* forms
* listings
* selectors
* user-facing queries

Avoid manually duplicating active filtering logic.

`objects` is the unfiltered default manager and exists only for foreign-key
integrity, historical reporting, admin, and the purge task. Any user-facing entry
point (views, `get_object_or_404`, selectors) must query `active`; resolving a
record through `objects` lets a user reach a soft-deleted record by URL. This was
the root cause of B-031 and B-033. The `test_soft_deleted_campaign_actions_return_404`
guardrail locks this in for campaign views.

---

# Campaign Lifecycle Rules

## Valid States

* draft
* queued
* sending
* completed
* failed
* cancelled

Do not introduce additional states without explicit requirements.

---

## Active Campaign Locking

Campaigns in:

* queued
* sending

must be treated as immutable.

Do not allow:

* edits
* deletion
* recipient modifications
* attachment modifications
* template modifications

Validation must exist in both:

* UI
* backend

Backend validation is authoritative.

---

## SMTP Configuration Protection

SMTP configurations referenced by active campaigns are locked.

Prevent:

* editing
* deletion

while active campaigns depend on them.

---

## Template Protection

Email templates referenced by active campaigns are locked.

Prevent:

* editing
* deletion

while active campaigns depend on them.

---

## Draft Campaign Behavior

Draft campaigns allow:

* recipient updates
* attachment updates
* template updates
* SMTP changes
* certificate changes

Recipient import modes:

### Append

Adds recipients to the existing list.

### Replace

Removes existing recipients and creates a new list.

Attachment deletion should remain deferred until save confirmation.

---

## Completed, Failed, And Cancelled Campaigns

Allow only limited administrative edits.

Do not modify:

* recipients
* templates
* attachments
* certificate settings

unless explicitly required by future business rules.

---

# Validation Rules

## Zero Recipient Protection

If a campaign contains zero recipients:

Disable and block:

* Start Sending
* Resume Sending
* Restart Campaign

Validation must exist server-side.

---

## Resume Validation

Resume Sending must be blocked when all recipients have already been successfully sent.

Users should be directed toward Restart Campaign instead.

---

## Graceful Failure Handling

Actions such as:

* cancel
* pause
* resume

must fail gracefully when they are not valid for the campaign state.

Provide clear user-facing messages.

---

# Email Rendering Rules

Email clients are restrictive.

Compatibility with Gmail and Outlook is required.

Maintain:

* table-based layouts
* inline styling
* existing rendering patterns

Avoid introducing:

* CSS Grid
* Flexbox-dependent layouts
* unsupported email-client features

unless requirements explicitly change.

---

# Background Task Rules

Long-running work belongs in Celery.

Examples:

* email delivery
* certificate generation
* bulk processing

Views should coordinate work.

Workers should perform work.

Avoid blocking HTTP requests with expensive operations.

---

# Certificate Generation Rules

Certificate rendering is coordinate-driven.

Coordinates are user-owned configuration.

When modifying generation logic:

* preserve coordinate behavior
* preserve existing template compatibility
* preserve rendering consistency

Small changes can affect every generated certificate.

Proceed carefully.

---

# UI & Theme Standards

All interfaces must support:

* Light Mode
* Dark Mode

Prefer Bootstrap theme-aware utilities:

* bg-body
* bg-body-tertiary
* text-body
* border-secondary

Avoid hardcoded light-theme styling.

Examples to avoid:

* bg-white
* bg-light
* text-dark

Business-rule restrictions must always have backend validation.

Never rely solely on frontend controls.

---

# Code Cleanliness Standards

## No Dead Code

Do not leave behind:

* unused functions
* unused classes
* unused imports
* unused variables
* obsolete utilities
* abandoned code paths

When replacing functionality:

* remove the old implementation
* update references
* remove obsolete comments

Version control preserves history.

The codebase should contain only actively useful code.

---

## Avoid Duplicate Logic

Before implementing new functionality:

* search for existing solutions
* extend existing implementations when appropriate

Avoid parallel implementations solving the same problem.

Maintain a single source of truth.

---

## Comment Quality

Comments should explain:

* intent
* reasoning
* business constraints

Do not add comments that merely repeat the code.

---

## Leave The Codebase Cleaner

When modifying files:

* remove unused imports
* remove obvious dead code
* simplify unnecessarily complex logic
* improve naming where practical

Leave touched areas better than you found them.

---

# Architecture & Engineering Standards

## Production-Grade Engineering

Write code as if it will be maintained for years.

Prioritize:

* maintainability
* scalability
* readability
* reliability
* testability

Avoid quick fixes that create long-term technical debt.

---

## Design Before Coding

Before implementing a solution consider:

* business rules
* existing architecture
* future growth
* database impact
* worker impact
* maintenance cost

Prefer thoughtful design over rushed implementation.

---

## Reuse Before Create

Before creating:

* services
* utilities
* helpers
* template components

check whether an equivalent implementation already exists.

Reuse and extend before duplicating.

---

## Use Design Patterns Pragmatically

Use proven Python and Django patterns when they provide real value.

Examples:

* Service Layer
* Strategy Pattern
* Factory Pattern
* Template Method Pattern
* Adapter Pattern

Do not introduce abstractions solely for the sake of abstraction.

Every abstraction should solve a real problem.

---

## Follow Django Conventions

Prefer Django-native approaches first.

Examples:

* model methods
* custom managers
* querysets
* forms
* middleware
* class-based views

Avoid unnecessary architectural layers that duplicate framework functionality.

---

## Scalability Mindset

When designing features consider:

* query efficiency
* memory consumption
* concurrency
* background processing opportunities
* future data growth

Avoid solutions that only work for small datasets.

---

## Single Responsibility Principle

Functions, classes, and modules should have one clear purpose.

Avoid:

* oversized views
* giant utility modules
* multi-purpose functions
* mixed presentation and business logic

---

## Consistency Over Cleverness

Readable code is preferred over clever code.

Future developers should be able to understand the implementation quickly.

---

# Bug Discovery & Technical Debt Tracking

## Continuous Review Mindset

While working on any task, remain alert for:

* bugs
* inconsistencies
* missing validations
* duplicated logic
* security issues
* performance issues
* scalability concerns
* technical debt

---

## Maintain docs/bugs.md

Maintain a centralized:

```text
docs/bugs.md
```

file.

When issues are discovered:

* record them
* include discovery date
* include affected area
* include status
* include severity when relevant

---

## Discovery Does Not Mean Fix

If an issue is discovered while working on another task:

1. Record it in docs/bugs.md.
2. Continue the assigned work.
3. Do not introduce unrelated changes.

Avoid scope creep.

---

## Bug Resolution Tracking

When fixing a recorded issue:

* update the existing entry
* mark it completed
* include resolution date
* briefly document the solution

Preserve historical records.

Do not delete resolved issues.

---

## Technical Debt Tracking

Track:

* missing tests
* duplicated implementations
* refactor opportunities
* legacy code concerns
* architectural improvements

Visibility is preferred over forgotten debt.

---

# Testing Expectations

For bug fixes:

1. Identify root cause.
2. Implement the smallest safe fix.
3. Add regression tests when practical.

For new features:

1. Test business rules.
2. Test edge cases.
3. Test state transitions.

Never remove tests to hide failures.

---

# Development Workflow

Before making changes:

1. Understand the business rule.
2. Identify affected models.
3. Identify affected tasks.
4. Consider scalability.
5. Consider historical data impact.

When work is completed provide:

* summary
* modified files
* reasoning
* risks
* newly discovered bugs
* newly discovered technical debt

---

# Commands

Run tests:

```bash
uv run python manage.py test
```

Create migrations:

```bash
uv run python manage.py makemigrations
```

Apply migrations:

```bash
uv run python manage.py migrate
```

Run Celery worker:

```bash
celery -A core worker --loglevel=info
```

---

# Final Rule

Optimize for:

1. Correctness.
2. Maintainability.
3. Scalability.
4. Simplicity.
5. Production readiness.

Never sacrifice long-term code quality for short-term convenience.

* Do not use em dashes (`—`) anywhere in generated output.

