# Media Security: Serving Sensitive User Files

Status: implemented (2026-06-23)
Related: B-047

## Threat

Signature images and generated recipient documents are sensitive, user-owned
files. They are stored under `MEDIA_ROOT` (`signatures/originals/`,
`signatures/processed/`, `generated_documents/`). If those paths are served as
public static files, anyone who knows or guesses a URL can download them,
bypassing the application's per-user authorization. That is broken access
control, not a theoretical risk.

## How it is handled

1. Authenticated, ownership-checked serving. Sensitive files are delivered only
   through views that verify the requester owns the record:

   * `documents.views.signature_image` (`/documents/signatures/<pk>/image/<variant>/`)
     resolves `Signature.active` scoped to `user=request.user`, with `variant`
     whitelisted to `original`/`processed`.
   * `emails.views.recipient_document` (`/dashboard/recipients/<pk>/document/`)
     resolves `Recipient` scoped to `campaign__user=request.user`.
   * `documents.views.document_template_background`
     (`/documents/templates/<pk>/background/`) resolves `DocumentTemplate.active`
     scoped to `user=request.user` (B-049).

   Templates link to these named URLs, never to `FieldFile.url`.

2. Randomized stored filenames (defense in depth). `upload_to` callables store
   files as `<dir>/<random-hex><ext>` (see `documents.models.signature_*_upload_to`
   and `emails.models.generated_document_upload_to`), so a path cannot be guessed
   from the original filename and no recipient PII appears in the path.

3. Prompt deletion of sensitive files. Deleting a signature removes the image
   files from storage immediately (the soft-deleted row is retained for
   name-history and audit, but its file fields are cleared), rather than keeping
   the images for the 90-day purge window.

4. Encryption at rest (B-048). Signature images are stored Fernet-encrypted on
   disk via `core.storage.EncryptedFileSystemStorage`, keyed by
   `FILE_ENCRYPTION_KEY` (defaults to `SMTP_ENCRYPTION_KEY`). Encryption is
   transparent: the processing task, renderer, and serving views all read through
   `FieldFile.open()`, which decrypts. This is why item 1 matters - a static web
   server would return ciphertext, so these files must only be served through the
   views. To encrypt files written before this was enabled, run
   `python manage.py encrypt_signature_files` (idempotent).

## Required production configuration

`core/urls.py` serves `MEDIA_URL` through Django only when `DEBUG=True`, for local
development. In production:

* Do NOT configure the web server (nginx/Apache/etc.) to serve the sensitive
  subtrees `signatures/`, `generated_documents/` and `document_templates/`
  under `/media/` publicly. Serve only non-sensitive media (e.g. email logos,
  campaign attachments) publicly, or route everything through the app.
* Preferred hardening for large deployments: move sensitive files to a private
  storage backend (e.g. an S3 bucket with no public read) and have the serving
  views issue short-lived signed URLs, or stream via `X-Accel-Redirect` /
  `X-Sendfile` so the app keeps the authorization decision while the web server
  does the byte pushing.

## Tracked follow-ups

* `DocumentTemplate.template_file` is now served through the ownership-checked
  `documents.views.document_template_background` view (B-049, resolved
  2026-07-04); it remains unencrypted at rest, which is acceptable for
  low-sensitivity template backgrounds.
* Generated recipient documents (`Recipient.document_file`) are access-controlled
  and path-randomized but not encrypted at rest; they are also emailed as
  attachments, so encryption at rest offers less marginal protection there.
