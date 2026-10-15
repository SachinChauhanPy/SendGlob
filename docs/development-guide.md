# Developer & Contribution Guide

This document outlines the engineering standards, invariants, and best practices for developing and maintaining EmailOPS.

---

## 1. Project Philosophy & Standards

EmailOPS is a production application. Developers must follow these guidelines:
1. **Preserve Data Integrity**: Never physically delete records containing historical performance data or recipient delivery histories.
2. **Prioritize Historical Accuracy**: Campaign outcomes, recipient delivery histories, and generated certificates represent historical records and must be protected from accidental edits.
3. **Leave touched code cleaner**: Remove unused imports, simplify complex logic, and clean up dead code paths during edits.

---

## 2. Critical Architectural Invariants

### A. Soft Delete is Mandatory
To preserve foreign-key constraints and historical records, models inheriting from `SoftDeleteModel` must not be physically deleted.
- Users see active listings filtered by the `active` manager.
- Administrative, analytical, and foreign-key calculations use the unfiltered `objects` manager to resolve relations.
- **Rule**: Avoid manual filtering of soft-deleted records. Use `active` for dashboards, listings, and selectors, and `objects` for administrative tasks and foreign-key checks.

### B. Campaign State is the Source of Truth
Campaign progress is managed by a strict state machine: `draft` -> `queued` -> `sending` -> `completed`, `failed`, or `cancelled`.
- **Locking rule**: Campaigns in the `queued` or `sending` states are immutable. Edit, delete, recipient update, and attachment update views must block modifications to prevent race conditions with background Celery workers.
- **Validation rule**: Validate state transitions on both the frontend and the backend. The backend validation checks are authoritative.

### C. Resource Locking Constraints
To prevent dispatches from failing mid-run:
- **SMTP configuration lock**: An SMTP profile cannot be edited or deleted if it is linked to a campaign in the `queued` or `sending` state.
- **Email template lock**: An email template cannot be edited or deleted if it is linked to an active campaign.

### D. Zero-Recipient Protection
- If a campaign contains zero recipients, the **Start Sending**, **Resume Sending**, and **Restart Campaign** actions must be blocked on the server.

---

## 3. Code Cleanliness Standards

- **No Dead Code**: Do not leave unused imports, classes, functions, or commented-out code blocks in the codebase. Version control preserves history; keep the active codebase clean.
- **Avoid Duplicate Logic**: Search for existing solutions and extend them rather than writing duplicate implementations. For example, use the dynamic managers instead of duplicating filtering logic.
- **Write High-Quality Comments**: Write comments that explain the **why** and the business logic constraints behind the code, rather than simply explaining what the code does.

---

## 4. Bug Tracking Workflow

All bugs, discrepancies, or technical debt identified during development must be documented in [bugs.md](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/bugs.md).

1. **Log Discoveries**: Document issues immediately upon discovery. Do not fix unrelated bugs within your active task branch.
2. **Document Resolution**: When resolving an issue, update its status, mark it **Completed**, and record the resolution date. Do not delete resolved entries from `bugs.md` to maintain a historical log.
