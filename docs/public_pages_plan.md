# SendGlob - Public Pages and Pricing Unification Plan

**Status:** Implemented (all phases, 2026-07-04). Legal placeholders and the
open items in s.13 still need owner review before launch.
**Date:** 2026-07-03
**Depends on:** billing and ops console (shipped). This plan adds the public-facing
site and makes pricing a single source of truth.

**Two goals:**

1. Ship the public pages: landing (refresh), about, security/trust, the legal
   pages the project actually needs, and a DB-backed contact form surfaced in the
   ops console.
2. **Unify pricing.** Today `core/pricing.py` holds static `INDIA_PRICING` /
   `GLOBAL_PRICING` dicts rendered on the landing page, while the real purchasable
   items live in the billing `CreditPack` / `Plan` models behind `/billing/packs`
   and `/billing/plans`. These are two sources of truth that will drift. This plan
   makes one public `/pricing/` page, driven by the billing models, the only
   pricing surface. Billing keeps checkout only.

Guiding constraint from the product owner: **global clients will not convert
without visible transparency and security.** So the public site leads with what
the system actually does to protect data, drawn only from what is really built
(no roadmap claims).

---

## 1. Which legal pages the project actually needs

Derived from what the codebase genuinely does, not a generic list:

| Page | Why this project needs it |
|---|---|
| **Terms of Service** | Paid product; defines the agreement, prepaid-credit nature, disclaimers, termination. |
| **Privacy Policy** | Collects account/profile/billing data and, critically, users upload **their recipients' personal data** (CSV) plus signatures and generated documents. Must disclose collection, encryption, retention/purge, and sub-processors. |
| **Refund & Cancellation Policy** | Required for Razorpay activation and for prepaid credits/subscriptions. |
| **Acceptable Use / Anti-Spam Policy** | The product sends bulk email from users' own Gmail. An anti-spam/AUP protects the operator and reassures recipients' domains. |
| **Security / Trust page** | The transparency lever for global clients. Documents encryption at rest, authenticated media, BYO-Gmail, purge, sub-processors. Factual, from the code. |
| **Contact** | Razorpay activation and basic trust; DB-backed per decision. |
| **About** | Trust and story; who is behind it and why. |

Cookie usage is covered as a section inside Privacy (the app uses session/CSRF and
Turnstile cookies only; no ad tracking), avoiding a thin standalone page.

All legal text is drafted as **review-ready templates** with placeholders
(`{{ legal_name }}`, `{{ contact_email }}`, `{{ jurisdiction }}`,
`{{ effective_date }}`). A visible internal note marks them "not legal advice,
review before relying." Because the operator is an unregistered individual, the
templates use individual/sole-operator language and an India governing-law
default, both editable.

---

## 2. Pricing unification (the core architectural change)

### 2.1 Single source of truth

- Refactor `core/pricing.py`: **remove** the static `INDIA_PRICING` /
  `GLOBAL_PRICING` dicts. Keep the region detection (`is_india_request`, now
  country-aware). Add `get_public_pricing(request)` that reads **active
  `CreditPack` and `Plan` rows for the resolved currency** from billing and
  returns a display structure (packs, plans, currency, note).
- One shared partial `templates/partials/pricing_table.html` renders that
  structure. Both the public `/pricing/` page and the landing pricing section
  include it, so they can never disagree.

### 2.2 Public /pricing/ is the only pricing surface

- New public view + route `/pricing/` (in the new `pages` app) renders the shared
  partial for guests and authenticated users alike.
- **CTAs bind to billing checkout, not a second listing:**
  - Guest clicks "Buy" -> signup, then returns to checkout for the chosen item
    (carry the pack/plan id through `next`).
  - Authenticated user clicks "Buy" -> POST to the existing
    `billing:checkout_pack` / `billing:checkout_plan` endpoints.
- **Billing loses its standalone pricing pages.** `billing.views.packs` and
  `billing.views.plans` and their templates are removed; `/billing/packs` and
  `/billing/plans` **redirect (301) to `/pricing/`** to preserve any existing
  links. The billing **checkout POST endpoints stay**. The billing dashboard's
  "Buy credits" / "View plans" buttons point to `/pricing/`.
- `setup_billing_plans` remains the seeder; `/pricing/` renders whatever it seeds.
  Empty-DB safety: if no active packs/plans exist, `/pricing/` shows a graceful
  "pricing coming soon / contact us" state and never 500s.

### 2.3 No dead code

