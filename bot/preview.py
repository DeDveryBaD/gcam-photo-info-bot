"""Thumbnail generation for documents and RAW files.

Telegram shows no preview for DNG/CR2/NEF/ARW documents, and the card in
/share_photo must show one. Two paths:

1. Pillow for formats it understands (JPEG, PNG, WebP, TIFF).
2. ExifTool ``-b -PreviewImage`` / ``-ThumbnailImage`` for RAW: the sensor dump
   already sits inside the file, so no RAW decoder is needed.

Returns the path to a JPEG, or None when nothing usable was found.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

MAX_EDGE = 1280  # Telegram renders photo captions best below ~1280 px
PILLOW_FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
EXIFTOOL_TAGS = ("PreviewImage", "ThumbnailImage", "MainImage")


def _via_pillow(src: Path, dst: Path) -> bool:
    if src.suffix.lower() not in PILLOW_FORMATS:
        return False
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False
    try:
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((MAX_EDGE, MAX_EDGE))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(dst, "JPEG", quality=85, optimize=True)
        return dst.exists() and dst.stat().st_size > 0
    except Exception:  # noqa: BLE001 - truncated/odd files are common
        return False


def _via_exiftool(src: Path, dst: Path) -> bool:
    for tag in EXIFTOOL_TAGS:
        try:
            proc = subprocess.run(
                ["exiftool", "-b", f"-{tag}", str(src)],
                check=False, capture_output=True, timeout=20,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        data = proc.stdout
        # Embedded thumbnails are often 160x120; reject anything useless.
        if len(data) > 4096 and data[:3] == b"\xff\xd8\xff":
            dst.write_bytes(data)
            return True
    return False


def build_preview(src: Path, dst: Path) -> Path | None:
    """Write a JPEG preview to ``dst``; return it, or None on failure."""
    if _via_pillow(src, dst):
        return dst
    if _via_exiftool(src, dst):
        # ExifTool previews can be tiny; upscale is pointless, keep as-is.
        return dst
    dst.unlink(missing_ok=True)
    return None
