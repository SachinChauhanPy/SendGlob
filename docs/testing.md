# Testing Strategy

This document details the testing architecture, unit test suites, and validation commands for EmailOPS.

---

## 1. Test Environment Configuration

To ensure unit tests run in isolation and do not affect local database files or initiate real network dispatches:

- **Eager Queue Mode**: Background tasks run synchronously within the testing process. This is configured in [core/settings.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/core/settings.py#L229-L234):
  ```python
  if "test" in sys.argv:
      CELERY_TASK_ALWAYS_EAGER = True
      CELERY_TASK_EAGER_PROPAGATES = True
  ```
- **In-Memory Database**: Django automatically runs tests using a separate, in-memory SQLite database, which is destroyed after the tests finish.
- **Outbound Email Override**: Email is routed to Django's in-memory outbox (`django.core.mail.outbox`) rather than being dispatched to SMTP hosts, enabling dispatches to be verified programmatically.

---

## 2. Test Suite Breakdown

### A. Core app (`core/tests.py`)
- **Pricing Detection**: Asserts that requests containing Cloudflare country headers (`IN`) or Accept-Language overrides fallback correctly to INR pricing, while other locations fall back to USD.
- **Landing Page Rendering**: Verifies that the correct currency metrics (`₹799` vs `$19`) are shown depending on the user's location.

### B. Accounts app (`accounts/tests.py`)
- **App Password Encryption**: Verifies that SMTP Config app passwords are encrypted in the database and decrypt back to their exact original values.
- **Turnstile Captcha Validation**: Verifies that Turnstile checks pass or fail correctly using mock API payloads.

### C. Emails app (`emails/tests.py`)
- **Recipient CSV Normalization**: Asserts that the CSV parser handles various header styles (e.g. `mail` vs `email`) and processes character encodings correctly.
- **Campaign State Machine**: Verifies that campaigns queue, pause, cancel, and restart correctly, and that transition logic functions as expected.
- **Asynchronous Task Flows**: Runs task simulations to verify that dispatches, throttling intervals, and retry behaviors function correctly.
- **Tracking Pixels**: Verifies that absolute tracking URLs are injected into email templates and that visiting a pixel's path records the read timestamp.

### D. Certificates app (`certificates/tests.py`)
- **Pillow Canvas Drawings**: Verifies name coordinate alignments, bounding box logic, and text width measurements.
- **Autoscaling Logic**: Asserts that names exceeding coordinate bounds are dynamically scaled down to fit within the constraints.
- **PDF Generation**: Confirms that raw generated output bytes resolve to valid, single-page PDF structures.

---

## 3. Running the Test Suite

Execute the test runner using the `uv` package manager:
```bash
uv run python manage.py test
```

To run tests for a specific app:
```bash
uv run python manage.py test emails
```

To run a specific test class:
```bash
uv run python manage.py test emails.tests.CampaignTaskTests
```
