"""
Rendering engine for personalized documents.

Each ``DocumentTextField`` is drawn onto the template background at its own
coordinates, alignment, font and size.
"""
import io
import os
import uuid
import logging
import platform

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Font discovery helpers
# ---------------------------------------------------------------------------

def get_bundled_font_path():
    """Return the path to the project-bundled TrueType font, or None."""
    candidates = [
        os.path.join(settings.BASE_DIR, 'static', 'fonts', 'DejaVuSans.ttf'),
        os.path.join(settings.BASE_DIR, 'static', 'fonts', 'Roboto-Regular.ttf'),
        os.path.join(settings.BASE_DIR, 'static', 'fonts', 'OpenSans-Regular.ttf'),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def get_system_font_path():
    """Return the path to a readable system TrueType font, or None."""
    system = platform.system()
    if system == 'Windows':
        candidates = [
            r'C:\Windows\Fonts\arial.ttf',
            r'C:\Windows\Fonts\calibri.ttf',
            r'C:\Windows\Fonts\verdana.ttf',
        ]
    elif system == 'Darwin':
        candidates = [
            '/Library/Fonts/Arial.ttf',
            '/System/Library/Fonts/Helvetica.ttc',
            '/Library/Fonts/Verdana.ttf',
        ]
    else:
        candidates = [
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
        ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _measure_text_width(draw, text, font, font_size):
    """Return the rendered pixel width of ``text`` with ``font``."""
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]
    except AttributeError:
        # Pillow < 8.0 fallback
        return draw.textlength(text, font=font)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _load_scalable_font(font_file, font_size):
    """
    Load a size-respecting TrueType font, trying the field's own uploaded font,
    then the bundled project font, then a system font. Returns
    ``(font, font_bytes, fallback_path, is_scalable)`` so callers can cheaply
    re-instantiate the font at smaller sizes during autoscaling.
    """
    font_bytes = None
    if font_file:
        try:
            with font_file.open('rb') as fh:
                font_bytes = fh.read()
            return ImageFont.truetype(io.BytesIO(font_bytes), font_size), font_bytes, None, True
        except Exception as e:
            logger.warning(f"Could not load field font_file: {e}")
            font_bytes = None

    fallback_path = get_bundled_font_path() or get_system_font_path()
    if fallback_path:
        try:
            return ImageFont.truetype(fallback_path, font_size), None, fallback_path, True
        except Exception as e:
            logger.warning(f"Could not load font '{fallback_path}': {e}")

    logger.error(
        "No scalable TrueType font available for document rendering; text will "
        "render at Pillow's fixed default size. Vendor a .ttf into static/fonts/."
    )
    return ImageFont.load_default(), None, None, False


def _render_field(draw, text_field, value, font_file, font_size, font_color, alignment):
    """Draw ``value`` for one field onto ``draw`` using the supplied typography
    (font/size/color/alignment), honoring autoscale-to-fit."""
    if value is None:
        value = ''
    value = str(value)
    if not value:
        return

    max_w = text_field.x_end - text_field.x_start
    font, font_bytes, fallback_path, is_scalable = _load_scalable_font(
        font_file, font_size,
    )

    def _reinstantiate(size):
        if font_bytes is not None:
            return ImageFont.truetype(io.BytesIO(font_bytes), size)
        if fallback_path:
            return ImageFont.truetype(fallback_path, size)
        return font

    if is_scalable and max_w > 0:
        try:
            text_w = _measure_text_width(draw, value, font, font_size)
            while text_w > max_w and font_size > 12:
                font_size -= 2
                font = _reinstantiate(font_size)
                text_w = _measure_text_width(draw, value, font, font_size)
        except Exception:
            pass

    text_w = _measure_text_width(draw, value, font, font_size)

    if alignment == 'left':
        target_x = text_field.x_start
    elif alignment == 'right':
        target_x = text_field.x_end - text_w
    else:  # center (default)
        target_x = text_field.x_start + (max_w - text_w) / 2

    draw.text((target_x, text_field.y_coordinate), value, font=font, fill=font_color)


def _render_signature(base_img, sig_field, signature):
    """
    Composite a ready ``Signature`` onto ``base_img`` inside the field's box.
    """
    if signature is None or not signature.is_ready:
        return

    try:
        with signature.processed_image.open('rb') as fh:
            sig = Image.open(io.BytesIO(fh.read()))
            sig.load()
    except Exception as e:
        logger.error(f"Could not open processed signature {signature.pk}: {e}")
        return

    if sig.mode != 'RGBA':
        sig = sig.convert('RGBA')

    box_w = max(1, sig_field.x_end - sig_field.x_start)
    max_h = max(1, sig_field.max_height)
    scale = min(box_w / sig.width, max_h / sig.height)
    if scale <= 0:
        scale = 1.0
    new_w = max(1, int(round(sig.width * scale)))
    new_h = max(1, int(round(sig.height * scale)))
    sig = sig.resize((new_w, new_h), Image.LANCZOS)

    if sig_field.alignment == 'left':
        target_x = sig_field.x_start
    elif sig_field.alignment == 'right':
        target_x = sig_field.x_end - new_w
    else:  # center
        target_x = sig_field.x_start + (box_w - new_w) // 2

    base_img.paste(sig, (int(target_x), int(sig_field.y_top)), mask=sig)


def generate_document_image(template, values, signatures=None, placeholder_signatures=False,
                            text_fields=None, signature_fields=None):
    """
    Render every text field of ``template`` onto its background image, then
    composite any per-recipient signatures.

    ``text_fields``/``signature_fields`` accept pre-fetched field lists so bulk
    callers avoid re-querying the same fixed template configuration per
    recipient (B-054); when omitted, the fields are queried from the template.
    """
    with template.template_file.open('rb') as fh:
        img = Image.open(io.BytesIO(fh.read()))
        img.load()

    if text_fields is None:
        text_fields = list(template.text_fields.all().order_by('id'))

    draw = ImageDraw.Draw(img)
    use_global = getattr(template, 'use_global_typography', False)
    for text_field in text_fields:
        if use_global:
            font_file = template.global_font_file
            font_size = template.global_font_size
            font_color = template.global_font_color
            alignment = template.global_alignment
        else:
            font_file = text_field.font_file
            font_size = text_field.font_size
            font_color = text_field.font_color
            alignment = text_field.alignment
        _render_field(
            draw, text_field, values.get(text_field.field_name, ''),
            font_file, font_size, font_color, alignment,
        )

    if signature_fields is not None:
        sig_fields = signature_fields
    else:
        sig_fields = list(template.signature_fields.all()) if hasattr(template, 'signature_fields') else []

    if sig_fields and placeholder_signatures:
        for sf in sig_fields:
            draw.rectangle(
                [sf.x_start, sf.y_top, sf.x_end, sf.y_top + sf.max_height],
                outline='#6c757d', width=2,
            )
            draw.text((sf.x_start + 4, sf.y_top + 4), sf.label or sf.source_column, fill='#6c757d')
    elif sig_fields and signatures:
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        for sf in sig_fields:
            _render_signature(img, sf, signatures.get(sf.id))

    return img


def generate_document_bytes(template, values, signatures=None,
                            text_fields=None, signature_fields=None):
    """
    Render the document and return ``(bytes, extension)`` in the template's
    configured output format ('pdf' or 'image'/PNG).
    """
    img = generate_document_image(
        template, values, signatures=signatures,
        text_fields=text_fields, signature_fields=signature_fields,
    )

    if template.output_format == template.OUTPUT_FORMAT_PDF:
        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
            bg = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'RGBA':
                mask = img.split()[3]
            elif img.mode == 'LA':
                mask = img.split()[1]
            else:
                mask = None
            bg.paste(img, mask=mask)
            img = bg
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        out = io.BytesIO()
        img.save(out, format='PDF', resolution=100.0)
        return out.getvalue(), 'pdf'

    out = io.BytesIO()
    img.save(out, format='PNG')
    return out.getvalue(), 'png'


def _safe_name(name):
    clean = "".join(c for c in str(name) if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    return f"{clean or 'document'}_{uuid.uuid4().hex[:8]}"


def resolve_values(recipient, template, column_mapping=None, text_fields=None):
    """
    Build the ``field_name -> value`` map for a recipient.
    """
    column_mapping = column_mapping or {}
    context = recipient.context_data or {}
    values = {}
    if text_fields is None:
        text_fields = template.text_fields.all()
    for text_field in text_fields:
        source = column_mapping.get(text_field.field_name, text_field.field_name)
        if source in context:
            values[text_field.field_name] = context.get(source, '')
        elif text_field.field_name == 'name':
            values[text_field.field_name] = recipient.name
        else:
            values[text_field.field_name] = context.get(text_field.field_name, '')
    return values


def resolve_signatures(recipient, template, user, signature_fields=None, signature_cache=None):
    """
    Build the ``signature_field_id -> Signature`` map for a recipient.

    ``signature_cache`` is an optional dict shared across a bulk run mapping a
    lowercased signature name to its resolved ``Signature`` (or None), so the
    same name referenced by many recipients is looked up once (B-054).
    """
    from documents.models import Signature  # local import avoids a cycle

    context = recipient.context_data or {}
    if signature_fields is None:
        signature_fields = template.signature_fields.all()
    resolved = {}
    for sig_field in signature_fields:
        ref = str(context.get(sig_field.source_column, '') or '').strip()
        signature = None
        if ref:
            cache_key = ref.lower()
            if signature_cache is not None and cache_key in signature_cache:
                signature = signature_cache[cache_key]
            else:
                signature = (
                    Signature.active
                    .filter(user=user, name__iexact=ref)
                    .first()
                )
                if signature is not None and not signature.is_ready:
                    signature = None
                if signature_cache is not None:
                    signature_cache[cache_key] = signature
        resolved[sig_field.id] = signature
    return resolved


def save_document_for_recipient(recipient, template, column_mapping=None, user=None,
                                text_fields=None, signature_fields=None, signature_cache=None):
    """Generate the document for a recipient and save it to ``document_file``.

    Pre-fetched ``text_fields``/``signature_fields`` and a shared
    ``signature_cache`` may be supplied by bulk callers to avoid per-recipient
    re-queries of fixed template configuration (B-054).
    """
    values = resolve_values(recipient, template, column_mapping, text_fields=text_fields)
    owner = user or template.user
    signatures = resolve_signatures(
        recipient, template, owner,
        signature_fields=signature_fields, signature_cache=signature_cache,
    )
    content, ext = generate_document_bytes(
        template, values, signatures=signatures,
        text_fields=text_fields, signature_fields=signature_fields,
    )
    filename = f"doc_{recipient.id}_{_safe_name(recipient.name)}.{ext}"
    recipient.document_file.save(filename, ContentFile(content), save=True)
