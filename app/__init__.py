# Single source of truth for the running version -- surfaced in the
# OpenAPI schema (FastAPI's `version=`, see app/main.py) and in
# GET /api/health, so "what's actually deployed" is answerable by asking
# the running process, not by comparing it against git history. Bump this
# by hand alongside a new CHANGELOG.md entry -- see that file's header and
# README.md's "Versioning" section for the policy.
__version__ = "0.2.0"
