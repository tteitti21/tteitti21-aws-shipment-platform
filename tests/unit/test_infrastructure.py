from pathlib import Path


PLATFORM_TEMPLATE = (
    Path(__file__).resolve().parents[2] / "infra" / "platform.yaml"
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
