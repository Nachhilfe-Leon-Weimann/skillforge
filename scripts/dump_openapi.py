"""Dump the FastAPI OpenAPI schema to ``openapi.json``.

This is the source of truth for API consumers (e.g. skillbot generates its
client from it). Importing ``app.main`` runs settings loading at module level
but never opens a DB connection, so the dump needs satisfiable settings but no
running database. CI provides dummy values; see the ``openapi`` Just recipe.
"""

import json
import os
import sys
from pathlib import Path

# Dummy settings so the app imports for the schema dump
os.environ.setdefault("DB__URL", "postgresql+asyncpg://schema:dump@localhost/schema")
os.environ.setdefault("AUTH__SECRET_KEY", "schema-dump")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

OUTPUT = ROOT / "openapi.json"


def main() -> None:
    schema = app.openapi()
    OUTPUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
