# Troubleshooting Guide

This document lists common issues, their root causes, and resolutions for EmailOPS.

---

## 1. SMTP & Delivery Issues

### A. Gmail App Password Failures
- **Symptom**: Outbox dispatches fail with authentication errors (`SMTPAuthenticationError`).
- **Cause**: Google Accounts require a unique 16-character **App Password** for SMTP connections. Using your account's primary sign-in password will trigger an authentication block.
- **Resolution**:
  1. Navigate to your Google Account settings -> Security.
  2. Search for **App Passwords** (requires 2-Step Verification to be active).
  3. Create a custom password labeled `emailOPS` and copy the 16-character code.
  4. Paste the code into your **SMTP Config** page in EmailOPS and click **Test Connection**.

### B. Outbox Port 587 Blocked
- **Symptom**: Outbound requests timeout or throw network errors (`OSError`).
- **Cause**: Certain cloud providers (such as AWS, DigitalOcean, or Azure) block outbound traffic on port `587` by default to prevent spam.
- **Resolution**: Request your hosting provider to unblock outbound mail traffic, or update your configuration to route dispatches through port `465` (SSL) or a custom relay port.

---

## 2. Certificate Generation Failures

### A. Naming Coordinates Rendering Microscopic Names
- **Symptom**: Participant names on generated certificates render as tiny text that ignores the configured `font_size` setting.
- **Cause**: The Pillow drawing engine failed to load both the template's custom font and the system fallback fonts. It fell back to Pillow's default font, which renders names at a fixed `11px` height.
- **Resolution**: Ensure a TrueType font file is uploaded with the template, or copy an OFL-licensed `.ttf` file (e.g. `PlayfairDisplay-Bold.ttf`) into `static/fonts/` to vendor it with the application.

### B. Certificates Fail to Generate in Worker Containers
- **Symptom**: Certificate campaigns throw file access errors when running in Celery worker containers.
- **Cause**: The Pillow coordinate builder relies on `FileField.path` to read background images from the local filesystem. This raises errors when using cloud storage backends (like AWS S3 or Google Cloud Storage) because they do not support absolute filesystem paths.
- **Resolution**: Modify the certificate generation utility to read background files using Django's storage API (e.g. `template.image.open('rb')`) instead of absolute filesystem paths.

---

## 3. Authentication & Captha Troubleshooting

### A. Google SSO Unique Constraint Crashes
- **Symptom**: Authenticating with Google SSO throws an `IntegrityError` referencing a UNIQUE constraint failure.
- **Cause**: A local account was previously created using the same email address. When attempting Google SSO, allauth tries to register a new user, colliding with the existing email and username fields.
- **Resolution**: Ensure the following settings are enabled in `core/settings.py` to auto-link matching profiles:
  ```python
  SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
  SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
  ```

### B. Turnstile Validation Blockages
- **Symptom**: Submitting registration or login forms displays the error message: "CAPTCHA verification failed."
- **Cause**: The Turnstile token expired, or the server's outbound HTTP route is blocked from connecting to Cloudflare's verification API.
- **Resolution**:
  1. Verify your server can reach `challenges.cloudflare.com`.
  2. For development environments, ensure Turnstile credentials in your `.env` file default to the official test keys (Site Key: `1x00000000000000000000AA`, Secret Key: `1x0000000000000000000000000000000AA`).

---

## 4. Bug Tracking Protocol

All verified bugs, performance bottlenecks, and security issues must be logged in [bugs.md](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/bugs.md) according to the project's engineering standards.

- **Requirements**:
  - File discoveries in `bugs.md` immediately upon identification.
  - Detail the discovery date, affected component area, severity rating, and a brief fix sketch.
  - When fixing a bug, do not delete its entry in `bugs.md`. Instead, update its status, mark it **Completed**, and record the resolution date to maintain a historical log.
