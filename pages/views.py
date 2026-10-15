"""Public site views: pricing, about, security, legal pages, contact, and the
SEO endpoints (robots.txt, sitemap.xml)."""
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from core.pricing import get_public_pricing

from . import content
from .forms import ContactForm
from .models import ContactMessage

logger = logging.getLogger(__name__)

# ?buy=pack-<pk> / ?buy=plan-<pk>: set when a guest clicked Buy, signed up, and
# was sent back here. Strictly validated because the value round-trips through
# a redirect and lands in the template.
_BUY_PARAM_RE = re.compile(r'^(pack|plan)-(\d+)$')

# Contact-form flood control (per IP, sliding hour). Override in settings.
CONTACT_RATE_LIMIT_PER_HOUR = getattr(settings, 'CONTACT_RATE_LIMIT_PER_HOUR', 5)


def pricing(request):
    """The only public pricing surface, driven by the billing models.

    For authenticated visitors arriving with a valid ``buy`` parameter, the
    template auto-submits the matching checkout form so a guest who clicked
    "Buy", signed up, and returned lands directly in checkout.
    """
    context = {'pricing': get_public_pricing(request)}
    if request.user.is_authenticated:
        match = _BUY_PARAM_RE.match(request.GET.get('buy', ''))
        if match:
            context['auto_buy'] = f'buy-{match.group(1)}-{match.group(2)}'
    return render(request, 'pages/pricing.html', context)


def about(request):
    return render(request, 'pages/about.html', content.legal_context())


def security(request):
    return render(request, 'pages/security.html', content.legal_context())


def terms(request):
    return render(request, 'pages/legal/terms.html', content.legal_context())


def privacy(request):
    return render(request, 'pages/legal/privacy.html', content.legal_context())


def refund(request):
    return render(request, 'pages/legal/refund.html', content.legal_context())


def acceptable_use(request):
    return render(request, 'pages/legal/acceptable_use.html', content.legal_context())


def _client_ip(request):
    return request.META.get('REMOTE_ADDR') or None


def _rate_limited(request) -> bool:
    ip = _client_ip(request)
    if not ip:
        return False
    window_start = timezone.now() - timedelta(hours=1)
    recent = ContactMessage.objects.filter(
        ip_address=ip, created_at__gte=window_start).count()
    return recent >= CONTACT_RATE_LIMIT_PER_HOUR


def _notify_operator(msg: ContactMessage) -> None:
    """Best-effort operator notification; failure never loses the stored row."""
    try:
        send_mail(
            subject=f"[SendGlob contact] {msg.subject or 'New message'}",
            message=(f"From: {msg.name} <{msg.email}>\n"
                     f"User: {msg.user_id or 'guest'}\n\n{msg.message}"),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[content.contact_notify_email()],
            fail_silently=False,
        )
    except Exception:
        logger.exception('Operator notification for ContactMessage %s failed',
                         msg.pk)


def contact(request):
    """GET renders the form; POST stores the message then notifies the operator."""
    if request.method == 'POST':
        if _rate_limited(request):
            messages.error(request,
                           'Too many messages from your network. Please try again later.')
            return redirect('pages:contact')
        form = ContactForm(request.POST, request=request)
        if form.is_valid():
            msg = form.save(commit=False)
            if request.user.is_authenticated:
                msg.user = request.user
            msg.ip_address = _client_ip(request)
            msg.user_agent = (request.META.get('HTTP_USER_AGENT') or '')[:300]
            msg.save()
            _notify_operator(msg)
            messages.success(request,
                             "Thanks, your message has been received. We'll reply by email.")
            return redirect('pages:contact')
    else:
        initial = {}
        if request.user.is_authenticated:
            initial = {'name': request.user.full_name or '',
                       'email': request.user.email}
        form = ContactForm(initial=initial)
    context = {'form': form}
    context.update(content.legal_context())
    return render(request, 'pages/contact.html', context)


# --- SEO endpoints (public pages plan s.8) ---

# The public, crawlable routes. Everything else (dashboard, billing, ops,
# accounts, admin) is deliberately disallowed.
_PUBLIC_ROUTE_NAMES = [
    'landing', 'pages:pricing', 'pages:about', 'pages:security',
    'pages:contact', 'pages:terms', 'pages:privacy', 'pages:refund',
    'pages:acceptable_use',
]


def _site_base(request) -> str:
    site_url = (getattr(settings, 'SITE_URL', '') or '').rstrip('/')
    return site_url or f"{request.scheme}://{request.get_host()}"


def robots_txt(request):
    base = _site_base(request)
    lines = [
        'User-agent: *',
        'Disallow: /dashboard/',
        'Disallow: /billing/',
        'Disallow: /accounts/',
        'Disallow: /ops/',
        'Disallow: /admin/',
        'Allow: /',
        f'Sitemap: {base}/sitemap.xml',
    ]
    return HttpResponse('\n'.join(lines) + '\n', content_type='text/plain')


def sitemap_xml(request):
    base = _site_base(request)
    urls = ''.join(
        f'  <url><loc>{base}{reverse(name)}</loc></url>\n'
        for name in _PUBLIC_ROUTE_NAMES
    )
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           f'{urls}</urlset>\n')
    return HttpResponse(xml, content_type='application/xml')
