import json

import pytest

from app import alerting
from app.models import Incident, IncidentSeverity, IncidentStatus, IncidentType
from app.util import utcnow


def make_incident(severity: IncidentSeverity) -> Incident:
    return Incident(
        incident_uid="uid-1",
        incident_type=IncidentType.ZONE_INCURSION,
        severity=severity,
        status=IncidentStatus.OPEN,
        track_id=42,
        zone_id=7,
        opened_at=utcnow(),
        description="Track 42 entered restricted zone 'Test Zone'",
    )


class _CapturedRequest:
    def __init__(self, request):
        self.url = request.full_url
        self.headers = {k.lower(): v for k, v in request.headers.items()}
        self.body = request.data


@pytest.fixture
def captured_requests(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(_CapturedRequest(request))

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"{}"

        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return calls


def test_slack_disabled_by_default_sends_nothing(captured_requests, monkeypatch):
    monkeypatch.setattr("app.alerting.SLACK_WEBHOOK_URL", "")
    alerting.notify_slack(make_incident(IncidentSeverity.CRITICAL))
    assert captured_requests == []


def test_slack_sends_when_configured_and_above_threshold(captured_requests, monkeypatch):
    monkeypatch.setattr("app.alerting.SLACK_WEBHOOK_URL", "https://hooks.slack.example/xyz")
    monkeypatch.setattr("app.alerting.SLACK_MIN_SEVERITY", "low")
    alerting.notify_slack(make_incident(IncidentSeverity.HIGH))
    assert len(captured_requests) == 1
    assert captured_requests[0].url == "https://hooks.slack.example/xyz"
    body = json.loads(captured_requests[0].body)
    assert "HIGH" in body["text"]


def test_slack_skips_incidents_below_its_threshold(captured_requests, monkeypatch):
    monkeypatch.setattr("app.alerting.SLACK_WEBHOOK_URL", "https://hooks.slack.example/xyz")
    monkeypatch.setattr("app.alerting.SLACK_MIN_SEVERITY", "high")
    alerting.notify_slack(make_incident(IncidentSeverity.MEDIUM))
    assert captured_requests == []


def test_pagerduty_skips_below_default_high_threshold(captured_requests, monkeypatch):
    monkeypatch.setattr("app.alerting.PAGERDUTY_ROUTING_KEY", "routing-key-123")
    monkeypatch.setattr("app.alerting.PAGERDUTY_MIN_SEVERITY", "high")
    alerting.notify_pagerduty(make_incident(IncidentSeverity.MEDIUM))
    assert captured_requests == []


def test_pagerduty_pages_for_critical_with_mapped_severity(captured_requests, monkeypatch):
    monkeypatch.setattr("app.alerting.PAGERDUTY_ROUTING_KEY", "routing-key-123")
    monkeypatch.setattr("app.alerting.PAGERDUTY_MIN_SEVERITY", "high")
    alerting.notify_pagerduty(make_incident(IncidentSeverity.CRITICAL))
    assert len(captured_requests) == 1
    assert captured_requests[0].url == "https://events.pagerduty.com/v2/enqueue"
    body = json.loads(captured_requests[0].body)
    assert body["routing_key"] == "routing-key-123"
    assert body["payload"]["severity"] == "critical"
    assert body["dedup_key"] == "uid-1"


def test_sms_disabled_unless_fully_configured(captured_requests, monkeypatch):
    monkeypatch.setattr("app.alerting.TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setattr("app.alerting.TWILIO_AUTH_TOKEN", "")  # missing token
    monkeypatch.setattr("app.alerting.TWILIO_FROM_NUMBER", "+15550001111")
    monkeypatch.setattr("app.alerting.SMS_TO_NUMBERS", ["+15550002222"])
    alerting.notify_sms(make_incident(IncidentSeverity.CRITICAL))
    assert captured_requests == []


def test_sms_sends_to_every_configured_number_at_critical(captured_requests, monkeypatch):
    monkeypatch.setattr("app.alerting.TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setattr("app.alerting.TWILIO_AUTH_TOKEN", "authtoken")
    monkeypatch.setattr("app.alerting.TWILIO_FROM_NUMBER", "+15550001111")
    monkeypatch.setattr("app.alerting.SMS_TO_NUMBERS", ["+15550002222", "+15550003333"])
    monkeypatch.setattr("app.alerting.SMS_MIN_SEVERITY", "critical")
    alerting.notify_sms(make_incident(IncidentSeverity.CRITICAL))
    assert len(captured_requests) == 2
    urls = {r.url for r in captured_requests}
    assert urls == {"https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"}


def test_sms_skips_below_critical_by_default(captured_requests, monkeypatch):
    monkeypatch.setattr("app.alerting.TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setattr("app.alerting.TWILIO_AUTH_TOKEN", "authtoken")
    monkeypatch.setattr("app.alerting.TWILIO_FROM_NUMBER", "+15550001111")
    monkeypatch.setattr("app.alerting.SMS_TO_NUMBERS", ["+15550002222"])
    monkeypatch.setattr("app.alerting.SMS_MIN_SEVERITY", "critical")
    alerting.notify_sms(make_incident(IncidentSeverity.HIGH))
    assert captured_requests == []


def test_sms_delivery_failure_to_one_number_does_not_block_the_rest(monkeypatch, caplog):
    monkeypatch.setattr("app.alerting.TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setattr("app.alerting.TWILIO_AUTH_TOKEN", "authtoken")
    monkeypatch.setattr("app.alerting.TWILIO_FROM_NUMBER", "+15550001111")
    monkeypatch.setattr("app.alerting.SMS_TO_NUMBERS", ["+15550002222", "+15550003333"])
    monkeypatch.setattr("app.alerting.SMS_MIN_SEVERITY", "critical")

    import urllib.error

    def failing_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", failing_urlopen)

    with caplog.at_level("WARNING"):
        alerting.notify_sms(make_incident(IncidentSeverity.CRITICAL))  # must not raise

    assert "SMS alert to +15550002222 failed" in caplog.text
    assert "SMS alert to +15550003333 failed" in caplog.text


def test_a_failed_channel_does_not_crash_or_block_the_others(captured_requests, monkeypatch):
    monkeypatch.setattr("app.alerting.SLACK_WEBHOOK_URL", "https://hooks.slack.example/xyz")
    monkeypatch.setattr("app.alerting.SLACK_MIN_SEVERITY", "low")
    monkeypatch.setattr("app.alerting.PAGERDUTY_ROUTING_KEY", "routing-key-123")
    monkeypatch.setattr("app.alerting.PAGERDUTY_MIN_SEVERITY", "low")

    def raising_urlopen(request, timeout=None):
        import urllib.error
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", raising_urlopen)
    # Must not raise -- a dead integration shouldn't crash incident handling.
    alerting.notify_escalations(make_incident(IncidentSeverity.HIGH))


def test_invalid_min_severity_env_value_disables_the_channel_safely(captured_requests, monkeypatch):
    monkeypatch.setattr("app.alerting.SLACK_WEBHOOK_URL", "https://hooks.slack.example/xyz")
    monkeypatch.setattr("app.alerting.SLACK_MIN_SEVERITY", "not-a-real-severity")
    alerting.notify_slack(make_incident(IncidentSeverity.CRITICAL))
    assert captured_requests == []


@pytest.fixture
def sent_meshtastic_texts(monkeypatch):
    # _send_meshtastic_text is monkeypatched directly (not the real
    # meshtastic.tcp_interface.TCPInterface) so these tests don't need the
    # real (optional, requirements-meshtastic.txt) package installed --
    # same reasoning as this file's own docstring on _send_meshtastic_text.
    calls = []
    monkeypatch.setattr(alerting, "_send_meshtastic_text", calls.append)
    return calls


def test_meshtastic_disabled_by_default_sends_nothing(sent_meshtastic_texts, monkeypatch):
    monkeypatch.setattr("app.alerting.MESHTASTIC_HOSTNAME", "")
    alerting.notify_meshtastic(make_incident(IncidentSeverity.CRITICAL))
    assert sent_meshtastic_texts == []


def test_meshtastic_sends_when_configured_and_above_threshold(sent_meshtastic_texts, monkeypatch):
    monkeypatch.setattr("app.alerting.MESHTASTIC_HOSTNAME", "192.168.1.50")
    monkeypatch.setattr("app.alerting.MESHTASTIC_MIN_SEVERITY", "low")
    alerting.notify_meshtastic(make_incident(IncidentSeverity.HIGH))
    assert len(sent_meshtastic_texts) == 1
    assert "HIGH" in sent_meshtastic_texts[0]


def test_meshtastic_skips_incidents_below_its_threshold(sent_meshtastic_texts, monkeypatch):
    monkeypatch.setattr("app.alerting.MESHTASTIC_HOSTNAME", "192.168.1.50")
    monkeypatch.setattr("app.alerting.MESHTASTIC_MIN_SEVERITY", "high")
    alerting.notify_meshtastic(make_incident(IncidentSeverity.MEDIUM))
    assert sent_meshtastic_texts == []


def test_meshtastic_truncates_an_overlong_message(sent_meshtastic_texts, monkeypatch):
    monkeypatch.setattr("app.alerting.MESHTASTIC_HOSTNAME", "192.168.1.50")
    monkeypatch.setattr("app.alerting.MESHTASTIC_MIN_SEVERITY", "low")
    incident = make_incident(IncidentSeverity.HIGH)
    incident.description = "x" * 500
    alerting.notify_meshtastic(incident)
    assert len(sent_meshtastic_texts[0]) <= 200


def test_meshtastic_send_failure_does_not_raise(monkeypatch):
    monkeypatch.setattr("app.alerting.MESHTASTIC_HOSTNAME", "192.168.1.50")
    monkeypatch.setattr("app.alerting.MESHTASTIC_MIN_SEVERITY", "low")

    def raising_send(text):
        raise OSError("connection refused")

    monkeypatch.setattr(alerting, "_send_meshtastic_text", raising_send)
    # Must not raise -- a dead/unreachable Meshtastic node shouldn't crash
    # incident handling, same contract as every other channel.
    alerting.notify_meshtastic(make_incident(IncidentSeverity.HIGH))


def test_send_meshtastic_text_uses_the_real_tcp_interface(monkeypatch):
    # The only test in this file exercising _send_meshtastic_text itself
    # rather than monkeypatching it away -- meshtastic (requirements-
    # meshtastic.txt, an optional extra) isn't installed in this
    # environment, so a fake module stands in, the same sys.modules-
    # injection pattern this repo's other optional-hardware adapter tests
    # use (cv2, onvif, sounddevice, ...).
    import sys
    import types

    sent = {}
    closed = []

    class _FakeTCPInterface:
        def __init__(self, hostname, portNumber=None):
            sent["hostname"] = hostname
            sent["port"] = portNumber

        def sendText(self, text, channelIndex=None):
            sent["text"] = text
            sent["channel_index"] = channelIndex

        def close(self):
            closed.append(True)

    fake_tcp_interface = types.ModuleType("meshtastic.tcp_interface")
    fake_tcp_interface.TCPInterface = _FakeTCPInterface
    fake_meshtastic = types.ModuleType("meshtastic")
    fake_meshtastic.tcp_interface = fake_tcp_interface
    monkeypatch.setitem(sys.modules, "meshtastic", fake_meshtastic)
    monkeypatch.setitem(sys.modules, "meshtastic.tcp_interface", fake_tcp_interface)
    monkeypatch.setattr("app.alerting.MESHTASTIC_HOSTNAME", "192.168.1.50")
    monkeypatch.setattr("app.alerting.MESHTASTIC_PORT", 4403)
    monkeypatch.setattr("app.alerting.MESHTASTIC_CHANNEL_INDEX", 2)

    alerting._send_meshtastic_text("test alert")

    assert sent == {"hostname": "192.168.1.50", "port": 4403, "text": "test alert", "channel_index": 2}
    assert closed == [True]  # the interface is always closed, even on the success path


def test_send_meshtastic_text_closes_the_interface_even_if_send_fails(monkeypatch):
    import sys
    import types

    closed = []

    class _FakeTCPInterface:
        def __init__(self, hostname, portNumber=None):
            pass

        def sendText(self, text, channelIndex=None):
            raise OSError("connection refused")

        def close(self):
            closed.append(True)

    fake_tcp_interface = types.ModuleType("meshtastic.tcp_interface")
    fake_tcp_interface.TCPInterface = _FakeTCPInterface
    fake_meshtastic = types.ModuleType("meshtastic")
    fake_meshtastic.tcp_interface = fake_tcp_interface
    monkeypatch.setitem(sys.modules, "meshtastic", fake_meshtastic)
    monkeypatch.setitem(sys.modules, "meshtastic.tcp_interface", fake_tcp_interface)
    monkeypatch.setattr("app.alerting.MESHTASTIC_HOSTNAME", "192.168.1.50")

    with pytest.raises(OSError, match="connection refused"):
        alerting._send_meshtastic_text("test alert")

    assert closed == [True]


def test_post_json_merges_extra_headers_with_the_default_content_type(captured_requests):
    alerting._post_json(
        "https://hooks.example/webhook", {"a": 1}, headers={"Authorization": "Bearer token123"}
    )
    assert len(captured_requests) == 1
    assert captured_requests[0].headers["authorization"] == "Bearer token123"
    assert captured_requests[0].headers["content-type"] == "application/json"
