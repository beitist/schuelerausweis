"""
App-weite Key/Value-Einstellungen (Tabelle `app_settings`).

Global (nicht mandantengebunden), vom Superadmin verwaltet. Aktuell: Live-Override der
Login-Rate-Limits. Lesezugriffe während des Logins laufen privilegiert (vor set_tenant)
bzw. als Superadmin → kein RLS-Problem.
"""

from datetime import datetime
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import Session

from models import AppSetting


def get_value(session: Session, key: str) -> Optional[str]:
    row = session.get(AppSetting, key)
    return row.value if row else None


def set_value(session: Session, key: str, value: str) -> None:
    row = session.get(AppSetting, key)
    if row:
        row.value = value
        row.updated_at = datetime.utcnow()
    else:
        row = AppSetting(key=key, value=value)
    session.add(row)
    session.commit()
