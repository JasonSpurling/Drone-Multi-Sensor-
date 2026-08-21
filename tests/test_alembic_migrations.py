"""Regression guard for the exact drift class that motivated writing
migrations/README.md's warning in the first place: app/schema.py's
`track` table was for a long time missing `maneuver_probability`, a
column that only ever got added via app/db.py's runtime ALTER-TABLE
migrator, never via metadata.create_all(). schema.py's own docstring
calls it "the single source of truth for the database schema" -- this
test makes that actually true by failing CI the moment schema.py and
what init_db() produces diverge again, for any column, in either
direction.

Doesn't touch migrations/versions/ at all -- this compares the *live*
schema init_db() just built against app.schema.metadata directly via
Alembic's own autogenerate diff engine, which is a stronger and simpler
check than re-running the baseline migration each time.
"""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.schema import metadata


def test_schema_metadata_matches_what_init_db_actually_creates(isolated_db):
    # isolated_db (autouse fixture) has already run init_db() against a
    # fresh database -- exactly the "what does the real app produce"
    # baseline this compares app.schema.metadata against.
    with isolated_db.connect() as conn:
        context = MigrationContext.configure(conn)
        diff = compare_metadata(context, metadata)

    assert diff == [], (
        "app/schema.py doesn't match what init_db() actually creates: "
        f"{diff}. If you just added a column via app/db.py's "
        "_TABLE_MIGRATION_COLUMNS (for upgrading *existing* databases), "
        "add the same column to its Table definition in app/schema.py too "
        "-- metadata.create_all() needs it for a *fresh* database to end "
        "up with the same shape."
    )