Removing the static dicts and the two billing list templates follows the project's
no-dead-code and single-source-of-truth rules. A test asserts the landing and
`/pricing/` render the same seeded items.

---

## 3. New `pages` app

```
pages/
├── apps.py
├── models.py            # ContactMessage
├── admin.py             # ContactMessage admin (read-oriented)
├── views.py             # about, security, contact (GET/POST), legal_* , pricing
├── urls.py
├── forms.py             # ContactForm (+ Turnstile validation, reused)
├── content.py           # effective dates / placeholders for legal pages
└── tests.py
```

Routes mounted at root in `core/urls.py`:

```
/pricing/            pages.views.pricing
/about/              pages.views.about
/security/           pages.views.security
/contact/            pages.views.contact           (GET form, POST store+notify)
/legal/terms/        pages.views.terms
/legal/privacy/      pages.views.privacy
/legal/refund/       pages.views.refund
/legal/acceptable-use/  pages.views.acceptable_use
/sitemap.xml, /robots.txt
```

---

## 4. Contact form (DB-backed, ops-console surfaced)

```python
class ContactMessage(models.Model):
    STATUS_NEW = 'new'; STATUS_READ = 'read'; STATUS_RESOLVED = 'resolved'
    name = models.CharField(max_length=120)
    email = models.EmailField()
    subject = models.CharField(max_length=200, blank=True)
    message = models.TextField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL)   # set if logged in
    status = models.CharField(max_length=10, default=STATUS_NEW)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='handled_messages')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

- Form validated server-side, **Turnstile** anti-spam reused (already integrated),
  honeypot field as defense in depth.
- On submit: store the row, then send an operator notification email through the
  existing SMTP/email infrastructure (best-effort; failure to email never loses
  the stored message).
- **Ops console integration:** a new "Messages" section lists ContactMessages with
  status filters; operators open, reply-by-email (mailto or copy), and mark
  read/resolved. Status changes are audited via the existing `AdminAuditLog`.
  Also registered in Django admin for raw access.
- Rate-limit submissions per IP to prevent flooding.

---

## 5. Public layout and navigation

- New `templates/base_public.html`: marketing header (logo, nav: Product/Features,
  Pricing, About, Security, Login, Sign up) and a **footer present on every public
  page** with links to Pricing, About, Security, Contact, and all legal pages.
  Razorpay activation and user trust both need these links globally visible.
- About, Security, Contact, Pricing, and legal pages extend `base_public.html`.
  The landing page is refactored to share the same header/footer partials so the
  public surface is visually consistent (its bespoke section styling is retained).
- Theme-aware Bootstrap only (`bg-body`, `text-body`, `border-secondary`); no
  hardcoded light/dark colors, per project UI standard. Fully responsive.

---

## 6. Security / Trust page content (from the real codebase)

Facts to present, each already implemented, so this stays honest:

- **Your mail stays yours.** Email is sent from the user's own Gmail via their own
  App Password; SendGlob is not a relay and does not read mailboxes.
- **Encryption at rest.** SMTP App Passwords and signature files are encrypted with
  Fernet; nothing sensitive is stored in plaintext.
- **Private files.** Signatures and generated documents are served only through
  authenticated, ownership-checked views under randomized filenames, never public
  URLs.
- **Data lifecycle.** Soft delete plus a scheduled purge past a retention window,
  with an audit log of purge runs.
- **Abuse protection.** Turnstile on auth and contact; anti-spam acceptable-use
  policy.
- **Sub-processors.** Razorpay (payments), Google (optional OAuth sign-in), AWS
  (hosting). Listed for transparency.
- **Your recipients' data.** The user controls the CSV data they upload; SendGlob
  processes it to send/generate on their behalf and applies the same retention and
  security. The user is responsible for having consent to contact recipients.

This page doubles as the transparency asset for global-client acquisition.

---

## 7. Legal drafts (review-ready, not legal advice)

Each rendered from a template with placeholders and an effective date.

- **Privacy Policy:** data collected (account, profile, billing address, encrypted
  SMTP credentials, uploaded recipient CSV data, signatures, generated documents);
  purposes; legal-basis/consent framing; retention and purge; security measures;
  sub-processors; international transfer note; user rights (access, correction,
  deletion, export); no sale of data; children not permitted; controller/processor
  split for recipient data; contact.
- **Terms of Service:** eligibility and account responsibility; prepaid credits are
  digital goods; BYO-Gmail deliverability is not guaranteed and is subject to
  Google limits; acceptable-use incorporation; suspension/termination; disclaimers
  and limitation of liability; changes; governing law.
- **Refund & Cancellation:** credits are prepaid and generally non-refundable once
  purchased, with exceptions for failed, duplicate, or unauthorized charges;
  unused credits do not expire; subscriptions cancel at period end with no
  pro-rata refund; how to request and expected timelines. (Sensible defaults,
  flagged for the owner to confirm against Razorpay requirements.)
- **Acceptable Use / Anti-Spam:** no unsolicited bulk mail; recipient consent
  required; no illegal, deceptive, malware, or phishing content; respect Google
  and destination-domain policies; enforcement via suspension in the ops console.

---

## 8. SEO and trust basics

Per-page `<title>` and meta description; Open Graph tags on landing and pricing;
`sitemap.xml` and `robots.txt`; favicon; canonical URLs. Lightweight, but material
for organic discovery and for looking legitimate to global buyers.

---

## 9. Integration and cleanup checklist

- `core/urls.py`: mount `pages.urls`; keep `/` landing.
- `core/pricing.py`: delete static dicts; add `get_public_pricing`; keep region
  logic.
- `core/views.landing`: pass `get_public_pricing(request)`.
- `billing/urls.py` + `views.py`: remove `packs`/`plans` list views; add redirects
  to `/pricing/`; keep checkout endpoints.
- `templates/billing/dashboard.html`, `manage_subscription.html`, `packs.html`,
  `plans.html`: repoint buttons to `/pricing/`; delete the two obsolete templates.
- `settings.py`: add `pages` to `INSTALLED_APPS`; operator-notification email and
  legal placeholders read via `getattr` defaults (consistent with existing conf
  pattern; avoid editing settings where a conf module suffices).
- `opsconsole`: add Messages section + audited status actions.

---

## 10. Phased execution

**Phase 1 - Pricing unification.** Refactor `pricing.py` to DB source, shared
pricing partial, public `/pricing/`, rewire landing, convert billing pages to
redirects + checkout, remove static dicts and obsolete templates, empty-DB safety.
This is the highest-value, highest-risk change; do it first and test hard.

**Phase 2 - Public shell + About + Security.** `base_public.html`, shared
header/footer, About and Security/Trust pages.

**Phase 3 - Legal pages.** Terms, Privacy, Refund, Acceptable Use from templates;
footer links live.

**Phase 4 - Contact.** `ContactMessage` model + form + Turnstile + operator
notification + ops-console Messages section + admin.

**Phase 5 - SEO/trust polish + verification.** Meta/OG, sitemap, robots, favicon;
full test pass; manual review of every public route and the legal placeholders.

---

## 11. Testing

- Pricing renders from seeded `CreditPack`/`Plan`; landing and `/pricing/` show
  identical items; region switches INR/USD by country; empty DB degrades, no 500.
- Guest "Buy" -> signup -> returns to correct checkout; authenticated "Buy" ->
  checkout POST.
- `/billing/packs` and `/billing/plans` redirect to `/pricing/`; checkout still
  works; dashboard buttons point to `/pricing/`.
- Contact: validation, Turnstile/honeypot reject spam, row stored, operator
  emailed, appears in ops console, status change audited, rate limit works.
- Every legal/about/security route returns 200, extends the public base, and is
  linked from the footer.
- Theme-aware rendering in light and dark.
- No references remain to the deleted static pricing dicts (guards against dead
  code).

---

## 12. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Legal text treated as authoritative | High | Placeholders + "not legal advice, review" note; owner fills identity and verifies before launch |
| Refund policy fails Razorpay activation | Medium | Use Razorpay-aligned defaults; owner confirms during activation |
| Pricing page 500s on empty/unseeded DB | Medium | Graceful empty state; run `setup_billing_plans`; test covers it |
| Broken links after removing billing pages | Medium | 301 redirects to `/pricing/`; repoint all buttons; link test |
| Contact form spam/abuse | Medium | Turnstile + honeypot + per-IP rate limit |
| Over-claiming security | High | Security page lists only shipped features (honors pitch-only-built-features rule) |

---

## 13. Open items for the owner

- Provide legal identity, support email, and governing jurisdiction for the legal
  placeholders.
- Confirm the refund stance (credits non-refundable except failed/duplicate;
  subscriptions cancel at period end) against Razorpay's requirements.
- Confirm the sub-processor list (Razorpay, Google, AWS) is complete.
