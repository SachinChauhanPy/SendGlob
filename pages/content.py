"""Operator identity and effective dates for the public and legal pages.

Read from Django settings via getattr so the defaults work out of the box and
the owner can override them in settings or .env without code changes (same
pattern as billing.conf). The legal templates render these placeholders; the
owner must review and fill them before launch (they are not legal advice).
"""
from django.conf import settings


def _get(name, default):
    return getattr(settings, name, default)


def legal_context() -> dict:
    return {
        'legal_name': _get('LEGAL_ENTITY_NAME',
                           'the SendGlob operator (an individual, unregistered)'),
        'contact_email': _get('LEGAL_CONTACT_EMAIL', 'devlopersachin007@gmail.com'),
        'jurisdiction': _get('LEGAL_JURISDICTION', 'India'),
        'effective_date': _get('LEGAL_EFFECTIVE_DATE', 'July 4, 2026'),
    }


def contact_notify_email() -> str:
    """Where operator notifications for new contact messages are sent."""
    return _get('CONTACT_NOTIFY_EMAIL',
                _get('DEFAULT_FROM_EMAIL', 'devlopersachin007@gmail.com'))
