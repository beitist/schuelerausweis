# Database Migrations

Das Backend legt fehlende Tabellen beim Start automatisch an (`SQLModel.metadata.create_all`)
und führt zusätzlich **idempotente Schema-Migrationen** im Startup aus (siehe `backend/main.py`,
Lifespan-Handler). Eine frische Installation braucht daher keine manuellen Migrationen.

Dieses Verzeichnis ist für zukünftige Schema-Änderungen gedacht, die sich nicht
sinnvoll im Startup abbilden lassen (z.B. lang laufende Daten-Umbauten).

## Migrations ausführen

### In Coolify (Production)

1. Gehe zu **PostgreSQL Database** in Coolify
2. Klicke auf **Execute SQL** oder öffne das Terminal
3. Führe die Migration aus:
   ```bash
   psql -f /path/to/migration.sql
   ```

### Lokal (Development)

```bash
# Mit DATABASE_URL aus .env
source venv/bin/activate
psql $DATABASE_URL -f db/migrations/XXX_migration_name.sql
```

## Best Practices

- Jede Migration sollte **idempotent** sein (`IF NOT EXISTS`, `IF EXISTS`)
- Migrations niemals zurücknehmen, sondern neue erstellen
- Dateinamen: `XXX_description.sql` (XXX = fortlaufende Nummer)
- In Production NICHT ohne Backup ausführen!
