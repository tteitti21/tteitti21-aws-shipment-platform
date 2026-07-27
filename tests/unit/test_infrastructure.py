from pathlib import Path


PLATFORM_TEMPLATE = (
    Path(__file__).resolve().parents[2] / "infra" / "platform.yaml"
)
POWERSHELL_REPLAY_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "powershell"
    / "ReplayShipmentRequests.ps1"
)
BASH_REPLAY_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "bash"
    / "replay-shipment-requests.sh"
)


def _eventbridge_input_template_lines(template: str) -> list[list[str]]:
    lines = template.splitlines()
    input_templates: list[list[str]] = []

    for index, line in enumerate(lines):
        if line.strip() != "InputTemplate: |":
            continue

        marker_indent = len(line) - len(line.lstrip())
        content: list[str] = []
        for candidate in lines[index + 1 :]:
            candidate_indent = len(candidate) - len(candidate.lstrip())
            if candidate.strip() and candidate_indent <= marker_indent:
                break
            if candidate.strip():
                content.append(candidate.strip())
        input_templates.append(content)

    return input_templates


def test_eventbridge_email_template_lines_are_quoted_strings() -> None:
    templates = _eventbridge_input_template_lines(
        PLATFORM_TEMPLATE.read_text(encoding="utf-8")
    )

    assert len(templates) == 2
    for template in templates:
        assert template
        assert all(line.startswith('"') and line.endswith('"') for line in template)


def test_failed_email_includes_reason_on_a_valid_text_line() -> None:
    template = PLATFORM_TEMPLATE.read_text(encoding="utf-8")

    assert '"Failure reason: <reason>"' in template


def test_archive_retains_only_shipment_requested_events() -> None:
    template = PLATFORM_TEMPLATE.read_text(encoding="utf-8")
    archive = template.split("  ShipmentRequestArchive:", maxsplit=1)[1].split(
        "\n  ProcessingDeadLetterQueue:", maxsplit=1
    )[0]

    assert "Type: AWS::Events::Archive" in archive
    assert "shipment-event-platform.api" in archive
    assert "ShipmentRequested" in archive
    assert "ShipmentDispatched" not in archive
    assert "ShipmentFailed" not in archive
    assert "RetentionDays: !Ref EventArchiveRetentionDays" in archive
    assert "SourceArn: !GetAtt ShipmentEventBus.Arn" in archive


def test_replay_scripts_require_confirmation_and_filter_to_request_rule() -> None:
    powershell = POWERSHELL_REPLAY_SCRIPT.read_text(encoding="utf-8")
    bash = BASH_REPLAY_SCRIPT.read_text(encoding="utf-8")

    for script in (powershell, bash):
        assert "REPLAY" in script
        assert "ShipmentRequestArchiveArn" in script
        assert "EventBusArn" in script
        assert "ShipmentRequestedRuleArn" in script
        assert "FilterArns" in script


def test_powershell_replay_uses_culture_independent_aws_timestamps() -> None:
    powershell = POWERSHELL_REPLAY_SCRIPT.read_text(encoding="utf-8")

    assert "[Globalization.CultureInfo]::InvariantCulture" in powershell
    assert "\"yyyy-MM-dd'T'HH:mm:ss'Z'\"" in powershell
