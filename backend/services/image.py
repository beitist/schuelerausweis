"""
Bildverarbeitung für Hagen-ID

Komprimiert und skaliert hochgeladene Fotos auf eine einheitliche Größe,
um Speicherplatz zu sparen und Ladezeiten zu reduzieren.
"""

import io
import base64
import hashlib
from PIL import Image

# Maximale Pixelgröße (längste Seite)
MAX_DIMENSION = 800
# JPEG-Qualität (1-100)
JPEG_QUALITY = 95


def compress_image(image_bytes: bytes) -> str:
    """
    Komprimiert ein Bild auf max. 800px, konvertiert zu JPEG und gibt Base64 zurück.

    - Skaliert proportional auf MAX_DIMENSION
    - Konvertiert RGBA/Palette zu RGB (für JPEG-Kompatibilität)
    - Entfernt EXIF-Daten (Datenschutz)
    - Gibt Base64-String zurück (ohne data:-Prefix)
    """
    img = Image.open(io.BytesIO(image_bytes))

    # EXIF-Rotation anwenden (manche Handys speichern Rotation nur als EXIF)
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    # RGBA/Palette → RGB konvertieren (JPEG unterstützt kein Alpha)
    if img.mode in ("RGBA", "P", "LA"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Proportional skalieren
    width, height = img.size
    if max(width, height) > MAX_DIMENSION:
        if width > height:
            new_width = MAX_DIMENSION
            new_height = int(height * MAX_DIMENSION / width)
        else:
            new_height = MAX_DIMENSION
            new_width = int(width * MAX_DIMENSION / height)
        img = img.resize((new_width, new_height), Image.LANCZOS)

    # Als JPEG in Buffer schreiben
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    buffer.seek(0)

    return base64.b64encode(buffer.read()).decode()


def compress_base64_image(b64_data: str) -> str:
    """
    Komprimiert ein Base64-codiertes Bild.

    Für die Sync-API, die Fotos bereits als Base64 empfängt.
    """
    image_bytes = base64.b64decode(b64_data)
    return compress_image(image_bytes)


def hash_image(b64_data: str) -> str:
    """SHA-256 Hash eines Base64-Strings."""
    return hashlib.sha256(b64_data.encode()).hexdigest()
