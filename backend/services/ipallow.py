"""
IP-Allowlist für die Sync-API.

Die Sync-API ist der größte PII-Kanal (kompletter Roster, Lesen UND Schreiben) und
hängt sonst an genau EINEM Shared Secret. Diese Allowlist macht den Key optional zum
zweiten Faktor: Ist sie für eine Schule aktiv, akzeptiert der Sync nur noch Requests
aus hinterlegten IP-Bereichen — ein geleakter Key allein nützt dann nichts mehr.

Bewusst eine Allowlist statt WireGuard/mTLS (der stärkeren, aber aufwendigeren
Option): Schulen haben meist statische Egress-IPs, und eine Allowlist ist der
günstigste echte Gewinn.

Design:
- `normalize_cidr` validiert + kanonisiert Eingaben (Einzel-IP → /32 bzw. /128), v4 und v6.
- `is_sync_allowed` ist **fail-closed**, wenn die Allowlist aktiv ist: eine leere Liste
  sperrt alles (der AUS-Schalter an der Schule ist die Fluchttür, nicht die leere Liste).
- Ist die Allowlist AUS, gilt keine Einschränkung.
"""

import ipaddress
from typing import Iterable, Optional


def normalize_cidr(raw: str) -> str:
    """
    Validiert und kanonisiert eine IP/CIDR-Eingabe.

    "203.0.113.7"      -> "203.0.113.7/32"
    "203.0.113.0/24"   -> "203.0.113.0/24"
    "2001:db8::1"      -> "2001:db8::1/128"
    Wirft ValueError bei ungültiger Eingabe.

    strict=False erlaubt "203.0.113.5/24" (Host-Bits gesetzt) und normalisiert auf das Netz.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("Leere Eingabe")
    if "/" in text:
        net = ipaddress.ip_network(text, strict=False)
    else:
        # Einzel-Host → volle Präfixlänge
        net = ipaddress.ip_network(ipaddress.ip_address(text))
    return str(net)


def ip_in_entries(client_ip: str, cidrs: Iterable[str]) -> bool:
    """
    True, wenn `client_ip` in einem der `cidrs` liegt.

    Fehlerhafte Einträge/IPs werden übersprungen bzw. führen zu False —
    im Zweifel NICHT erlauben (fail-closed).
    """
    try:
        ip = ipaddress.ip_address((client_ip or "").strip())
    except ValueError:
        return False

    for c in cidrs:
        try:
            if ip in ipaddress.ip_network(c, strict=False):
                return True
        except ValueError:
            continue  # kaputter Eintrag zählt nicht als Treffer
    return False


def is_sync_allowed(enabled: bool, client_ip: Optional[str], cidrs: Iterable[str]) -> bool:
    """
    Entscheidet, ob ein Sync-Request von `client_ip` zugelassen wird.

    - Allowlist AUS  -> immer erlaubt (Fallback).
    - Allowlist AN   -> nur wenn die IP in einem Eintrag liegt (leere Liste = alles blocken).
    """
    if not enabled:
        return True
    if not client_ip:
        return False
    return ip_in_entries(client_ip, list(cidrs))
