"""
SendGlob - AI copy generation.

Instead of asking the model for raw HTML (which is fragile and often breaks in
email clients), we ask Gemini for STRUCTURED COPY that slots into our hand-built,
email-safe layouts (see emails/email_designs.py). The design quality is therefore
guaranteed by our renderer, and the AI focuses purely on writing excellent,
layout-appropriate copy. This is the reliable way to get consistently good
email designs.
"""

import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# What each layout actually uses, so we can steer the model.
_LAYOUT_GUIDANCE = {
    "simple": "A clean transactional/outreach email with one clear call to action. Keep it warm and concise.",
    "announcement": "A bold announcement. 'subheading' is a short uppercase eyebrow label (1-2 words like 'Announcement' or 'New'). 'heading' is the punchy headline.",
    "newsletter": "A newsletter digest. 'subheading' is a short issue label (e.g. 'Issue #4' or a date). Body can have 2-3 short paragraphs.",
    "promotion": "A promotional offer. 'subheading' is a short uppercase deal label (e.g. 'Limited time', '25% OFF'). 'heading' states the offer. Body is short and urgent.",
    "event": "An event invitation. 'subheading' is a short label like 'You're invited'. Put concrete logistics in 'event_details' as separate lines (Date: ..., Time: ..., Location: ...).",
}

_SYSTEM_INSTRUCTION = (
    "You are a senior email marketing copywriter. You write concise, scannable, high-converting "
    "email copy that fits a fixed, professionally designed layout. Rules:\n"
    "1. Output ONLY structured copy - never HTML, markdown, or styling.\n"
    "2. Subject line: under 60 characters, specific, no spammy ALL-CAPS or excessive punctuation.\n"
    "3. Preheader: 40-90 characters, complements (does not repeat) the subject.\n"
    "4. Heading: short and benefit-led.\n"
    "5. Body: 2-4 short paragraphs separated by a blank line; lead with the value; one idea per paragraph.\n"
    "6. Use the merge tag {{ name }} for personalization where it reads naturally (e.g. the greeting). "
    "You may also use {{ email }} or other reasonable merge tags.\n"
    "7. Button text: 2-4 words, action-oriented (e.g. 'Get started', 'Claim offer'). "
    "Leave button_url as 'https://' unless the user explicitly gives a link.\n"
    "8. Footer: a brief, compliant sign-off line (e.g. why they're receiving this + an unsubscribe mention).\n"
    "9. Match the tone the user asks for; default to friendly and professional.\n"
    "Only fill fields relevant to the chosen layout; leave others as empty strings."
)


def generate_email_content_ai(prompt, layout="simple", brand_color="#0078D4"):
    """
    Generate structured email copy for a given layout.

    Returns a dict: {success, subject, preheader, heading, subheading, body,
    button_text, button_url, footer, event_details, image_url} on success,
    or {success: False, error: ...} on failure.
    """
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        return {
            "success": False,
            "error": "Gemini API key is not configured in the server's environment (.env).",
        }

    guidance = _LAYOUT_GUIDANCE.get(layout, _LAYOUT_GUIDANCE["simple"])
    model_name = "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

    full_prompt = (
        f"{_SYSTEM_INSTRUCTION}\n\n"
        f"Layout: {layout}. {guidance}\n"
        f"Brand color (for tone reference only): {brand_color}\n\n"
        f"User request:\n{prompt}"
    )

    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "subject": {"type": "STRING", "description": "Subject line, under 60 chars"},
                    "preheader": {"type": "STRING", "description": "Inbox preview text, 40-90 chars"},
                    "heading": {"type": "STRING", "description": "Main headline"},
                    "subheading": {"type": "STRING", "description": "Short eyebrow/label or sub-headline (may be empty)"},
                    "body": {"type": "STRING", "description": "2-4 short paragraphs separated by blank lines"},
                    "button_text": {"type": "STRING", "description": "Short CTA label"},
                    "button_url": {"type": "STRING", "description": "CTA url or 'https://' placeholder"},
                    "event_details": {"type": "STRING", "description": "Event logistics, one per line (event layout only, else empty)"},
                    "image_url": {"type": "STRING", "description": "Leave empty unless the user provides one"},
                    "footer": {"type": "STRING", "description": "Brief compliant footer line"},
                },
                "required": ["subject", "preheader", "heading", "body", "button_text", "footer"],
            },
        },
    }

    try:
        response = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )

        if response.status_code != 200:
            logger.error("Gemini API error (Status %s): %s", response.status_code, response.text)
            return {"success": False, "error": f"Gemini API error: status code {response.status_code}."}

        result = response.json()
        candidates = result.get("candidates", [])
        if not candidates:
            return {"success": False, "error": "No response was generated by the AI."}

        part_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
        data = json.loads(part_text)

        return {
            "success": True,
            "subject": data.get("subject", ""),
            "preheader": data.get("preheader", ""),
            "heading": data.get("heading", ""),
            "subheading": data.get("subheading", ""),
            "body": data.get("body", ""),
            "button_text": data.get("button_text", ""),
            "button_url": data.get("button_url") or "https://",
            "event_details": data.get("event_details", ""),
            "image_url": data.get("image_url", ""),
            "footer": data.get("footer", ""),
        }

    except json.JSONDecodeError:
        logger.error("Failed to decode JSON returned by Gemini API.")
        return {"success": False, "error": "AI response was not valid JSON. Please try again."}
    except Exception as exc:  # noqa: BLE001
        logger.error("Exception during AI content generation: %s", exc)
        return {"success": False, "error": f"AI generation failed: {exc}"}


# Backwards-compatible alias (older imports/tests may reference this name).
def generate_email_template_ai(prompt):
    return generate_email_content_ai(prompt, layout="simple")
