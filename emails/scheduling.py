"""
Schedule computation for campaigns (three-mode model).

Pure, deterministic helpers that turn a campaign's schedule specification
(``schedule_type`` + ``schedule_time`` + the per-mode field) into the next
aware UTC fire time. Kept free of side effects so it can be unit tested in
isolation: every email/document cycle depends on it being correct.

Modes:
    interval  - every ``interval_days`` days; the first run is exactly N days
                from the anchor (creation for a new campaign, the completed run
                for a recurrence) at ``schedule_time``.
    monthly   - on ``day_of_month`` (1-31, clamped to the last valid day of the
                target month) at ``schedule_time``.
    weekly    - on ``day_of_week`` (0=Monday .. 6=Sunday) at ``schedule_time``.

Times are interpreted in the campaign owner's timezone (``CustomUser.timezone``,
defaulting to ``settings.TIME_ZONE``) and returned as aware datetimes. See
docs/scheduling_refactor_plan.md for the rationale. DST edge handling remains a
deferred follow-up.
"""
import calendar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.utils import timezone


def resolve_timezone(campaign):
    """Return the ``ZoneInfo`` the campaign schedules in: its owner's timezone,
    falling back to ``settings.TIME_ZONE`` (and finally UTC) when there is no
    owner or the stored name is invalid. Safe for unsaved, owner-less campaigns
    (used by the form's validation probe)."""
    try:
        user = campaign.user
    except Exception:
        user = None
    name = getattr(user, 'timezone', None) or settings.TIME_ZONE or 'UTC'
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo('UTC')


def timezone_label(campaign):
    """Short label for the campaign's scheduling timezone, used in UI/email copy."""
    return str(resolve_timezone(campaign).key)


def _combine(date_part, time_part, tz):
    """Build an aware datetime in ``tz`` from a date and a time."""
    naive = datetime.combine(date_part, time_part)
    return timezone.make_aware(naive, tz)


def _clamp_day(year, month, day):
    """Clamp a day-of-month to the last valid day of the given month."""
    last = calendar.monthrange(year, month)[1]
    return min(day, last)


def compute_next_run(campaign, *, base=None, now=None):
    """
    Return the next aware UTC datetime ``campaign`` should fire, strictly in the
    future relative to ``now``. ``base`` anchors interval stepping (defaults to
    ``now``); it is the creation time for a fresh schedule or the completed run
    for a recurrence. Returns ``None`` when the campaign is not scheduled or is
    missing a required field.
    """
    now = now or timezone.now()
    base = base or now

    stype = campaign.schedule_type
    stime = campaign.schedule_time
    if stype == campaign.SCHEDULE_NONE or stime is None:
        return None

    # Interpret the wall-clock schedule in the owner's zone, so day-of-week,
    # day-of-month and the time of day are all relative to the user's calendar.
    tz = resolve_timezone(campaign)
    local_now = timezone.localtime(now, tz)

    if stype == campaign.SCHEDULE_INTERVAL:
        n = campaign.interval_days
        if not n or n < 1:
            return None
        candidate_date = timezone.localtime(base, tz).date() + timedelta(days=n)
        candidate = _combine(candidate_date, stime, tz)
        # Roll forward in whole intervals if the anchor was far in the past.
        # Re-combine each step so the wall-clock time stays fixed across DST.
        guard = 0
        while candidate <= now and guard < 10000:
            candidate_date += timedelta(days=n)
            candidate = _combine(candidate_date, stime, tz)
            guard += 1
        return candidate

    if stype == campaign.SCHEDULE_MONTHLY:
        dom = campaign.day_of_month
        if not dom or not (1 <= dom <= 31):
            return None
        ref = local_now
        guard = 0
        while guard < 120:
            day = _clamp_day(ref.year, ref.month, dom)
            candidate = _combine(ref.replace(day=day).date(), stime, tz)
            if candidate > now:
                return candidate
            ref = ref + relativedelta(months=1)
            guard += 1
        return None

    if stype == campaign.SCHEDULE_WEEKLY:
        dow = campaign.day_of_week
        if dow is None or not (0 <= dow <= 6):
            return None
        days_ahead = (dow - local_now.weekday()) % 7
        candidate = _combine(local_now.date() + timedelta(days=days_ahead), stime, tz)
        if candidate <= now:
            candidate = _combine(
                local_now.date() + timedelta(days=days_ahead + 7), stime, tz
            )
        return candidate

    return None


def describe_schedule(campaign):
    """Human-readable one-line summary of a campaign's schedule, for the UI and
    the reminder email. Returns an empty string when not scheduled."""
    stype = campaign.schedule_type
    stime = campaign.schedule_time
    if stype == campaign.SCHEDULE_NONE or stime is None:
        return ""

    t = stime.strftime("%H:%M")
    tz = timezone_label(campaign)
    repeat = "" if campaign.is_recurring else " (once)"

    if stype == campaign.SCHEDULE_INTERVAL:
        n = campaign.interval_days or 0
        unit = "day" if n == 1 else "days"
        return f"Every {n} {unit} at {t} {tz}{repeat}"

    if stype == campaign.SCHEDULE_MONTHLY:
        dom = campaign.day_of_month or 0
        return f"Monthly on day {dom} at {t} {tz}{repeat}"

    if stype == campaign.SCHEDULE_WEEKLY:
        names = dict(campaign.WEEKDAY_CHOICES)
        label = names.get(campaign.day_of_week, "?")
        return f"Weekly on {label} at {t} {tz}{repeat}"

    return ""
