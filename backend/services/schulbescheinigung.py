"""
Schulbescheinigungs-Service für Hagen-ID

Erzeugt eine PDF-Schulbescheinigung mit einem DAUERHAFTEN QR-Code.
Anders als der rotierende Ausweis-QR (TOTP) verweist dieser QR auf eine
öffentliche Verifizierungsseite, die den aktuellen Live-Status des Schülers
(aktiv/eingeschrieben) anzeigt.

Zusätzlich enthält das Modul kleine Datums-Helfer und die Logik für das
"Vorläufig"-Fenster (Bescheinigungen, die zwischen dem 01.08. und 31.10.
ausgestellt werden, sind nur vorläufig und 3 Monate gültig — falls die
Schule diesen Modus aktiviert hat).
"""

import base64
import calendar
import unicodedata
from datetime import date
from io import BytesIO
from typing import Optional, Tuple


# ============================================================================
# TEXT-SICHERHEIT (fpdf2 Kern-Schriften nutzen Latin-1)
# ============================================================================
#
# Die eingebauten fpdf2-Schriften können nur Latin-1 darstellen. Deutsche
# Umlaute und westeuropäische Akzente (ä, ö, ü, ß, é, ñ, ç ...) sind darin
# enthalten. Namen außerhalb von Latin-1 (z.B. türkisch ş/ğ, polnisch ł/ą,
# vietnamesisch) würden sonst einen Fehler auslösen und die PDF-Erzeugung
# komplett verhindern. Wir transliterieren solche Zeichen daher schonend,
# damit immer ein lesbares Dokument entsteht.

_TRANSLIT = {
    "ł": "l", "Ł": "L",
    "ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "İ": "I", "ı": "i",
    "đ": "d", "Đ": "D",
}


def latin1_safe(text) -> str:
    """Macht einen String Latin-1-tauglich (transliteriert/ersetzt unbekannte Zeichen)."""
    if text is None:
        return ""
    out = []
    for ch in str(text):
        try:
            ch.encode("latin-1")
            out.append(ch)
            continue
        except UnicodeEncodeError:
            pass
        repl = _TRANSLIT.get(ch)
        if repl is None:
            # NFKD-Zerlegung: Basisbuchstabe ohne kombinierende Akzente
            base = "".join(
                c for c in unicodedata.normalize("NFKD", ch)
                if not unicodedata.combining(c)
            )
            try:
                base.encode("latin-1")
                repl = base if base else "?"
            except UnicodeEncodeError:
                repl = "?"
        out.append(repl)
    return "".join(out)


# ============================================================================
# DATUMS-HELFER
# ============================================================================

def add_months(d: date, months: int) -> date:
    """Addiert Monate auf ein Datum (mit Tag-Clamping am Monatsende)."""
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last_day))


def school_year_str(d: date) -> str:
    """
    Liefert das NRW-Schuljahr als String, z.B. '2026/2027'.
    Das Schuljahr beginnt am 01.08.
    """
    if d.month >= 8:
        return f"{d.year}/{d.year + 1}"
    return f"{d.year - 1}/{d.year}"


def is_provisional_window(d: date) -> bool:
    """True, wenn das Datum im Fenster 01.08.–31.10. liegt."""
    return d.month in (8, 9, 10)


def teacher_service_end(dob: date, retirement_age: int = 67) -> date:
    """
    Formales Dienstzeitende einer Lehrkraft: Ende des SCHULHALBJAHRES, in dem
    der 67. Geburtstag liegt (NRW-Halbjahre: 01.08.–31.01. und 01.02.–31.07.).

    Beispiele: 67. Geburtstag am 15.09.2030 → 31.01.2031 · am 15.03.2031 → 31.07.2031
    · am 31.01.2031 → 31.01.2031 (Geburtstag ist letzter Tag des Halbjahres).
    """
    # 67. Geburtstag (29.02. → 28.02. in Nicht-Schaltjahren)
    try:
        b = dob.replace(year=dob.year + retirement_age)
    except ValueError:
        b = date(dob.year + retirement_age, 2, 28)
    if b.month >= 8:        # Aug–Dez → 1. Halbjahr endet 31.01. des Folgejahres
        return date(b.year + 1, 1, 31)
    if b.month == 1:        # Januar → 31.01. desselben Jahres
        return date(b.year, 1, 31)
    return date(b.year, 7, 31)   # Feb–Jul → 2. Halbjahr endet 31.07.


# ============================================================================
# HILFSFUNKTIONEN
# ============================================================================

