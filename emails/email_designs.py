"""
SendGlob - Email design system.

Hand-crafted, email-client-safe layouts. Every layout renders a full HTML
document built from tables + inline CSS only (no <style> blocks, no flexbox,
no external CSS) so it survives Gmail, Outlook, Apple Mail, etc. The output is
the source of truth stored on EmailTemplate.body_html and is later rendered by
the send pipeline with Django's template engine, so merge tags such as
{{ name }} typed by the user pass straight through untouched.

Public API:
    LAYOUTS                      -> ordered list of layout metadata dicts
    get_layout(key)              -> single layout metadata dict (or None)
    default_content(key)         -> starter content dict for a layout
    render(layout, content, brand_color, logo_url) -> (html, text)
"""

import html as _html
import re


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _esc(value):
    """Escape user text for safe HTML embedding while keeping merge tags intact."""
    return _html.escape(str(value or ""), quote=True)


def _clean_color(color, fallback="#0078D4"):
    """Validate a hex color, falling back to the brand default."""
    if color and re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", color.strip()):
        return color.strip()
    return fallback


def _readable_text_on(color):
    """Return black or white depending on the brightness of a hex background."""
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return "#ffffff"
    # Perceived luminance (ITU-R BT.601)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b)
    return "#1a1a1a" if luminance > 160 else "#ffffff"


