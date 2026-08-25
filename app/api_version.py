"""API versioning: header-based (not a `/v1/` path prefix), so a route's
URL never has to change just because its version does.

Only one version exists today (there's been no breaking change yet to
version against), so this module is the *mechanism* a future breaking
change would use, not evidence one has happened: a client can send
`X-API-Version: 1` to pin against the version it was written against: if
that ever stops being CURRENT_API_VERSION, this app can start dispatching
differently per version rather than breaking every existing integration
the moment a route's request/response shape changes. Every response
carries `X-API-Version` (whatever version was actually served) so a
client can always tell what it got, whether or not it asked.

An unrecognized version is rejected with 400 rather than silently served
the current version -- silently serving a version the client didn't ask
for is exactly the kind of surprise this exists to prevent.
"""

from __future__ import annotations

CURRENT_API_VERSION = "1"
SUPPORTED_API_VERSIONS = frozenset({"1"})

API_VERSION_HEADER = "X-API-Version"


def is_supported(version: str) -> bool:
    return version in SUPPORTED_API_VERSIONS
