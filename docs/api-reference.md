# API Reference

This document maps the URL routing paths and HTTP endpoint actions available in EmailOPS.

---

## 1. Routing Context & App Mounts

Root routing is defined in [core/urls.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/core/urls.py). The landing marketing page is mapped to the root path (`/`), and sub-applications are mounted under dedicated prefixes:

- `/dashboard/`: Email campaigns, templates, and analytics tracking (maps to `emails.urls`).
- `/accounts/`: User profile management, password updates, and SMTP configurations (maps to `accounts.urls`).
- `/accounts/` (OAuth integration): Shared mount path with allauth for authentication routing (`allauth.urls`).
- `/certificates/`: Certificate creation, Preview coordinate checks, and templates (maps to `certificates.urls`).

---

## 2. Accounts API & SMTP Configurations (`/accounts/`)

SMTP profiles are managed under the accounts namespace:

- **`GET /accounts/smtp/`** (Name: `accounts:smtp_list`): Lists the active SMTP configurations for the logged-in user.
- **`GET /accounts/smtp/new/`** / **`POST /accounts/smtp/new/`** (Name: `accounts:smtp_create`): Renders and handles the creation of a new SMTP configuration.
- **`GET /accounts/smtp/<id>/edit/`** / **`POST /accounts/smtp/<id>/edit/`** (Name: `accounts:smtp_edit`): Edits an existing configuration. Blocked if the configuration is in use by a campaign in the `queued` or `sending` state.
- **`POST /accounts/smtp/<id>/delete/`** (Name: `accounts:smtp_delete`): Soft-deletes a configuration. Blocked if referenced by a campaign in the `queued` or `sending` state.
- **`POST /accounts/smtp/<id>/test/`** (Name: `accounts:smtp_test`): Outbound connection test. Expects a POST payload with a `test_email` address.
  - **Response (Success)**:
    ```json
    {"success": true, "message": "SMTP Connection successful! Test email sent to recipient."}
    ```
  - **Response (Failure)**:
    ```json
    {"success": false, "message": "SMTP connection failed: <error details>"}
    ```

---

## 3. Campaigns & Analytics API (`/dashboard/`)

Campaign and template endpoints are managed under the emails dashboard namespace:

- **`GET /dashboard/`** (Name: `emails:dashboard`): Renders the main user dashboard with campaign statistics, open rates, and activity charts.
- **`GET /dashboard/campaigns/`** (Name: `emails:campaign_list`): Lists campaigns.
- **`POST /dashboard/campaigns/new/`** (Name: `emails:campaign_create`): Creates a campaign and parses recipient inputs (CSV uploads or copy-pasted text).
- **`GET /dashboard/campaigns/<id>/`** (Name: `emails:campaign_detail`): Shows campaign metadata, delivery progress, and a list of target recipients.
- **`GET /dashboard/campaigns/<id>/status/`** (Name: `emails:campaign_status_api`): AJAX polling endpoint returning current campaign progress stats.
  - **Response**:
    ```json
    {
      "status": "sending",
      "total": 100,
      "sent": 45,
      "failed": 2,
      "progress": 47,
      "breakdown": {
        "sent": 45,
        "failed": 2,
        "sending": 10,
        "pending": 43
      }
    }
    ```
- **`POST /dashboard/campaigns/<id>/queue/`** (Name: `emails:campaign_queue`): Queues a campaign for background dispatch.
- **`POST /dashboard/campaigns/<id>/cancel/`** (Name: `emails:campaign_cancel`): Cancels a campaign. This halts future recipient batch dispatches.
- **`POST /dashboard/campaigns/<id>/restart/`** (Name: `emails:campaign_restart`): Resets recipient delivery attempts, deletes generated certificates, resets statistics, and queues the campaign again.
- **`POST /dashboard/campaigns/<id>/delete/`** (Name: `emails:campaign_delete`): Soft-deletes a campaign.

### Analytics Tracking Pixel
- **`GET /dashboard/track/<uuid:tracking_uuid>/pixel.png`** (Name: `emails:email_open_track`): A public endpoint that serves a 1x1 transparent GIF (`image/gif`). When loaded, it updates the corresponding recipient's `opened_at` field in the database if it hasn't been set.

---

## 4. Templates API

Templates are managed under the templates dashboard namespace:

- **`GET /dashboard/templates/`** (Name: `emails:template_list`): Lists templates.
- **`GET /dashboard/templates/new/`** / **`POST /dashboard/templates/new/`** (Name: `emails:template_create`): Creates templates.
- **`GET /dashboard/templates/<id>/edit/`** / **`POST /dashboard/templates/<id>/edit/`** (Name: `emails:template_edit`): Edits a template. Blocked if the template is in use by a campaign in the `queued` or `sending` state.
- **`POST /dashboard/templates/<id>/delete/`** (Name: `emails:template_delete`): Soft-deletes a template. Blocked if the template is in use by active campaigns.
- **`POST /dashboard/templates/<id>/clone/`** (Name: `emails:template_clone`): Clones a template to let users build off its configuration.
- **`POST /dashboard/templates/preview/`** (Name: `emails:template_preview`): Renders template builder inputs to HTML for an iframe preview.
- **`POST /dashboard/templates/generate-ai/`** (Name: `emails:template_generate_ai`): AJAX endpoint that accepts a prompt and returns structured copy generated by Gemini AI.
  - **Response**:
    ```json
    {
      "success": true,
      "subject": "Example Subject",
      "preheader": "Inbox Preview Summary",
      "heading": "Headline Copy",
      "body": "Paragraph 1\n\nParagraph 2",
      "button_text": "Action CTA",
      "button_url": "https://example.com"
    }
    ```

---

## 5. Certificates API (`/certificates/`)

Certificate configurations are managed under the certificates namespace:

- **`GET /certificates/`** (Name: `certificates:certificate_list`): Lists templates.
- **`GET /certificates/new/`** / **`POST /certificates/new/`** (Name: `certificates:certificate_create`): Renders and handles template uploads.
- **`GET /certificates/<id>/edit/`** / **`POST /certificates/<id>/edit/`** (Name: `certificates:certificate_edit`): Edits coordinates, template details, or font files.
- **`POST /certificates/<id>/delete/`** (Name: `certificates:certificate_delete`): Soft-deletes certificate templates.
- **`GET /certificates/<id>/preview/`** (Name: `certificates:certificate_preview`): Generates a PNG preview of the background image with bounding box coordinate overlays.
