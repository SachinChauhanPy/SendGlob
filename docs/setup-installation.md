# Setup & Installation Guide

This document describes how to set up the EmailOPS application in a local development environment.

---

## System Prerequisites

Before configuring EmailOPS, make sure the following dependencies are installed:

1. **Python 3.13+**: Ensure your system environment has Python 3.13 or newer active.
2. **uv Package Manager**: EmailOPS manages dependencies using `uv` for fast, reproducible environment compilation. Install it via:
   - **Windows (PowerShell)**:
     ```powershell
     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
     ```
   - **macOS / Linux**:
     ```bash
     curl -LsSf https://astral.sh/uv/install.sh | sh
     ```
3. **Redis Broker**: Redis acts as the message broker for background task scheduling.
   - **macOS**: `brew install redis`
   - **Linux**: `sudo apt install redis-server`
   - **Windows**: Install Redis via WSL (Ubuntu) or download pre-compiled Windows binaries.

---

## Installation Steps

### 1. Clone the Codebase
Navigate to your desired workspace and open a shell terminal.

### 2. Configure Environment Secrets
Copy the example environment configuration file to create your local active environment file:
```bash
cp .env.example .env
```
Open `.env` and fill out the following keys:
- `SECRET_KEY`: A unique random key for Django session security.
- `SMTP_ENCRYPTION_KEY`: Set a 32-byte URL-safe base64 key for encrypting credentials. You can generate one via:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- `GEMINI_API_KEY`: Input your API key from Google AI Studio if using the AI copy generator.
- `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY`: Default test keys are preconfigured. These bypass verification checks (always returning success) for local development.

### 3. Sync Dependencies
Compile the virtual environment and fetch requirements using `uv`:
```bash
uv sync
```
This builds an isolated virtual environment (`.venv/`) and locks all dependencies based on [uv.lock](file:///C:/Users/ASUS/Desktop/WorkTree/EmailOPS/uv.lock).

### 4. Run Migrations
Generate the SQLite database and create model tables:
```bash
uv run python manage.py migrate
```

### 5. Create administrative superuser
Create an admin user account to access the administrative dashboard:
```bash
uv run python manage.py createsuperuser
```

---

## Running the Application

To run the complete system, you must start the web server, the Redis broker, and the Celery worker concurrently.

### A. Start the Redis Broker
Make sure the Redis service is active:
```bash
# macOS/Linux
redis-server

# Windows (WSL)
sudo service redis-server start
```

### B. Run the Development Server
Execute the Django development server:
```bash
uv run python manage.py runserver
```
Visit the landing page in your browser at `http://127.0.0.1:8000/`.

### C. Run the Celery Worker Daemon
In a separate terminal shell inside the project root, launch the Celery task queue listener:
```bash
# Windows (requires solo pool execution to prevent concurrency errors)
uv run celery -A core worker -l info --pool=solo

# macOS / Linux
uv run celery -A core worker -l info
```

---

## Verifying Setup

Ensure your local installation is functioning correctly by running the unit test suite:
```bash
uv run python manage.py test
```
If the environment is set up correctly, all tests will complete with an `OK` status.
