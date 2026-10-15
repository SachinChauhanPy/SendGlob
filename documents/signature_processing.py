"""
Signature-specialized background removal.

This module turns a photographed or scanned signature into a clean transparent
PNG. It is deliberately a *deterministic classical computer-vision* pipeline
rather than a general ML matting model: for high-contrast line art (pen on
paper) a tuned threshold pipeline is more reliable than learned saliency, which
softens thin strokes and is non-deterministic. Determinism matters for a tool
users are meant to trust — the same photo always yields the same signature.

Pipeline (see ``process_signature_bytes``):

    1. Decode, fix EXIF orientation, bound size.
    2. Illumination normalization (flat-field) — removes shadows / uneven phone
       lighting / paper tint, the single biggest reliability win on real photos.
    3. Colour-aware ink detection — a pixel is ink if it is locally dark
       (Sauvola adaptive threshold) OR clearly coloured (saturated blue/black
       pen). The colour term captures ink a luminance-only test would drop.
    4. Connected-component denoise — drop specks (dust, JPEG noise).
    5. Seal strokes — morphological close + hole-fill so interiors are fully
       opaque (no transparent pin-holes inside the signature).
    6. Validation — a lenient photo guard: reject images with no dominant paper
       background (photos/selfies); signatures on paper always pass.
    7. Alpha matte — solid interior, Gaussian-feathered outer edge for clean
       anti-aliasing without eroding strokes.
    8. Colour handling (preserve / single colour) and auto-crop with a margin.

Only NumPy + SciPy + Pillow are used; no heavy/ML dependency is required.
"""
import io
import logging
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage

logger = logging.getLogger(__name__)


class SignatureProcessingError(Exception):
    """Raised when an image cannot be turned into a usable signature."""


@dataclass(frozen=True)
class SignatureConfig:
    """Centralized, testable tuning for the pipeline."""

    # Cap the working resolution to bound memory/CPU. The processed matte is
    # produced at this working size; signatures rarely need more detail.
    max_dimension: int = 2400
    # Background (paper) estimate footprint, as a fraction of the smaller image
    # dimension. Large enough to span strokes, small enough to track shadows.
    illumination_kernel_frac: float = 0.08
    # Sauvola local-threshold window (odd px) and sensitivity.
    sauvola_window: int = 25
    sauvola_k: float = 0.18
    sauvola_r: float = 128.0
    # Drop connected components smaller than this fraction of total pixels.
    # Kept small so genuine tiny marks (an i-dot, an accent) survive — only
    # true specks/JPEG noise are removed.
    min_component_area_frac: float = 0.000008
    # Transparent padding (px) kept around the cropped signature.
    crop_margin: int = 14
    # Sanity band for ink coverage. Outside it the photo is effectively blank
    # (over-exposed) or near-solid (under-exposed) and is rejected.
    min_ink_coverage: float = 0.0006
    max_ink_coverage: float = 0.60
    # Color-aware ink detection. A pixel counts as ink if it is locally dark
    # (Sauvola) OR clearly *coloured* — saturated ink (blue/black pen) that a
    # luminance-only test would miss. Chroma = (max-min channel)/255.
    chroma_min: float = 0.10
    color_dark_min: float = 0.12
    # Mask cleanup + matte. Strokes are sealed (closing + hole-fill) so their
    # interiors are fully opaque (no pin-holes), and only the outer edge is
    # feathered for clean anti-aliasing.
    close_iter: int = 1
    edge_feather_sigma: float = 0.8
    # Only enclosed holes smaller than this fraction of the image are filled
    # (noise pin-holes). Large gaps enclosed by signature loops must stay
    # transparent, so they are never filled.
    max_hole_frac: float = 0.00006
    # Non-signature (photo) guard. A signature lives on a plain page, so paper
    # is the dominant colour by a wide margin: clean white paper bins at ~0.95
    # and even tinted/ruled paper stays ~0.93. A photo/selfie/screenshot has no
    # dominant background and its largest colour bin sits far lower (~0.16). The
    # threshold is set at the midpoint of that gap so paper must be the majority
    # of the image to pass; this rejects busy photos while keeping ample headroom
    # for genuine signatures on used or unevenly lit paper.
    min_paper_fraction: float = 0.5


DEFAULT_CONFIG = SignatureConfig()

# Guard against decompression-bomb DoS on user uploads.
Image.MAX_IMAGE_PIXELS = 64_000_000


