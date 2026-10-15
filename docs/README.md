# EmailOPS Project Documentation

Welcome to the EmailOPS documentation hub. This index serves as a guide to help developers, administrators, and maintainers understand, run, and develop for the system.

---

## 🗺️ Documentation Directory

### 🏗️ System Design & Structure
- **[System Architecture](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/architecture.md)**: High-level overview of the Django/Celery/Redis stack, data flows, and design trade-offs.
- **[Folder & Module Breakdown](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/project-structure.md)**: File layout and responsibilities of the `core/`, `accounts/`, `emails/`, `certificates/`, `static/`, and `templates/` folders.
- **[Database Schema & Models](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/database.md)**: Bounding box coordinate models, SQLite structures, relations, soft-delete rules, and file cleanup signals.

### ⚙️ Setup & Operations
- **[Local Installation](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/setup-installation.md)**: Setting up python virtual environments using `uv`, starting Redis, executing migrations, running development servers, and launching Celery workers.
- **[Configurations & Environment](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/configuration.md)**: Environment variables (`SITE_URL`, `SMTP_ENCRYPTION_KEY`, etc.), dynamic CSRF origins, and setting overrides.
- **[Business Workflows](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/workflows.md)**: Step-by-step user guides covering SMTP testing, template designs, CSV imports, and campaign management.

### 🧩 Core Code Modules
- **[Backend Logic & Pipelines](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/backend.md)**: Background queues, SMTP connection retries with exponential backoffs, throttled delivery loops, Fernet credential encryption, and database concurrency locks.
- **[Frontend Standards](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/frontend.md)**: Theme-aware Bootstrap variables, dynamic preview forms, certificate boundary lines, and Microsoft Excel UTF-BOM CSV parser rules.
- **[Credentials & Authentication](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/authentication.md)**: Allauth credentials, Google Workspace OAuth2 SSO login setup, Turnstile CAPTCHA integration, and account linking logic.

### 🔌 Integrations & Testing
- **[Third-Party Integrations](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/integrations.md)**: Google Gemini AI REST endpoints, JSON validation schemas, layout constraints, Cloudflare Turnstile verification, and external provider connections.
- **[View Endpoints Reference](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/api-reference.md)**: Path maps, GET/POST payload parameters, and JSON views for templates, campaigns, track, and certificate pre-runs.
- **[Testing Strategies](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/testing.md)**: Eager Celery task configurations, mock outbox mail asserts, in-memory databases, and run commands.

### 🛠️ Maintenance & Guides
- **[Debugging & Troubleshooting](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/troubleshooting.md)**: Resolutions for common SMTP auth errors, port 587 cloud blocks, microscopic cert names, SSO constraints, and the `bugs.md` protocol.
- **[Engineering & Contribution Standards](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/development-guide.md)**: Contribution practices, soft-delete invariants, immutable campaigns, zero-recipient safeguards, and code quality expectations.
- **[Glossary & Concepts](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/docs/glossary.md)**: Domain terminology including "Bounding Box coordinates", "Fernet App Password Encryption", "Stale Reaper", and "Open Pixels".
