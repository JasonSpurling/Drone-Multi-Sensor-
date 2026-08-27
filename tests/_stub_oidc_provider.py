"""A minimal, real OpenID Connect provider for testing app/oidc.py and
app/api/auth_sso.py end-to-end -- a genuine HTTP server (not a mock of
authlib's internals) implementing just enough of the spec (discovery,
authorization-code + PKCE, RS256-signed ID tokens, JWKS) for authlib's
real client code to complete a real login against it. Auto-approves
every /authorize request (no login page) since these tests are about
this app's handling of the OIDC flow, not testing a browser filling in a
consent screen.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from cryptography.hazmat.primitives.asymmetric import rsa
from joserfc import jwt
from joserfc.jwk import RSAKey

_KID = "stub-oidc-key"


class StubOidcProvider:
    """Usage: `with StubOidcProvider() as idp: ...` -- idp.issuer_url is
    ready to plug into DRONE_OIDC_ISSUER_URL. Set idp.next_claims before
    triggering a login to control what the "authenticated user"'s ID
    token carries (role/site/email/sub -- whatever a real IdP's claims
    would carry).
    """

    def __init__(self) -> None:
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._jwk = RSAKey.import_key(self._private_key, {"kid": _KID, "use": "sig", "alg": "RS256"})
        self._public_jwk = RSAKey.import_key(
            self._private_key.public_key(), {"kid": _KID, "use": "sig", "alg": "RS256"}
        )
        self._codes: dict[str, dict] = {}
        self.next_claims: dict = {"sub": "user-123", "email": "alice@example.com"}
        self.port = _free_port()
        self.issuer_url = f"http://127.0.0.1:{self.port}"
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> StubOidcProvider:
        provider = self
        issuer_url = self.issuer_url

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # quiet -- pytest captures enough noise already
                pass

            def _json(self, obj: dict, status: int = 200) -> None:
                body = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/.well-known/openid-configuration":
                    self._json(
                        {
                            "issuer": issuer_url,
                            "authorization_endpoint": f"{issuer_url}/authorize",
                            "token_endpoint": f"{issuer_url}/token",
                            "jwks_uri": f"{issuer_url}/jwks",
                            "response_types_supported": ["code"],
                            "subject_types_supported": ["public"],
                            "id_token_signing_alg_values_supported": ["RS256"],
                            "scopes_supported": ["openid", "profile", "email"],
                            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
                        }
                    )
                elif parsed.path == "/jwks":
                    self._json({"keys": [provider._public_jwk.as_dict()]})
                elif parsed.path == "/authorize":
                    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                    code = secrets.token_urlsafe(16)
                    provider._codes[code] = {
                        "nonce": params.get("nonce"),
                        "redirect_uri": params.get("redirect_uri"),
                        "code_challenge": params.get("code_challenge"),
                        "code_challenge_method": params.get("code_challenge_method"),
                        "expires_at": time.monotonic() + 60,
                    }
                    location = f"{params['redirect_uri']}?{urlencode({'code': code, 'state': params.get('state', '')})}"
                    self.send_response(302)
                    self.send_header("Location", location)
                    self.end_headers()
                else:
                    self._json({"error": "not_found"}, status=404)

            def do_POST(self) -> None:
                if urlparse(self.path).path != "/token":
                    self._json({"error": "not_found"}, status=404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode()
                params = {k: v[0] for k, v in parse_qs(body).items()}
                # Newer authlib defaults to client_secret_basic (client
                # credentials in an HTTP Basic Authorization header)
                # rather than client_secret_post (in the form body) --
                # this stub's discovery doc advertises both, so honor
                # whichever the client actually used.
                if "client_id" not in params:
                    auth_header = self.headers.get("Authorization", "")
                    if auth_header.startswith("Basic "):
                        decoded = base64.b64decode(auth_header[len("Basic "):]).decode()
                        client_id, _, _client_secret = decoded.partition(":")
                        params["client_id"] = client_id
                code = params.get("code")
                entry = provider._codes.pop(code, None) if code else None
                if entry is None or entry["expires_at"] < time.monotonic():
                    self._json({"error": "invalid_grant"}, status=400)
                    return

                if entry.get("code_challenge") and params.get("code_verifier"):
                    digest = hashlib.sha256(params["code_verifier"].encode()).digest()
                    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
                    if computed != entry["code_challenge"]:
                        self._json({"error": "invalid_grant", "error_description": "PKCE mismatch"}, status=400)
                        return

                now = int(time.time())
                claims = {
                    "iss": issuer_url,
                    "aud": params.get("client_id"),
                    "exp": now + 300,
                    "iat": now,
                    **({"nonce": entry["nonce"]} if entry.get("nonce") else {}),
                    **provider.next_claims,
                }
                id_token = jwt.encode({"alg": "RS256", "kid": _KID}, claims, provider._jwk)
                self._json(
                    {
                        "access_token": secrets.token_urlsafe(16),
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "id_token": id_token,
                    }
                )

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
