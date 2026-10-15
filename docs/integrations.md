# Integrations

This document describes the external services integrated with EmailOPS and their communication workflows.

---

## 1. Google Gemini AI API

EmailOPS integrates with **Google Gemini AI** (`gemini-2.5-flash`) via its REST API to generate structured marketing copy for campaigns.

```
[User prompt] ──> [Apply Layout Guidance] ──> [Assemble JSON Payload + Schema]
                                                        │
                                                        ▼
                                        POST /models/gemini-2.5-flash:generateContent
                                                        │
                                                        ▼
[Populate Builder Form] <── [Verify JSON Fields] <── [API Response]
```

### Layout-Aware Guidance
Instead of asking the model to write raw HTML (which can break responsive layouts in strict email clients like Outlook), EmailOPS requests structured copy to fit within pre-designed, email-safe templates. The request guides the model based on the selected layout style:
- **`simple`**: Warm, concise outreach copy with a single clear call-to-action (CTA).
- **`announcement`**: Headline copy paired with a short uppercase eyebrow label.
- **`newsletter`**: Newsletter articles containing 2 to 3 short paragraphs.
- **`promotion`**: Direct, urgent promotional copy centered around a specific deal label.
- **`event`**: Structured invitation copy separating event logistics (date, location, time) onto separate lines.

### JSON Schema Verification
The API payload defines a JSON schema to ensure the model responds with structured JSON that maps to our database fields:
```json
{
    "type": "OBJECT",
    "properties": {
        "subject": {"type": "STRING"},
        "preheader": {"type": "STRING"},
        "heading": {"type": "STRING"},
        "subheading": {"type": "STRING"},
        "body": {"type": "STRING"},
        "button_text": {"type": "STRING"},
        "button_url": {"type": "STRING"},
        "event_details": {"type": "STRING"},
        "image_url": {"type": "STRING"},
        "footer": {"type": "STRING"}
    },
    "required": ["subject", "preheader", "heading", "body", "button_text", "footer"]
}
```
This forces the model to return structured data rather than free-form text or Markdown, enabling the system to parse the response and populate the builder form programmatically.

---

## 2. Cloudflare Turnstile

Cloudflare Turnstile is used to protect authentication routes against automated bot traffic.

- **Frontend Integration**: Displays a Turnstile widget dynamically on login and registration pages using the `TURNSTILE_SITE_KEY`.
- **Backend Verification**: Outbound API request to:
  `https://challenges.cloudflare.com/turnstile/v0/siteverify`
  Verifies the widget token before processing the form.

---

## 3. Google Workspace Authentication (OAuth2)

OAuth2 integration handles single sign-on flows.

- **Authentication Flow**: Authenticates users using `django-allauth`.
- **Backend Settings**:
  - Redirects users directly to Google's consent screen.
  - Links incoming verified Google profiles to existing local records if the email addresses match.
