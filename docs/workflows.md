# Business Workflows & User Journeys

This document outlines the step-by-step user journeys and operational workflows of EmailOPS.

---

## 1. Setting Up SMTP Configurations

To send email campaigns, you must first register an SMTP account profile.

```
[Accounts Dashboard] ──> [Add SMTP Configuration]
                                │
                                ▼
                       [Input Host & Port]
                       [Input Gmail Address]
                       [Input Google App Password]
                                │
                                ▼
                       [Click Test Connection] ──> Connection OK?
                                │                       │
                                ├──[No]                 ├──[Yes]
                                │                       │
                                ▼                       ▼
                        Show connection error        Save SMTP config
```

### Steps
1. **Navigate to SMTP Configs**: From the sidebar menu, select **SMTP Settings**.
2. **Add Configuration**: Click **Add Config**.
3. **App Password Generation**: Do not use your primary Google Account password. Navigate to your Google Account Settings -> Security -> App Passwords, generate a 16-character code, and paste it into the **Gmail App Password** field.
4. **Test Connection**: Enter a recipient address and click **Test Connection** to send a test email.
5. **Save**: Set the configuration as active and save it.

---

## 2. Configuring Certificate Layouts

If your campaigns distribute certificates, you must configure naming coordinate alignments on the template.

1. **Upload Background**: Select **Certificates** -> **New Template**, upload your base certificate image (PNG/JPG), and save it.
2. **Define Coordinates**:
   - Locate the name placement region on your background image.
   - Enter `X Start` and `X End` (horizontal boundaries). The system centers names within this region.
   - Enter `Y Coordinate` (vertical baseline).
3. **Verify Bounds**: Review the live dotted line overlay on the template details page to ensure alignment constraints are correct.

---

## 3. Creating Email Templates

Email templates define the visual layout and copy variables of your emails.

1. **Select Layout**: Select **Templates** -> **New Template** and choose a layout style (`simple`, `announcement`, `newsletter`, `promotion`, `event`).
2. **Configure Fields**: Fill out headers, buttons, and footers. Use template tags (like `{{ name }}`) for personalization.
3. **AI Copy Generation** (Optional): Click the **AI Assistant** tab, input a brief description of your email's goal, and click **Generate**. The server calls Gemini to populate the builder fields with structured copy.
4. **Review Previews**: Observe the right-hand preview frame to ensure the rendering is correct.

---

## 4. Managing Campaigns

Campaigns coordinate templates, recipient lists, and dispatches.

```
[Create Campaign] ──> [Select SMTP & Template] ──> [Import Recipients (CSV / Text)]
                                                           │
                                                           ▼
                                                  [Queue Campaign]
                                                           │
                                                           ▼
                                            [Celery background processing]
                                            - Generates certificate PDFs
                                            - Sends emails in throttled batches
                                            - Updates stats in real time
```

### A. Setup & Import
1. **Details**: Select **Campaigns** -> **New Campaign**, select an SMTP configuration, select an email template, and set the **Is Certificate Campaign** flag if applicable.
2. **Recipients Import**:
   - **CSV Import**: Upload a CSV file containing an email column, name column, and any optional merge columns.
   - **Quick Import**: Copy and paste raw email addresses.

### B. Execution
- Click **Start Sending**. The campaign transitions to the `queued` status, and the server triggers Celery.
- If it is a certificate campaign, the Celery worker generates the PDF certificates first, attaching them to each recipient profile.
- Once certificates are ready, the sending pipeline begins. Celery opens an SMTP connection, renders the templates with the recipient's variables, attaches the generated certificates, and sends the emails at `1.5`s intervals.
- The UI polls progress from the campaign status API, rendering delivery indicators and recipient logs in real time.
- If a dispatch fails, the sender retries the email up to 3 times before recording the error message.
