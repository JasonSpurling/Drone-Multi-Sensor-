"""Bootstraps the "default" site that a pre-multi-site deployment's data
and API keys get migrated into on upgrade, so existing single-site
behavior keeps working unchanged for anyone not deliberately setting up
more than one site. See app/db.py's _migrate_site_id_columns for the data
side of that migration and app/auth.py for the API-key side.
"""

from __future__ import annotations

DEFAULT_SITE_NAME = "default"

# Cached after the first lookup/creation -- a site's id is immutable for
# the life of a process, and this is read on every authenticated request
# (app/auth.py), so it isn't worth a database round-trip each time.
_default_site_id: int | None = None


def ensure_default_site() -> int:
    """Returns the default site's id, creating it if this is a fresh
    database. Called once from app.db.init_db() at startup, before any
    migration backfills that need a site to backfill into.
    """
    global _default_site_id
    if _default_site_id is not None:
        return _default_site_id

    from app.db import create_site, get_site_by_name

    site = get_site_by_name(DEFAULT_SITE_NAME)
    if site is None:
        site = create_site(DEFAULT_SITE_NAME)
    assert site.id is not None
    _default_site_id = site.id
    return _default_site_id


def reset_cache_for_tests() -> None:
    """Test-only: each test gets a fresh isolated database (see
    tests/conftest.py), so the cached id from a previous test's database
    must not leak into the next one.
    """
    global _default_site_id
    _default_site_id = None
