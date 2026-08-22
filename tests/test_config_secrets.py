"""app/config.py's _read_secret() (the Docker/Kubernetes _FILE-suffix
secrets convention) and get_api_key()/get_api_keys_json() (the file-aware,
rotation-capable accessors app.auth.configured_keys() actually calls) --
the mechanism that lets an API key be rotated by updating a mounted file,
with no restart and no reload logic needed.

Deliberately NOT implemented as a dynamic module __getattr__ on API_KEY/
API_KEYS_JSON themselves -- see get_api_key()'s docstring in app/config.py
for why that broke the moment any test in the (large) rest of the suite
did `monkeypatch.setattr("app.config.API_KEY", ...)`.
"""

import app.config as config


def test_read_secret_prefers_the_file_over_the_plain_env_var(monkeypatch, tmp_path):
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("from-the-file\n")
    monkeypatch.setenv("SOME_SECRET_FILE", str(secret_file))
    monkeypatch.setenv("SOME_SECRET", "from-the-env-var")

    assert config._read_secret("SOME_SECRET") == "from-the-file"


def test_read_secret_strips_whitespace_from_the_file_content(tmp_path, monkeypatch):
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("  padded-value  \n")
    monkeypatch.setenv("SOME_SECRET_FILE", str(secret_file))

    assert config._read_secret("SOME_SECRET") == "padded-value"


def test_read_secret_falls_back_to_the_plain_env_var_when_no_file_is_set(monkeypatch):
    monkeypatch.delenv("SOME_SECRET_FILE", raising=False)
    monkeypatch.setenv("SOME_SECRET", "from-the-env-var")

    assert config._read_secret("SOME_SECRET") == "from-the-env-var"


def test_read_secret_falls_back_to_the_default_when_neither_is_set(monkeypatch):
    monkeypatch.delenv("SOME_SECRET_FILE", raising=False)
    monkeypatch.delenv("SOME_SECRET", raising=False)

    assert config._read_secret("SOME_SECRET") == ""
    assert config._read_secret("SOME_SECRET", default="fallback") == "fallback"


def test_get_api_key_reads_from_its_file_when_the_file_env_var_is_set(monkeypatch, tmp_path):
    key_file = tmp_path / "api_key.txt"
    key_file.write_text("secret-key-from-file\n")
    monkeypatch.setenv("DRONE_API_KEY_FILE", str(key_file))

    assert config.get_api_key() == "secret-key-from-file"


def test_get_api_key_falls_back_to_the_plain_api_key_attribute(monkeypatch):
    monkeypatch.delenv("DRONE_API_KEY_FILE", raising=False)
    monkeypatch.setattr(config, "API_KEY", "plain-attribute-value")

    assert config.get_api_key() == "plain-attribute-value"


def test_get_api_key_updating_the_file_takes_effect_on_the_very_next_read(monkeypatch, tmp_path):
    # This IS the rotation mechanism -- no reload step, no restart.
    key_file = tmp_path / "api_key.txt"
    key_file.write_text("old-key")
    monkeypatch.setenv("DRONE_API_KEY_FILE", str(key_file))

    assert config.get_api_key() == "old-key"

    key_file.write_text("new-key")
    assert config.get_api_key() == "new-key"


def test_get_api_keys_json_reads_from_its_file(monkeypatch, tmp_path):
    keys_file = tmp_path / "api_keys.json"
    keys_file.write_text('{"radar-key": "ingest"}')
    monkeypatch.setenv("DRONE_API_KEYS_FILE", str(keys_file))

    assert config.get_api_keys_json() == '{"radar-key": "ingest"}'


def test_monkeypatching_api_key_directly_still_works_for_code_reading_the_attribute(monkeypatch):
    # app.config.API_KEY/API_KEYS_JSON stay plain, ordinary module
    # attributes -- untouched by this feature -- specifically so the rest
    # of this test suite's `monkeypatch.setattr("app.config.API_KEY", ...)`
    # keeps working exactly as it did before get_api_key() existed.
    monkeypatch.setattr(config, "API_KEY", "monkeypatched-value")
    assert config.API_KEY == "monkeypatched-value"
    # And get_api_key() picks it up too, via its fallback path, as long as
    # no _FILE override is set.
    monkeypatch.delenv("DRONE_API_KEY_FILE", raising=False)
    assert config.get_api_key() == "monkeypatched-value"


def test_configured_keys_picks_up_a_rotated_key_file_without_a_restart(monkeypatch, tmp_path, isolated_db):
    """End-to-end: app.auth.configured_keys() (what every authenticated
    request actually calls) sees a key added to the file immediately.
    """
    import json

    from app.auth import configured_keys

    keys_file = tmp_path / "api_keys.json"
    keys_file.write_text(json.dumps({"old-key": "admin"}))
    monkeypatch.setenv("DRONE_API_KEYS_FILE", str(keys_file))
    monkeypatch.setattr(config, "API_KEYS_JSON", "")
    monkeypatch.setattr(config, "API_KEY", "")

    assert set(configured_keys().keys()) == {"old-key"}

    # Rotate: add the new key alongside the old one (an overlap window),
    # then (in a real rotation) remove the old one once nothing uses it.
    keys_file.write_text(json.dumps({"old-key": "admin", "new-key": "admin"}))
    assert set(configured_keys().keys()) == {"old-key", "new-key"}

    keys_file.write_text(json.dumps({"new-key": "admin"}))
    assert set(configured_keys().keys()) == {"new-key"}
