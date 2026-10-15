"""Pricing region detection and the public pricing data source.

The billing ``CreditPack`` / ``Plan`` models are the single source of truth for
anything purchasable. ``get_public_pricing`` reads them for the visitor's
resolved currency; the shared ``templates/partials/pricing_table.html`` partial
renders that structure on both the landing page and /pricing/, so the two
surfaces can never disagree (public pages plan s.2).
"""
from __future__ import annotations


INDIA_COUNTRY_CODE = "IN"

COUNTRY_HEADER_KEYS = (
    "HTTP_CF_IPCOUNTRY",
    "HTTP_X_VERCEL_IP_COUNTRY",
    "HTTP_X_COUNTRY_CODE",
    "HTTP_X_APPENGINE_COUNTRY",
    "HTTP_CLOUDFRONT_VIEWER_COUNTRY",
)

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _country_from_headers(request) -> str:
    for key in COUNTRY_HEADER_KEYS:
        country_code = (request.META.get(key) or "").strip().upper()
        if country_code and country_code != "XX":
            return country_code
    return ""


def _accept_language_indicates_india(request) -> bool:
    accept_language = (request.META.get("HTTP_ACCEPT_LANGUAGE") or "").lower()
    return "-in" in accept_language or "_in" in accept_language


def _is_local_request(request) -> bool:
    host = request.get_host().split(":")[0].lower()
    remote_addr = (request.META.get("REMOTE_ADDR") or "").lower()
    return host in LOCAL_HOSTS or remote_addr in LOCAL_HOSTS


def is_india_request(request) -> bool:
    # An authenticated user's saved country is authoritative when set; it removes
    # the fragility of header-based geo detection (billing plan s.4.1).
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        user_country = (getattr(user, "country", "") or "").strip().upper()
        if user_country:
            return user_country == INDIA_COUNTRY_CODE
    country_code = _country_from_headers(request)
    if country_code:
        return country_code == INDIA_COUNTRY_CODE
    if _accept_language_indicates_india(request):
        return True
    return _is_local_request(request)


def get_public_pricing(request) -> dict:
    """Display structure for the shared pricing partial, read from billing.

    Prefers active packs/plans in the visitor's currency; if none exist in that
    currency it falls back to all active items (same behaviour the old billing
    list views had) so a partially seeded catalogue still renders. An entirely
    empty catalogue yields ``has_items=False`` and the partial shows a graceful
    "coming soon" state instead of an error.
    """
    # Local import: billing already imports core.pricing for region detection,
    # so a module-level import here would be circular.
    from billing import conf
    from billing.models import CreditPack, Plan

    preferred_currency = "INR" if is_india_request(request) else "USD"
    packs = list(CreditPack.objects.filter(is_active=True, currency=preferred_currency))
    plans = list(Plan.objects.filter(is_active=True, currency=preferred_currency))
    currency = preferred_currency
    if not packs and not plans:
        packs = list(CreditPack.objects.filter(is_active=True))
        plans = list(Plan.objects.filter(is_active=True))
        if packs or plans:
            currency = (packs or plans)[0].currency
    return {
        "currency": currency,
        "note": f"Prices shown in {currency}.",
        "packs": packs,
        "plans": plans,
        "has_items": bool(packs or plans),
        "free_credits": conf.free_signup_credits(),
    }
