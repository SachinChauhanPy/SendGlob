# Glossary

This glossary defines terms, components, and concepts used throughout the EmailOPS project.

---

### Bounding Box Coordinate Bounds
The naming bounds configured on a `CertificateTemplate` template:
- `x_start` and `x_end` represent the horizontal boundaries in pixels. Names are automatically centered within this range.
- `y_coordinate` represents the vertical baseline height in pixels where text drawing occurs.
- If a participant's name is too long to fit in this bounding box, the Pillow renderer automatically scales down the font size.

---

### App Password Encryption
The security process used to encrypt Gmail App Passwords before database insertion:
- Uses symmetric key cryptography (`cryptography.fernet.Fernet`).
- Requires a URL-safe, base64-encoded 32-byte key stored in the `SMTP_ENCRYPTION_KEY` environment variable.
- Encrypted strings are stored in `encrypted_password` fields, ensuring SMTP credentials cannot be retrieved without the server's environment configuration keys.

---

### Soft Delete
A database deletion pattern where rows are flagged as inactive instead of being physically removed:
- Tracks deletions using a `deleted_at` datetime field.
- **`objects` manager**: Includes all records. Used for database references, administrative reviews, and historical statistics.
- **`active` manager**: Excludes soft-deleted records. Used for user-facing list views, dashboard metrics, and dropdown forms.

---

### 1x1 Tracking Pixel
A tracking mechanism used to record email open rates:
- Serves a 1x1 pixel transparent GIF (`image/gif`) from the `/dashboard/track/<uuid>/pixel.png` route.
- Injected into HTML template bodies before the closing `</body>` tag during dispatch.
- When loaded by a recipient's email client, the server records the read timestamp (`opened_at`) in the database.

---

### Stale Sending Reaper
A Celery maintenance task that prevents campaign dispatches from stalling:
- Scans the recipient database for entries stuck in the `sending` status for longer than 10 minutes (indicating a worker crashed or died mid-run).
- Resets stuck entries back to the `retrying` status so they can be picked up by active workers.

---

### Throttled Dispatch
A rate-limiting mechanism used during email campaigns:
- EmailOPS pauses for `1.5` seconds between each recipient's email dispatch.
- This helps avoid triggering spam filters and respects daily SMTP rate limits set by providers (such as Google SMTP limits).

---

### Cloudflare Turnstile
A privacy-focused CAPTCHA service integrated into EmailOPS:
- Displays Turnstile widgets on public forms (e.g. login, sign-up, password reset).
- Verifies submission tokens on the server by checking them against Cloudflare's verification API.