def _paragraphs_html(body, color="#454545", size="16px"):
    """Split a textarea body on blank lines into styled <p> blocks."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", body or "") if b.strip()]
    if not blocks:
        return ""
    out = []
    for block in blocks:
        # single newlines inside a block become <br>
        safe = _esc(block).replace("\n", "<br>")
        out.append(
            f'<p style="margin:0 0 18px;color:{color};font-size:{size};'
            f'line-height:1.6;">{safe}</p>'
        )
    return "\n".join(out)


def _button(text, url, bg_color):
    """Bulletproof, centered call-to-action button (table-based)."""
    if not text:
        return ""
    text_color = _readable_text_on(bg_color)
    href = _esc(url or "#")
    return f"""
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:8px auto 4px;">
                  <tr>
                    <td align="center" bgcolor="{bg_color}" style="border-radius:8px;">
                      <a href="{href}" target="_blank"
                         style="display:inline-block;padding:14px 32px;font-family:Arial,Helvetica,sans-serif;
                                font-size:16px;font-weight:bold;color:{text_color};text-decoration:none;
                                border-radius:8px;">{_esc(text)}</a>
                    </td>
                  </tr>
                </table>"""


def _logo_block(logo_url, logo_size=40):
    if not logo_url:
        return ""
    try:
        size = int(logo_size)
    except (ValueError, TypeError):
        size = 40
    return f"""
                <tr>
                  <td align="center" style="padding:0 0 24px;">
                    <img src="{_esc(logo_url)}" alt="Logo" height="{size}"
                         style="display:block;max-height:{int(size * 1.2)}px;border:0;outline:none;">
                  </td>
                </tr>"""


def _preheader(preheader):
    """Hidden preview text shown in the inbox list before opening."""
    if not preheader:
        return ""
    return (
        f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;'
        f'font-size:1px;line-height:1px;color:#ffffff;opacity:0;">{_esc(preheader)}</div>'
    )


def _footer_block(footer):
    if not (footer or "").strip():
        return ""
    safe = _esc(footer).replace("\n", "<br>")
    return f"""
                <tr>
                  <td align="center" style="padding:24px 40px;border-top:1px solid #ececec;">
                    <p style="margin:0;color:#9b9b9b;font-size:12px;line-height:1.6;
                              font-family:Arial,Helvetica,sans-serif;">{safe}</p>
                  </td>
                </tr>"""


def _document(brand_color, preheader, inner_rows):
    """Wrap content rows in the outer responsive shell (600px card on grey bg, mobile-responsive)."""
    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>Email</title>
<style>
  @media screen and (max-width: 480px) {{
    .email-body-table, .email-footer-table {{ width: 100% !important; max-width: 100% !important; }}
    .email-inner-cell {{ padding: 20px 16px !important; }}
    .email-content {{ font-size: 14px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background-color:#f2f4f7;
             font-family:Arial,Helvetica,sans-serif;-webkit-font-smoothing:antialiased;">
{_preheader(preheader)}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:#f2f4f7;">
  <tr>
    <td align="center" style="padding:32px 16px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
             class="email-body-table"
             style="width:600px;max-width:600px;background-color:#ffffff;border-radius:14px;
                    overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.06);">
{inner_rows}
      </table>
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
             class="email-footer-table"
             style="width:600px;max-width:600px;">
        <tr>
          <td align="center" style="padding:16px;">
            <p style="margin:0;color:#b4b4b4;font-size:11px;font-family:Arial,Helvetica,sans-serif;">
              Sent with SendGlob
            </p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


def _text_version(content):
    """Plain-text fallback assembled from the structured fields."""
    parts = []
    if content.get("heading"):
        parts.append(content["heading"])
        parts.append("")
    if content.get("subheading"):
        parts.append(content["subheading"])
        parts.append("")
    if content.get("body"):
        parts.append(content["body"].strip())
        parts.append("")
    if content.get("button_text"):
        url = content.get("button_url") or ""
        parts.append(f"{content['button_text']}: {url}")
        parts.append("")
    if content.get("footer"):
        parts.append("---")
        parts.append(content["footer"])
    return "\n".join(parts).strip()


# --------------------------------------------------------------------------- #
# Layout renderers
# --------------------------------------------------------------------------- #

def _render_simple(c, brand, logo, logo_size=40):
    rows = f'<tr><td style="height:4px;background-color:{brand};font-size:0;line-height:0;">&nbsp;</td></tr>'
    rows += f'<tr><td style="height:36px;font-size:0;line-height:0;">&nbsp;</td></tr>'
    rows += _logo_block(logo, logo_size)
    inner = ""
    if c.get("heading"):
        inner += (f'<h1 style="margin:0 0 16px;color:#1a1a1a;font-size:24px;'
                  f'line-height:1.3;font-family:Arial,Helvetica,sans-serif;">{_esc(c["heading"])}</h1>')
    if c.get("subheading"):
        inner += (f'<p style="margin:0 0 16px;color:{brand};font-size:15px;font-weight:bold;'
                  f'font-family:Arial,Helvetica,sans-serif;">{_esc(c["subheading"])}</p>')
    inner += _paragraphs_html(c.get("body", ""))
    inner += _button(c.get("button_text"), c.get("button_url"), brand)
    rows += f"""
                <tr>
                  <td style="padding:44px 40px 8px;font-family:Arial,Helvetica,sans-serif;">
                    {inner}
                  </td>
                </tr>"""
    rows += _footer_block(c.get("footer"))
    return _document(brand, c.get("preheader"), rows)


def _render_announcement(c, brand, logo, logo_size=40):
    band_text = _readable_text_on(brand)
    head = ""
    if c.get("subheading"):
        head += (f'<p style="margin:0 0 8px;color:{band_text};opacity:0.85;font-size:13px;'
                 f'letter-spacing:1px;text-transform:uppercase;font-family:Arial,Helvetica,sans-serif;">'
                 f'{_esc(c["subheading"])}</p>')
    if c.get("heading"):
        head += (f'<h1 style="margin:0;color:{band_text};font-size:30px;line-height:1.25;'
                 f'font-family:Arial,Helvetica,sans-serif;">{_esc(c["heading"])}</h1>')
    body = _paragraphs_html(c.get("body", ""))
    body += _button(c.get("button_text"), c.get("button_url"), brand)
    rows = ""
    if logo:
        try:
            base_size = int(logo_size)
        except (ValueError, TypeError):
            base_size = 40
        announcement_height = int(base_size * 0.9)
        rows += f"""
        <tr><td align="center" bgcolor="{brand}" style="padding:28px 40px 0;">
          <img src="{_esc(logo)}" alt="Logo" height="{announcement_height}" style="display:block;max-height:{int(announcement_height * 1.2)}px;border:0;">
        </td></tr>"""
    if head:
        rows += f"""
        <tr>
          <td align="center" bgcolor="{brand}" style="padding:40px 40px 44px;">
            {head}
          </td>
        </tr>"""
    if body:
        rows += f"""
        <tr>
          <td style="padding:40px 40px 8px;font-family:Arial,Helvetica,sans-serif;">
            {body}
          </td>
        </tr>"""
    rows += _footer_block(c.get("footer"))
    return _document(brand, c.get("preheader"), rows)


def _render_newsletter(c, brand, logo, logo_size=40):
    rows = ""
    # Header bar with logo + label
    if logo:
        try:
            base_size = int(logo_size)
        except (ValueError, TypeError):
            base_size = 40
        newsletter_height = int(base_size * 0.75)
        logo_html = f'<img src="{_esc(logo)}" alt="Logo" height="{newsletter_height}" style="display:block;max-height:{int(newsletter_height * 1.2)}px;border:0;">'
    else:
        logo_html = f'<span style="font-size:18px;font-weight:bold;color:{brand};">Newsletter</span>'

    rows += f"""
        <tr>
          <td style="padding:22px 40px;border-bottom:1px solid #ececec;" align="left">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
              <td align="left" style="font-family:Arial,Helvetica,sans-serif;">
                {logo_html}
              </td>
              <td align="right" style="font-family:Arial,Helvetica,sans-serif;color:#9b9b9b;font-size:12px;">
                {_esc(c.get("subheading","")) }
              </td>
            </tr></table>
          </td>
        </tr>"""
    # Optional hero image
    if c.get("image_url"):
        rows += f"""
        <tr><td style="font-size:0;line-height:0;">
          <img src="{_esc(c['image_url'])}" alt="" width="600"
               style="display:block;width:100%;max-width:600px;border:0;">
        </td></tr>"""
    inner = ""
    if c.get("heading"):
        inner += (f'<h1 style="margin:0 0 18px;color:#1a1a1a;font-size:26px;line-height:1.3;'
                  f'font-family:Arial,Helvetica,sans-serif;">{_esc(c["heading"])}</h1>')
    inner += _paragraphs_html(c.get("body", ""))
    inner += _button(c.get("button_text"), c.get("button_url"), brand)
    rows += f"""
        <tr><td style="padding:36px 40px 8px;font-family:Arial,Helvetica,sans-serif;">{inner}</td></tr>"""
    rows += _footer_block(c.get("footer"))
    return _document(brand, c.get("preheader"), rows)


def _render_promotion(c, brand, logo, logo_size=40):
    band_text = _readable_text_on(brand)
    rows = f'<tr><td style="height:40px;font-size:0;line-height:0;">&nbsp;</td></tr>'
    rows += _logo_block(logo, logo_size)
    offer = c.get("subheading", "")
    inner = ""
    if offer:
        inner += f"""
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto 22px;">
                  <tr><td align="center" bgcolor="{brand}" style="border-radius:30px;padding:8px 22px;">
                    <span style="color:{band_text};font-size:13px;font-weight:bold;letter-spacing:1px;
                                 text-transform:uppercase;font-family:Arial,Helvetica,sans-serif;">{_esc(offer)}</span>
                  </td></tr>
                </table>"""
    if c.get("heading"):
        inner += (f'<h1 style="margin:0 0 18px;color:#1a1a1a;font-size:32px;line-height:1.2;text-align:center;'
                  f'font-family:Arial,Helvetica,sans-serif;">{_esc(c["heading"])}</h1>')
    body_html = _paragraphs_html(c.get("body", ""))
    body_html = body_html.replace('line-height:1.6;">', 'line-height:1.6;text-align:center;">')
    inner += body_html
    inner += _button(c.get("button_text"), c.get("button_url"), brand)
    rows += f"""
                <tr><td style="padding:48px 40px 12px;font-family:Arial,Helvetica,sans-serif;">{inner}</td></tr>"""
    rows += _footer_block(c.get("footer"))
    return _document(brand, c.get("preheader"), rows)


def _render_event(c, brand, logo, logo_size=40):
    rows = f'<tr><td style="height:4px;background-color:{brand};font-size:0;line-height:0;">&nbsp;</td></tr>'
    rows += f'<tr><td style="height:36px;font-size:0;line-height:0;">&nbsp;</td></tr>'
    rows += _logo_block(logo, logo_size)
    inner = ""
    if c.get("subheading"):
        inner += (f'<p style="margin:0 0 8px;color:{brand};font-size:13px;font-weight:bold;'
                  f'letter-spacing:1px;text-transform:uppercase;font-family:Arial,Helvetica,sans-serif;">'
                  f'{_esc(c["subheading"])}</p>')
    if c.get("heading"):
        inner += (f'<h1 style="margin:0 0 18px;color:#1a1a1a;font-size:27px;line-height:1.3;'
                  f'font-family:Arial,Helvetica,sans-serif;">{_esc(c["heading"])}</h1>')
    inner += _paragraphs_html(c.get("body", ""))
    # Details panel
    details = c.get("event_details", "")
    if details:
        det_rows = ""
        for line in [l.strip() for l in details.split("\n") if l.strip()]:
            det_rows += (f'<tr><td style="padding:6px 0;color:#454545;font-size:15px;'
                         f'font-family:Arial,Helvetica,sans-serif;">{_esc(line)}</td></tr>')
        inner += f"""
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                       style="margin:8px 0 22px;background-color:#f7f9fc;border-radius:10px;">
                  <tr><td style="padding:18px 22px;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{det_rows}</table>
                  </td></tr>
                </table>"""
    inner += _button(c.get("button_text"), c.get("button_url"), brand)
    rows += f"""
                <tr><td style="padding:44px 40px 8px;font-family:Arial,Helvetica,sans-serif;">{inner}</td></tr>"""
    rows += _footer_block(c.get("footer"))
    return _document(brand, c.get("preheader"), rows)


_RENDERERS = {
    "simple": _render_simple,
    "announcement": _render_announcement,
    "newsletter": _render_newsletter,
    "promotion": _render_promotion,
    "event": _render_event,
}


# --------------------------------------------------------------------------- #
# Layout metadata + starter content
# --------------------------------------------------------------------------- #

LAYOUTS = [
    {
        "key": "simple",
        "label": "Simple / Transactional",
        "icon": "align-left",
        "description": "Clean, focused message with one call to action. Great for confirmations, updates and outreach.",
        "fields": ["heading", "subheading", "body", "button_text", "button_url"],
    },
    {
        "key": "announcement",
        "label": "Announcement",
        "icon": "megaphone",
        "description": "Bold colored banner headline. Perfect for launches, news and important updates.",
        "fields": ["subheading", "heading", "body", "button_text", "button_url"],
    },
    {
        "key": "newsletter",
        "label": "Newsletter",
        "icon": "newspaper",
        "description": "Header bar with an optional hero image and article-style body. Ideal for regular digests.",
        "fields": ["heading", "subheading", "image_url", "body", "button_text", "button_url"],
    },
    {
        "key": "promotion",
        "label": "Promotion / Offer",
        "icon": "tag",
        "description": "Centered, eye-catching offer badge and big headline to drive clicks on a deal.",
        "fields": ["subheading", "heading", "body", "button_text", "button_url"],
    },
    {
        "key": "event",
        "label": "Event Invite",
        "icon": "calendar-days",
        "description": "Invitation layout with a highlighted details panel (date, time, location) and an RSVP button.",
        "fields": ["subheading", "heading", "body", "event_details", "button_text", "button_url"],
    },
]

_DEFAULT_CONTENT = {
    "simple": {
        "preheader": "A quick note from our team",
        "heading": "Hi {{ name }}, here's your update",
        "subheading": "",
        "body": "Thanks for being with us. We wanted to share a quick update and make sure you have everything you need.\n\nReply to this email any time - a real person reads every message.",
        "button_text": "View details",
        "button_url": "https://",
        "footer": "You're receiving this because you signed up for our updates.\nSendGlob · Unsubscribe any time.",
    },
    "announcement": {
        "preheader": "Something new just landed",
        "subheading": "Announcement",
        "heading": "We just launched something big",
        "body": "Hi {{ name }}, we've been working hard behind the scenes and we're thrilled to finally share it with you.\n\nTake a look and let us know what you think.",
        "button_text": "See what's new",
        "button_url": "https://",
        "footer": "You're receiving this because you're part of our community.\nUnsubscribe any time.",
    },
    "newsletter": {
        "preheader": "Your latest digest is here",
        "heading": "This month at a glance",
        "subheading": "Issue #1",
        "image_url": "",
        "body": "Hi {{ name }}, here's a roundup of what's been happening and what's coming next.\n\nGrab a coffee and dig in - we've kept it short.",
        "button_text": "Read the full update",
        "button_url": "https://",
        "footer": "You subscribed to our newsletter.\nUnsubscribe any time.",
    },
    "promotion": {
        "preheader": "A special offer, just for you",
        "subheading": "Limited time",
        "heading": "Save 25% this week only",
        "body": "Hi {{ name }}, as a thank-you for being a loyal customer, here's an exclusive discount on your next order.\n\nHurry - this offer ends Sunday.",
        "button_text": "Claim my discount",
        "button_url": "https://",
        "footer": "Offer valid for a limited time.\nUnsubscribe any time.",
    },
    "event": {
        "preheader": "You're invited - save the date",
        "subheading": "You're invited",
        "heading": "Join us for an evening to remember",
        "body": "Hi {{ name }}, we'd love for you to join us. Here are the details:",
        "event_details": "Date: Friday, June 12, 2026\nTime: 6:00 PM – 9:00 PM\nLocation: The Grand Hall, Downtown",
        "button_text": "RSVP now",
        "button_url": "https://",
        "footer": "Please RSVP by June 5.\nUnsubscribe any time.",
    },
}


def get_layout(key):
    for layout in LAYOUTS:
        if layout["key"] == key:
            return layout
    return None


def default_content(key):
    """Return a copy of the starter content for a layout (falls back to simple)."""
    base = _DEFAULT_CONTENT.get(key, _DEFAULT_CONTENT["simple"])
    return dict(base)


def render(layout, content, brand_color="#0078D4", logo_url="", logo_size=40):
    """
    Render a layout to (html, text).

    `content` is a dict of the structured fields. Unknown layouts fall back to
    'simple'. Brand color is validated; an unsafe value uses the default.
    """
    brand = _clean_color(brand_color)
    renderer = _RENDERERS.get(layout, _render_simple)
    content = content or {}
    html_out = renderer(content, brand, (logo_url or "").strip(), logo_size)
    text_out = _text_version(content)
    return html_out, text_out
