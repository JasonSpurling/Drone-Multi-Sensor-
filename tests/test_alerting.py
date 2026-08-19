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
