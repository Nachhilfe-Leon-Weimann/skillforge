from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _script_directory() -> ScriptDirectory:
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    return ScriptDirectory.from_config(config)


def test_migrations_have_a_single_head():
    script = _script_directory()
    assert len(script.get_heads()) == 1, "Expected exactly one Alembic head; merge the divergent revisions."


def test_migration_chain_is_walkable_to_base():
    script = _script_directory()
    revisions = list(script.walk_revisions())
    assert revisions, "No Alembic revisions found."
    # The oldest revision must be the baseline (down_revision is None).
    assert revisions[-1].down_revision is None
