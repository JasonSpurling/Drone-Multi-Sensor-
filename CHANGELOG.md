# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/) once past
1.0.0 -- pre-1.0 (`0.x.y`), a minor bump (`0.x`) may still include a
breaking change, same as semver's own pre-1.0 convention. `app/__version__`
(see `app/__init__.py`) is the single source of truth for the running
version; it's surfaced in `GET /api/health` and the OpenAPI schema so
"what's actually deployed" is answerable by asking the running process.

When you make a change worth calling out to someone upgrading a running
deployment, add a bullet under `## [Unreleased]` in the relevant category
(Added / Changed / Fixed / Security). When you cut a release, retitle that
section with the version and date, bump `__version__`, and start a fresh
`## [Unreleased]` above it.

## [Unreleased]

## [0.1.0] - 2026-08-22

First version-tracked baseline. This project didn't tag releases or keep
a changelog before this point -- rather than fabricate a history the git
log doesn't actually record cleanly release-by-release, this entry marks
the point version tracking started, on top of an already-substantial,
tested system: multi-sensor detection ingest and Kalman/IMM tracking,
zone-incursion and behavioral (loitering/formation/shadowing) incident
detection, a live dashboard, multi-site isolation, CoT/NATS/webhook
output integrations, and the operational tooling in this same change
(data retention, automated backups, Prometheus metrics, Dependabot,
graceful shutdown, a load-smoke CI job). See the git log and README.md
for the detail a changelog entry can't practically summarize retroactively.