def data_uri_to_bytes(data_uri: Optional[str]) -> Optional[bytes]:
    """Dekodiert einen Base64-Data-URI (oder rohen Base64-String) zu Bytes."""
    if not data_uri:
        return None
    try:
        b64 = data_uri.split(",", 1)[1] if "," in data_uri else data_uri
        return base64.b64decode(b64)
    except Exception:
        return None


def hex_to_rgb(hex_color: Optional[str], default: Tuple[int, int, int] = (26, 115, 232)) -> Tuple[int, int, int]:
    """Wandelt '#1a73e8' in (26, 115, 232). Fallback bei ungültigem Wert."""
    try:
        h = (hex_color or "").lstrip("#")
        if len(h) != 6:
            return default
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return default


def make_qr_png(url: str) -> BytesIO:
    """Erzeugt einen QR-Code als PNG-BytesIO."""
    import qrcode
    qr = qrcode.QRCode(border=2, box_size=10)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ============================================================================
# BESCHEINIGUNGS-TEMPLATES (Schuladmin-editierbar; NULL in der DB = Standard)
# ============================================================================
#
# Platzhalter: {Name} {Vorname} {GebDatum} {Bildungsgang} {Klasse} {Schule} {Schuljahr}
# {Bildungsgang} löst vorerst leer auf — das SchILD-Feld wird erst später mitgesynct
# (bewusst schon als Variable vorgesehen). Unbekannte {Platzhalter} bleiben wörtlich
# stehen, damit ein Tippfehler im Template sichtbar ist statt still zu verschwinden.

DEFAULT_TEMPLATE_STUDENT = (
    "Hiermit wird bescheinigt, dass\n\n"
    "{Vorname} {Name}, geboren am {GebDatum},\n\n"
    "im Schuljahr {Schuljahr} Schülerin bzw. Schüler der {Schule} "
    "in der Klasse {Klasse} ist."
)

DEFAULT_TEMPLATE_TEACHER = (
    "Hiermit wird bescheinigt, dass\n\n"
    "{Vorname} {Name}\n\n"
    "an der {Schule} als Lehrkraft tätig ist."
)

TEMPLATE_VARIABLES = ("Name", "Vorname", "GebDatum", "Bildungsgang", "Klasse", "Schule", "Schuljahr")


def render_template(template: str, values: dict) -> str:
    """
    Ersetzt {Platzhalter} im Template durch Werte. Nur bekannte Variablen werden
    ersetzt (kein str.format — geschweifte Klammern im Fließtext sind damit harmlos,
    und unbekannte Platzhalter bleiben sichtbar stehen).
    """
    out = template
    for key in TEMPLATE_VARIABLES:
        out = out.replace("{" + key + "}", str(values.get(key, "") or ""))
    return out


# ============================================================================
# PDF-ERZEUGUNG
# ============================================================================

