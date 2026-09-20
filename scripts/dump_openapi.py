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
from typing import Any

# Dummy settings so the app imports for the schema dump
os.environ.setdefault("DB__URL", "postgresql+asyncpg://schema:dump@localhost/schema")
os.environ.setdefault("AUTH__SECRET_KEY", "schema-dump")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

OUTPUT = ROOT / "openapi.json"


def _ints_for_integer_valued_floats(value: Any) -> Any:
    # release-please's `json` extra-file updater re-serializes this file with JavaScript's
    # JSON.parse/JSON.stringify; JavaScript has one numeric type, so a float like `50.0` would come
    # back as `50` and `just openapi-check` would flag the file as stale on every release PR.
    if isinstance(value, dict):
        return {key: _ints_for_integer_valued_floats(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_ints_for_integer_valued_floats(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def main() -> None:
    schema = _ints_for_integer_valued_floats(app.openapi())
    OUTPUT.write_text(json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
