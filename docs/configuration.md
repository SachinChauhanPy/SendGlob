# Configuration Reference

This document details all configuration options, environment variables, and settings profiles available in EmailOPS.

---

## Environment Variables (.env)

The application loads environment variables from a `.env` file in the project root using `python-dotenv`.

| Variable | Required | Default (Dev) | Description |
| :--- | :--- | :--- | :--- |
| `SECRET_KEY` | Yes | Django Insecure Key | Cryptographic key used for signing Django sessions, cookies, and tokens. |
| `DEBUG` | No | `True` | Standard Django debug flag. Must be set to `False` in production. |
| `ALLOWED_HOSTS` | No | `*` | Comma-separated list of host/domain names that this Django site can serve. |
| `SITE_URL` | Yes | `http://127.0.0.1:8000` | The public base URL of your site. Used to construct tracking pixel URLs. |
| `SMTP_ENCRYPTION_KEY` | Yes | default placeholder | A 32-byte URL-safe base64 encryption key for encrypting SMTP App Passwords. |
| `TURNSTILE_SITE_KEY` | No | `1x00000000000000000000AA` | Cloudflare Turnstile public site key. Default is CF's test key (always passes). |
| `TURNSTILE_SECRET_KEY` | No | `1x0000...00AA` | Cloudflare Turnstile secret key. Default is CF's test key. |
| `GEMINI_API_KEY` | No | *Empty* | API token for Google Gemini AI. Required for the template content generator. |
| `GOOGLE_CLIENT_ID` | No | placeholder | OAuth2 Client ID for Google SSO registration and sign-in. |
| `GOOGLE_CLIENT_SECRET` | No | placeholder | OAuth2 Client Secret for Google SSO registration and sign-in. |
| `CELERY_BROKER_URL` | No | `redis://127.0.0.1:6379/0` | The transport URL of the Celery broker. |
| `EMAIL_BACKEND` | No | console backend | Default email sender backend for django-allauth system emails. |
| `SECURE_PROXY_SSL_HEADER` | Behind a TLS proxy | `False` | Set `True` only when a reverse proxy terminates TLS and overwrites `X-Forwarded-Proto`; otherwise the HTTPS redirect loops. |
| `SECURE_SSL_REDIRECT` | No | `True` (prod) | Redirect HTTP to HTTPS when `DEBUG=False`. |
| `SECURE_HSTS_SECONDS` | No | `2592000` (prod) | HSTS max-age when `DEBUG=False`. Raise once HTTPS is confirmed stable. |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | No | `False` | Enable only if every subdomain is served over HTTPS. |
| `LOG_LEVEL` | No | `INFO` | Root console log level. |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | For live payments | *Empty* | Razorpay API keys. Without both (and the `razorpay` package) checkout runs in manual mode. |
| `RAZORPAY_WEBHOOK_SECRET` | With live keys | *Empty* | Secret of the Razorpay webhook pointing at `/billing/webhook/razorpay/`. Required for renewals, failures and refunds. |
| `BILLING_FREE_SIGNUP_CREDITS` | No | `50` | One-time credits granted on signup. |
| `BILLING_FREE_RECIPIENT_CAP` | No | `50` | Recipients-per-run cap on the free tier. |
| `BILLING_RECEIPT_PREFIX` | No | `SG` | Receipt number prefix (`SG-2026-000001`). |
| `BILLING_SELLER_NAME` / `_ADDRESS` / `_EMAIL` / `_TAX_ID` | No | `SendGlob` / empty | Issuer details printed on receipts. Use a literal `\n` in the address for line breaks. |
| `BILLING_PENDING_CHECKOUT_TTL_HOURS` | No | `24` | Unpaid pack orders and pending subscriptions expire after this. Manual orders can still be fulfilled afterwards. |
| `BILLING_SUBSCRIPTION_GRACE_DAYS` | No | `3` | Days past the paid period before an un-renewed plan is expired locally. |
| `BILLING_INFRA_COST_FLOOR` | No | `0` | Monthly cost the `revenue_report` command compares revenue against. |

---

## Production Startup Guards

With `DEBUG=False`, startup fails if `SECRET_KEY` is the committed `django-insecure` fallback, if `ALLOWED_HOSTS` contains `*`, or if `SITE_URL` points at localhost. Secure cookies, the HTTPS redirect, HSTS and `nosniff` are enabled automatically.

## Billing Operations

Run `uv run python manage.py check --deploy` before going live: the `billing.*` checks flag missing webhook secrets, half-configured keys and test keys in production.

Razorpay webhook events to enable: `payment.captured`, `payment.failed`, `subscription.charged`, `subscription.cancelled`, `subscription.completed`, `subscription.halted`, `subscription.paused`, `subscription.resumed`, `subscription.updated`, `refund.processed`. Each active `Plan` needs a Razorpay plan: in live mode the admin creates one automatically on save (and a new one whenever price, currency or cycle changes; existing subscribers keep their original price). Link plans created before the keys were set with `uv run python manage.py sync_razorpay_plans`.

Plan switches requested by users take effect at the next renewal (no mid-cycle proration).

Celery Beat must run `billing.tasks.expire_lapsed_subscriptions_task` (scheduled hourly in `core/celery.py`).

Manual payments (no gateway, or bank transfer):

```bash
uv run python manage.py fulfill_pack_order <order-reference> <payment-reference>
uv run python manage.py activate_plan <email> <plan-id> <payment-reference>
uv run python manage.py reconcile_payments
```

---

## Configuration Details & Safeguards

### 1. `SITE_URL` Validation
Email open-tracking pixels rely on an absolute URL pointing back to the server. If `SITE_URL` defaults to a localhost address in production, recipient clients will try to load the 1x1 tracking pixel from their own machine, causing analytics to fail.

To prevent this, the application includes a startup validation rule inside [settings.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/core/settings.py#L243-L250):
```python
if not DEBUG and "test" not in sys.argv:
    from django.core.exceptions import ImproperlyConfigured

    if not SITE_URL or any(host in SITE_URL for host in ("127.0.0.1", "localhost")):
        raise ImproperlyConfigured(
            "SITE_URL must be set to the public site URL when DEBUG=False; "
            f"got {SITE_URL!r}. Email open-tracking pixels depend on it."
        )
```

### 2. Dynamic CSRF Origin Configuration
To ensure authentication requests pass verification when running under alternative ports or SSH reverse tunnels (e.g. ngrok), the system computes dynamic CSRF trusted origins based on `SITE_URL` at startup inside [settings.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/core/settings.py#L252-L261):
```python
CSRF_TRUSTED_ORIGINS = []
if SITE_URL:
    clean_url = SITE_URL.rstrip("/")
    CSRF_TRUSTED_ORIGINS.append(clean_url)
    if clean_url.startswith("http://"):
        CSRF_TRUSTED_ORIGINS.append(clean_url.replace("http://", "https://"))
    elif clean_url.startswith("https://"):
        CSRF_TRUSTED_ORIGINS.append(clean_url.replace("https://", "http://"))
```

### 3. SMTP Encryption Configuration
`SMTP_ENCRYPTION_KEY` must be a valid Fernet key. If it is compromised, database records storing SMTP credentials must be re-entered. 

> [!WARNING]
> Keep `SMTP_ENCRYPTION_KEY` consistent across application restarts. If changed, any existing encrypted passwords in the database will raise decryption errors, and SMTP dispatches will fail.
