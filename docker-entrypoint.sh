#!/bin/sh
set -e
# Run DB init (creates tables and seeds POS transactions) then start the app
python - <<'PY'
import asyncio
from app import db
try:
    asyncio.run(db.init_db())
except Exception as e:
    print('DB init failed:', e)
PY

# Exec the CMD
exec "$@"
