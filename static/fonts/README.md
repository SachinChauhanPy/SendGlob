# Certificate fonts

Certificate name rendering uses a scalable TrueType font resolved at runtime
(see `certificates/utils.py`). Resolution order:

1. The font uploaded with the certificate template (`font_file`).
2. A font vendored here in `static/fonts/` — `PlayfairDisplay-Bold.ttf` by
   name, or any `.ttf` found in this directory.
3. A common system font (DejaVu / Liberation / Arial / Times).

To ship the intended premium look, download the OFL-licensed
`PlayfairDisplay-Bold.ttf` from Google Fonts and place it in this directory.
The previous runtime HTTPS download was removed (B-006): it blocked the Celery
worker, failed on slow/air-gapped hosts, and silently fell back to a fixed
tiny font that ignored the configured size.
