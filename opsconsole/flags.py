"""Cached read access to operational flags for the hot path.

The send and generation pipelines call these before every batch, so reads are
cached for a few seconds via Django's cache framework (LocMemCache by default).
A short TTL means a kill-switch toggle takes effect within seconds without a
redeploy, while avoiding a DB hit per recipient. Writes (via
``opsconsole.controls.set_flag``) clear the cache so changes apply promptly.
"""
from django.core.cache import cache

SEND_PAUSED = 'global_send_paused'
MAINTENANCE = 'maintenance_mode'
THROTTLE = 'send_throttle'

_CACHE_TTL = 5  # seconds
_CACHE_PREFIX = 'opsflag:'


def _cache_key(key):
    return _CACHE_PREFIX + key


def get_flag(key):
    """Return the OperationalFlag for ``key`` (or None), cached briefly."""
    ck = _cache_key(key)
    sentinel = object()
    cached = cache.get(ck, sentinel)
    if cached is not sentinel:
        return cached
    from .models import OperationalFlag
    flag = OperationalFlag.objects.filter(key=key).first()
    # Store a lightweight tuple, not the model instance, to keep the cache cheap
    # and avoid pickling FK state.
    payload = (flag.enabled, flag.value) if flag else None
    cache.set(ck, payload, _CACHE_TTL)
    return payload


def invalidate(key):
    cache.delete(_cache_key(key))


def invalidate_all():
    """Clear all operational flags from cache (used by tests and operators)."""
    for key in (SEND_PAUSED, MAINTENANCE, THROTTLE):
        invalidate(key)


def is_enabled(key):
    payload = get_flag(key)
    return bool(payload and payload[0])


def is_send_paused():
    """True when the global send kill switch is on."""
    return is_enabled(SEND_PAUSED)


def is_maintenance():
    """True when maintenance mode is on."""
    return is_enabled(MAINTENANCE)


def send_throttle_override(default=None):
    """Inter-send delay override in seconds, or ``default`` if not set/enabled."""
    payload = get_flag(THROTTLE)
    if not payload or not payload[0]:
        return default
    try:
        return float(payload[1])
    except (TypeError, ValueError):
        return default
