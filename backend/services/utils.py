"""
Hilfsfunktionen für Hagen-ID

PIN-Generierung und andere Utilities
"""

import secrets
import string


def generate_login_pin() -> str:
    """
    Generiert eine 6-stellige numerische PIN für den Login.
    
    Die PIN ist kryptographisch sicher zufällig und dient als
    "Besitz"-Faktor beim Login (wird auf physischem Ausweis gedruckt).
    
    Returns:
        str: 6-stellige PIN ohne führende Null (z.B. "482751")
             Bereich: 100000–999999
        
    Example:
        >>> pin = generate_login_pin()
        >>> len(pin)
        6
        >>> pin.isdigit()
        True
        >>> int(pin) >= 100000
        True
    """
    # Bereich 100000-999999: Keine führende Null, damit die PIN
    # beim Drucken/Ablesen eindeutig 6-stellig erkennbar ist
    return str(secrets.randbelow(900000) + 100000)


def generate_login_code(first_name: str, last_name: str) -> str:
    """
    Generiert den Display-Code für den Ausweis.
    
    DEPRECATED: Wird durch login_pin ersetzt, bleibt aber für
    CSV-Import-Kompatibilität erhalten.
    
    Format: Vorname[0] + Nachname + 4 zufällige Zeichen
    Beispiel: "MMustermann7x3k"
    
    Args:
        first_name: Vorname
        last_name: Nachname
        
    Returns:
        str: Display-Code
    """
    # Erste Buchstabe Vorname + ganzer Nachname
    base = (first_name[0] if first_name else 'X') + last_name
    
    # 4 zufällige alphanumerische Zeichen anhängen
    charset = string.ascii_lowercase + string.digits
    random_suffix = ''.join(secrets.choice(charset) for _ in range(4))
    
    return base + random_suffix