def render_certificate_pdf(
    *,
    school_name: str,
    student_name: str,
    dob_str: str,
    class_name: str,
    school_year: str,
    issue_date_str: str,
    valid_until_str: str,
    is_provisional: bool,
    verify_url: str,
    logo_bytes: Optional[bytes] = None,
    contact_email: Optional[str] = None,
    primary_color: Optional[str] = None,
    body: Optional[str] = None,        # gerenderter Template-Text; None = Standard-Schülertext
    is_teacher: bool = False,          # Dienstbescheinigung: Titel + "Funktion:" statt "Klasse:"
    school_address: Optional[str] = None,   # Briefkopf: Postanschrift (mehrzeilig)
    school_number: Optional[str] = None,    # Briefkopf: amtliche Schulnummer
    school_phone: Optional[str] = None,     # Briefkopf: Telefon
) -> bytes:
    """
    Rendert die Schulbescheinigung als PDF und gibt die Bytes zurück.

    Nutzt fpdf2 mit den eingebauten Latin-1-Schriften (deutsche Umlaute sind
    darin enthalten). Es werden daher bewusst nur Zeichen aus Latin-1 verwendet
    (kein Gedankenstrich/typografische Anführungszeichen).
    """
    from fpdf import FPDF

    # Alle dynamischen Texte Latin-1-tauglich machen (Namen etc.)
    school_name = latin1_safe(school_name)
    student_name = latin1_safe(student_name)
    dob_str = latin1_safe(dob_str)
    class_name = latin1_safe(class_name)
    school_year = latin1_safe(school_year)
    issue_date_str = latin1_safe(issue_date_str)
    valid_until_str = latin1_safe(valid_until_str)
    contact_email = latin1_safe(contact_email) if contact_email else None

    r, g, b = hex_to_rgb(primary_color)

    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_margins(20, 18, 20)

    # ---- Briefkopf: Logo links, Schuldaten-Block rechts ----
    top_y = pdf.get_y()
    if logo_bytes:
        try:
            pdf.image(BytesIO(logo_bytes), x=20, y=top_y, h=18)
        except Exception:
            pass  # kaputtes Logo darf das PDF nicht verhindern

    pdf.set_xy(20, top_y)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 9, school_name, align="R", new_x="LMARGIN", new_y="NEXT")

    # Adress-/Kontaktzeilen (nur befüllte; Adresse kann mehrzeilig sein)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 116, 139)
    head_lines = []
    if school_address:
        head_lines += [l.strip() for l in latin1_safe(school_address).splitlines() if l.strip()]
    contact_bits = []
    if school_phone:
        contact_bits.append(f"Tel. {latin1_safe(school_phone)}")
    if contact_email:
        contact_bits.append(contact_email)
    if contact_bits:
        head_lines.append("  ·  ".join(contact_bits))
    if school_number:
        head_lines.append(f"Schulnummer {latin1_safe(school_number)}")
    head_lines.append(f"Schuljahr {school_year}")
    for line in head_lines:
        pdf.cell(0, 4.5, line, align="R", new_x="LMARGIN", new_y="NEXT")

    # Trennlinie in Primärfarbe
    pdf.ln(4)
    line_y = pdf.get_y()
    pdf.set_draw_color(r, g, b)
    pdf.set_line_width(0.8)
    pdf.line(20, line_y, 190, line_y)
    pdf.ln(10)

    # ---- Titel ----
    if is_teacher:
        title = "Dienstbescheinigung"
    else:
        title = "Vorläufige Schulbescheinigung" if is_provisional else "Schulbescheinigung"
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 12, title, align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # ---- Fließtext (Template-gerendert; Fallback = Standard-Schülertext) ----
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(30, 41, 59)
    if body is None:
        body = (
            f"Hiermit wird bescheinigt, dass\n\n"
            f"{student_name}, geboren am {dob_str},\n\n"
            f"im Schuljahr {school_year} Schülerin bzw. Schüler der {school_name} "
            f"in der Klasse {class_name} ist."
        )
    pdf.multi_cell(0, 7, latin1_safe(body))
    pdf.ln(6)

    # ---- Vorläufig-Hinweis ----
    if is_provisional:
        note = f"Vorläufige Schulbescheinigung - nur 3 Monate gültig (bis {valid_until_str})."
        pdf.set_fill_color(254, 243, 199)   # helles Gelb
        pdf.set_draw_color(217, 119, 6)     # Amber
        pdf.set_text_color(146, 64, 14)
        pdf.set_line_width(0.3)
        pdf.set_font("Helvetica", "B", 11)
        pdf.multi_cell(0, 8, note, border=1, fill=True)
        pdf.ln(6)

    # ---- Detail-Zeilen ----
    pdf.set_text_color(30, 41, 59)

    def detail(label: str, value: str):
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(45, 7, label, new_x="END", new_y="LAST")
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")

    if is_teacher:
        detail("Funktion:", "Lehrkraft")
    else:
        detail("Klasse:", class_name)
    detail("Gültig bis:", valid_until_str)
    detail("Ausgestellt am:", issue_date_str)

    # ---- QR-Code (unten) ----
    pdf.ln(12)
    qr_y = pdf.get_y()
    try:
        qr_png = make_qr_png(verify_url)
        pdf.image(qr_png, x=20, y=qr_y, w=38)
    except Exception:
        pass

    # Text rechts neben dem QR
    pdf.set_xy(62, qr_y + 2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(30, 41, 59)
    pdf.multi_cell(128, 6, "Aktueller Status prüfbar")
    pdf.set_x(62)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 116, 139)
    pdf.multi_cell(
        128, 5,
        "Diesen QR-Code scannen, um zu prüfen, ob die Person aktuell aktiv "
        "eingeschrieben ist. Der Code bleibt dauerhaft gültig; der angezeigte "
        "Status wird jeweils live abgefragt."
    )

    # ---- Fußzeile ----
    # WICHTIG: Auto-Page-Break AUS — set_y(-24) landet in der Break-Zone (Trigger bei
    # Seitenhöhe−20mm); mit aktivem Break würde die 2-zeilige Fußzeile eine fast
    # leere ZWEITE Seite erzeugen.
    pdf.set_auto_page_break(auto=False)
    pdf.set_y(-24)
    pdf.set_draw_color(226, 232, 240)
    pdf.set_line_width(0.2)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(100, 116, 139)
    footer = "Digital erstellt - ohne Unterschrift gültig."
    if contact_email:
        footer += f"  Rückfragen: {contact_email}"
    pdf.multi_cell(0, 4, footer)

    out = pdf.output()
    return bytes(out)
