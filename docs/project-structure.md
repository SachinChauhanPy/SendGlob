# Project Structure

This document breaks down the organization of the EmailOPS codebase, detailing the responsibilities of each module and directory.

---

## Directory Map

```
EmailOPS/
├── .env.example             # Template for configuration environment variables
├── pyproject.toml           # Dependency specifications managed by uv
├── manage.py                # Django administrative command-line interface
├── db.sqlite3               # Local SQLite database file (ignored in git)
├── core/                    # Infrastructure and project settings
├── accounts/                # User authentication and SMTP profiles
├── emails/                  # Campaign management and delivery mechanics
├── certificates/            # Certificate generation and Pillow configurations
├── static/                  # Shared stylesheet assets, scripts, and fonts
└── templates/               # Django templates hierarchy
```

---

## Module Breakdown

### 1. `core/` (System Core)
Contains files responsible for systemic startup, middlewares, settings, and routing.

- **[settings.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/core/settings.py)**: Configuration settings for security, databases, authentication backends (`allauth`), email backends, Celery, and custom cryptography keys.
- **[urls.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/core/urls.py)**: The root URL configuration mapping requests to various sub-applications (`/dashboard/`, `/accounts/`, `/certificates/`).
- **[celery.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/core/celery.py)**: Startup entrypoint configuring celery to look for async tasks in the Django applications.
- **[models.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/core/models.py)**: Defines the central `SoftDeleteModel`, `SoftDeleteManager`, and `SoftDeleteQuerySet` which implement soft-deletion across all critical models.
- **[pricing.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/core/pricing.py)**: Pricing calculations based on Cloudflare geography headers or default fallbacks.
- **[views.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/core/views.py)**: Core landing redirect logic and custom template views for handling global HTTP error statuses.

---

### 2. `accounts/` (Authentication & Credentials)
Manages registration, session state, and user-provided credentials encryption.

- **[models.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/accounts/models.py)**:
  - `CustomUser`: User model overrides using email as the login identifier.
  - `SMTPConfig`: SMTP settings storage with symmetric cryptography functions (`set_app_password`, `get_app_password`).
- **[forms.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/accounts/forms.py)**: Captcha-enabled forms verifying Cloudflare Turnstile responses on login, registration, and password reset flows.
- **[utils.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/accounts/utils.py)**: Requests-based Turnstile validation checker.
- **[views.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/accounts/views.py)**: Standard CRUD operations for SMTP configurations, including testing mail connections.

---

### 3. `emails/` (Email Campaigns & Generation)
Tracks and handles mass email campaigns, HTML content builders, AI copy assistance, and dispatch pipelines.

- **[models.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/emails/models.py)**: Campaign representations (`EmailCampaign`), content templates (`EmailTemplate`), attachments (`CampaignAttachment`), and recipient records (`Recipient`).
- **[tasks.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/emails/tasks.py)**: Celery task loops executing bulk dispatches, throttling intervals, connection retries, and recovering stale tasks from defunct worker containers.
- **[email_designs.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/emails/email_designs.py)**: Responsive layout system rendering structured JSON to tables with inline styling (supports `simple`, `announcement`, `newsletter`, `promotion`, `event`, and `custom` layouts).
- **[ai.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/emails/ai.py)**: Interfaces with the Google Gemini API to write structured email copies within layout boundaries.
- **[views.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/emails/views.py)**: View logic managing dashboards, campaign lists, recipient updates, template builders, previews, and tracking pixel triggers.

---

### 4. `certificates/` (Certificate Generation)
Handles coordinates-driven Pillow rendering tasks drawing names dynamically on template images.

- **[models.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/certificates/models.py)**: Bounding box data model storage (`CertificateTemplate`) outlining X/Y coordinate lines, fonts, and colors.
- **[utils.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/certificates/utils.py)**: Bounding-box text wrapping, autoscaling fonts to fit within boxes, and PDF output rendering.
- **[tasks.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/certificates/tasks.py)**: CPU-intensive worker loops generating PDF certificates and saving them to the recipient DB.
- **[views.py](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/certificates/views.py)**: Previews of canvas boundaries and interactive template management tools.

---

### 5. `templates/` & `static/` (Frontend Layer)
Shared frontend resources defining design guidelines.

- **`templates/`**:
  - `layouts/`: Master skeleton layout (`base.html`) containing the offcanvas sidebar.
  - `accounts/`, `emails/`, `certificates/`: Sub-folders matching app templates.
- **`static/`**:
  - `css/style.css`: Contains CSS variables, light/dark responsive utility sheens, and sidebar styling overrides.
  - `fonts/`: OFL-licensed TrueType fonts (like `PlayfairDisplay-Bold.ttf`) vendored directly inside the project to allow reliable offline rendering.
