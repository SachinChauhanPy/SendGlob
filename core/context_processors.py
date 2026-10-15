from django.conf import settings

def turnstile_keys(request):
    """
    Exposes Cloudflare Turnstile Site Key in all templates.
    """
    return {
        'TURNSTILE_SITE_KEY': getattr(settings, 'TURNSTILE_SITE_KEY', '')
    }

def app_branding(request):
    """
    Exposes product branding (name and tagline) to every template so the brand
    is configured in one place rather than hardcoded across the UI.
    """
    return {
        'APP_NAME': getattr(settings, 'APP_NAME', 'SendGlob'),
        'APP_TAGLINE': getattr(settings, 'APP_TAGLINE', 'Generate. Personalize. Deliver.'),
    }
