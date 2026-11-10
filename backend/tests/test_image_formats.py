"""
Tests für die Formatbeschränkung beim Foto-Upload (services/image.py).

Kernaussagen:
- JPEG, PNG und WebP werden angenommen und als JPEG zurückgegeben.
- Andere Formate (z. B. TIFF, BMP, GIF) erreichen gar nicht erst ihren Pillow-Decoder,
  sondern werden mit UnsupportedImageError abgelehnt (die Routen machen daraus 400).

Ausführen:
    cd backend && python -m pytest tests/test_image_formats.py -q
"""

import base64
import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.image import compress_image, UnsupportedImageError


def _encode(fmt: str, mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (1200, 600), "red").save(buf, format=fmt)
    return buf.getvalue()


@pytest.mark.parametrize("fmt,mode", [("JPEG", "RGB"), ("PNG", "RGBA"), ("WEBP", "RGB")])
def test_allowed_formats_are_compressed_to_jpeg(fmt, mode):
    out = Image.open(io.BytesIO(base64.b64decode(compress_image(_encode(fmt, mode)))))
    assert out.format == "JPEG"
    assert max(out.size) == 800


@pytest.mark.parametrize("fmt", ["TIFF", "BMP", "GIF"])
def test_other_formats_are_rejected(fmt):
    with pytest.raises(UnsupportedImageError):
        compress_image(_encode(fmt))


def test_garbage_is_rejected():
    with pytest.raises(UnsupportedImageError):
        compress_image(b"not an image at all")
