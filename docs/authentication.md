# Authentication & Security

This document describes the user authentication system, Single Sign-On (SSO) configurations, and Captcha verification guards.

---

## 1. Credentials Framework

EmailOPS uses **django-allauth** for session authentication. 
- **Email Primary Identifier**: Authenticators are configured to use emails as unique login IDs instead of standard username tags (`ACCOUNT_LOGIN_METHODS = {"email"}`).
- **User Customization**: Form fields are processed via custom adapters defined in [accounts/adapters.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/accounts/adapters.py).

---

## 2. Google Workspace SSO

Integrations for external login are managed in [core/settings.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/core/settings.py#L152-L170).

- **Provider**: Google OAuth2 application flow.
- **Scopes Requested**: `profile` and `email`.
- **PKCE Verification**: Authorization code flow with Proof Key for Code Exchange (PKCE) is enabled (`OAUTH_PKCE_ENABLED = True`).
- **Direct Login Redirection**: Skip intermediary allauth warning views ("You are about to sign in via Google. Continue?") using `SOCIALACCOUNT_LOGIN_ON_GET = True`. This routes the user directly to Google's sign-in screen upon clicking the Google button.

---

## 3. Account Link Safety & Unique Constraints

If a user signs up using a local email-and-password account and later attempts to register using Google SSO with the same email address, allauth's default behavior tries to create a duplicate user. This results in an `IntegrityError` due to the unique constraint on the email column.

To mitigate this, the following configuration keys are defined in [settings.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/core/settings.py#L127-L136):
```python
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
```

- **Resolution Flow**:
  - The server verifies the Google SSO payload.
  - If the email address matches an existing user profile, allauth auto-connects the Google social account representation to the matching database row instead of creating a duplicate row.
  - The login state is established without error.

---

## 4. Cloudflare Turnstile CAPTCHA Protection

To protect the application from dictionary attacks, brute-force requests, and automated registration bots, Cloudflare Turnstile CAPTCHA is integrated into all public authentication forms:

```
[User Form Submission]
         │
         ├──> Reads 'cf-turnstile-response' Token
         │
         └──> Accounts forms.py calls validate_turnstile()
                     │
                     └──> API POST to challenges.cloudflare.com/turnstile/v0/siteverify
                                 │
                                 └──> Returns Success (Form Cleaned) OR Raises ValidationError
```

- **Target Screens**: Registration (`TurnstileSignupForm`), Login (`TurnstileLoginForm`), and Password Reset (`TurnstileResetPasswordForm`).
- **Token Extraction**: Forms retrieve the widget response token `'cf-turnstile-response'` from the HTTP request payloads.
- **Verification Service**: The server performs an outbound POST request to:
  `https://challenges.cloudflare.com/turnstile/v0/siteverify` passing the site key and client IP.
- **Local Fallbacks**: In local development environments, the keys default to Cloudflare's official test keys, which bypass verification and always return success.