def _hex_to_rgb(value):
    """Parse '#rrggbb' / '#rgb' / 'rgb(r,g,b)' into an (r, g, b) tuple."""
    value = (value or "#000000").strip()
    if value.startswith("rgb"):
        nums = value[value.find("(") + 1: value.find(")")].split(",")
        try:
            return tuple(int(float(n)) for n in nums[:3])
        except (ValueError, IndexError):
            return (0, 0, 0)
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        return (0, 0, 0)
    try:
        return tuple(int(value[i: i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (0, 0, 0)


def _load_rgb(raw_bytes, cfg):
    """Decode bytes to an EXIF-corrected, size-bounded RGB ``Image``."""
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img.load()
    except Exception as exc:  # noqa: BLE001 - surface as a domain error
        raise SignatureProcessingError(f"Could not read image: {exc}") from exc

    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")

    longest = max(img.size)
    if longest > cfg.max_dimension:
        scale = cfg.max_dimension / longest
        new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        img = img.resize(new_size, Image.LANCZOS)
    return img


def _illumination_normalized(gray, cfg):
    """
    Flat-field correction. Estimate the bright paper background with a large
    maximum filter, then divide it out. Returns an *ink probability* map in
    [0, 1] where higher means darker-than-paper (more likely ink).
    """
    k = max(3, int(min(gray.shape) * cfg.illumination_kernel_frac) | 1)
    background = ndimage.maximum_filter(gray, size=k)
    background = ndimage.uniform_filter(background, size=k)  # smooth the estimate
    flat = gray / np.maximum(background, 1.0)
    flat = np.clip(flat, 0.0, 1.0)
    return 1.0 - flat


def _sauvola_mask(gray, cfg):
    """Local adaptive (Sauvola) threshold. True where the pixel is ink."""
    win = cfg.sauvola_window | 1
    mean = ndimage.uniform_filter(gray, size=win)
    mean_sq = ndimage.uniform_filter(gray * gray, size=win)
    std = np.sqrt(np.clip(mean_sq - mean * mean, 0.0, None))
    threshold = mean * (1.0 + cfg.sauvola_k * ((std / cfg.sauvola_r) - 1.0))
    return gray < threshold


def _denoise(mask, cfg):
    """Remove connected components below the area threshold (specks/dust)."""
    labels, count = ndimage.label(mask)
    if count == 0:
        return mask
    min_area = max(8, int(mask.size * cfg.min_component_area_frac))
    sizes = ndimage.sum(np.ones_like(mask), labels, index=np.arange(1, count + 1))
    keep = np.zeros(count + 1, dtype=bool)
    keep[1:] = sizes >= min_area
    return keep[labels]


def _fill_small_holes(mask, cfg):
    """
    Fill only *tiny* enclosed holes — the noise pin-holes inside strokes that
    users saw as transparent dots. The large background regions enclosed by
    signature loops (the inside of an 'O', a flourish) are left transparent,
    because they are background, not ink. (Plain ``binary_fill_holes`` would
    wrongly fill those loops with a solid blob.)
    """
    filled = ndimage.binary_fill_holes(mask)
    holes = filled & ~mask
    if not holes.any():
        return mask
    labels, n = ndimage.label(holes)
    counts = np.bincount(labels.ravel())
    max_area = max(40, int(mask.size * cfg.max_hole_frac))
    small = np.nonzero(counts <= max_area)[0]
    small = small[small != 0]                      # 0 is the non-hole region
    return mask | np.isin(labels, small)


def _ink_chroma(rgb_arr):
    """Per-pixel colourfulness in [0, 1]: (max-min channel)/255. High for
    saturated pen ink, near-zero for grey rules, shadows and paper."""
    mx = rgb_arr.max(axis=2)
    mn = rgb_arr.min(axis=2)
    return (mx - mn) / 255.0


def _dominant_bg_fraction(rgb_arr):
    """
    Fraction of the image taken by the single most common (coarsely quantized)
    colour. For a signature on paper this is the paper and is large (>~0.5); a
    photo/selfie/screenshot has no such dominant colour, so it's small. Used to
    reject non-signature uploads.
    """
    small = rgb_arr[::8, ::8].astype(np.int32)
    q = small // 32                                  # 8 levels per channel
    codes = (q[..., 0] * 64 + q[..., 1] * 8 + q[..., 2]).ravel()
    if codes.size == 0:
        return 0.0
    counts = np.bincount(codes, minlength=512)
    return float(counts.max()) / codes.size


def _build_alpha(mask, cfg):
    """
    Build the alpha matte from the (hole-filled) ink ``mask``.

    Every mask pixel is fully opaque — this is what fixes partial/!faded ink and
    the transparent pin-holes users saw. Anti-aliasing comes purely from a
    small Gaussian feather applied to the region *outside* the mask, so edges
    stay smooth without eating into solid strokes.
    """
    m = mask.astype(np.float32)
    soft = ndimage.gaussian_filter(m, sigma=cfg.edge_feather_sigma)
    # Inside the mask: 1.0 (crisp, solid). Outside: the feathered ramp only.
    return np.clip(np.where(mask, 1.0, soft), 0.0, 1.0)


def _crop_box(alpha, cfg):
    """Bounding box of non-transparent pixels, padded by ``crop_margin``."""
    ys, xs = np.where(alpha > 0.04)
    if ys.size == 0:
        return None
    m = cfg.crop_margin
    h, w = alpha.shape
    top = max(0, ys.min() - m)
    bottom = min(h, ys.max() + 1 + m)
    left = max(0, xs.min() - m)
    right = min(w, xs.max() + 1 + m)
    return left, top, right, bottom


def _build_validated_mask(raw_bytes, cfg):
    """
    Load the image, run the photo guard, and return the ink data
    ``(rgb_arr, ink_prob, chroma, mask)`` for rendering. Raises
    ``SignatureProcessingError`` only if the upload has no dominant paper
    background (a photo/selfie), so genuine signatures always pass. Shared by
    ``validate_signature`` (upload-time gate) and ``process_signature_bytes``
    (worker rendering).
    """
    rgb = _load_rgb(raw_bytes, cfg)
    rgb_arr = np.asarray(rgb, dtype=np.float32)
    gray = rgb_arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)

    ink_prob = _illumination_normalized(gray, cfg)        # darkness vs paper
    chroma = _ink_chroma(rgb_arr)                         # colourfulness

    # Reject anything that is not a signature on a plain page: a real signature
    # has a dominant near-uniform paper background. Busy photos/selfies/
    # screenshots do not, so they are turned away with a clear message.
    if _dominant_bg_fraction(rgb_arr) < cfg.min_paper_fraction:
        raise SignatureProcessingError(
            "This doesn't look like a signature on a plain page. Please upload a "
            "clear signature written in dark ink on a blank white page."
        )

    # Ink = locally dark (Sauvola) OR clearly coloured. The colour term captures
    # saturated blue/black pen that is not very dark, which a luminance-only
    # test would drop (the "ink partially removed" symptom).
    dark_mask = _sauvola_mask(gray, cfg)
    color_mask = (chroma > cfg.chroma_min) & (ink_prob > cfg.color_dark_min)
    mask = dark_mask | color_mask
    mask = _denoise(mask, cfg)

    # Seal strokes: close small gaps and fill enclosed holes so interiors are
    # solid — this removes the transparent pin-holes inside the signature.
    if cfg.close_iter > 0:
        mask = ndimage.binary_closing(mask, iterations=cfg.close_iter)
    mask = _fill_small_holes(mask, cfg)

    return rgb_arr, ink_prob, chroma, mask


def validate_signature(raw_bytes, cfg=DEFAULT_CONFIG):
    """
    Run only the photo guard and raise ``SignatureProcessingError`` if the image
    has no dominant paper background (a photo/selfie). Called synchronously at
    upload time so obvious non-signatures are refused immediately, before any
    record is created or background processing starts. Genuine signatures —
    including on used or ruled paper — always pass.
    """
    _build_validated_mask(raw_bytes, cfg)


def process_signature_bytes(raw_bytes, ink_color_mode="preserve", ink_color="#000000",
                            cfg=DEFAULT_CONFIG):
    """
    Run the full pipeline (validation + background removal).

    Returns ``(png_bytes, confidence)`` where ``confidence`` is a 0-1 quality
    estimate. Raises ``SignatureProcessingError`` for rejected, blank/over-dark
    or unreadable inputs so callers can fail gracefully.
    """
    rgb_arr, ink_prob, chroma, mask = _build_validated_mask(raw_bytes, cfg)
    mask = _denoise(mask, cfg)

    coverage = float(mask.mean())
    if coverage < cfg.min_ink_coverage:
        raise SignatureProcessingError(
            "No clear signature was detected. Use a darker pen on plain, "
            "well-lit paper and try again."
        )
    if coverage > cfg.max_ink_coverage:
        raise SignatureProcessingError(
            "The image is too dark or busy to isolate a signature. Crop closely "
            "to the signature on a light background and retry."
        )

    # Confidence: how strongly the detected ink stands out from paper.
    strength = np.clip(ink_prob + chroma, 0.0, 1.0)
    confidence = float(np.clip(strength[mask].mean(), 0.0, 1.0)) if mask.any() else 0.0

    alpha = _build_alpha(mask, cfg)

    box = _crop_box(alpha, cfg)
    if box is None:
        raise SignatureProcessingError("No signature strokes found after cleanup.")
    left, top, right, bottom = box
    alpha = alpha[top:bottom, left:right]
    rgb_arr = rgb_arr[top:bottom, left:right]

    alpha_u8 = (alpha * 255.0).astype(np.uint8)

    if ink_color_mode == "mono":
        r, g, b = _hex_to_rgb(ink_color)
        out = np.zeros((*alpha.shape, 4), dtype=np.uint8)
        out[..., 0] = r
        out[..., 1] = g
        out[..., 2] = b
        out[..., 3] = alpha_u8
    else:  # preserve original ink color
        out = np.dstack([rgb_arr.astype(np.uint8), alpha_u8])

    buf = io.BytesIO()
    Image.fromarray(out, mode="RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue(), confidence
