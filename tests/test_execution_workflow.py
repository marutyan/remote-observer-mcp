from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "approved-execution.yml"
_REQUEST_DOC = _ROOT / "execution_requests" / "README.md"
_REQUEST_EXAMPLE = _ROOT / "execution_requests" / "example.json"
_RUNBOOK = _ROOT / "deploy" / "execution-runner.md"


def test_execution_workflow_has_narrow_dispatch_and_independent_environment_gate():
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "request_id:" in text
    for forbidden in ("command:", "script:", "argv:", "host:", "path:"):
        assert forbidden not in text
    assert "permissions:\n  contents: read" in text
    assert "environment: remote-execution" in text
    assert "runs-on: [self-hosted, remote-observer]" in text
    assert "remote-observer-exec" in text
    assert "issues." not in text
    assert "github.event.comment" not in text
    assert "eval " not in text


def test_request_storage_and_runner_docs_exist_without_literal_secrets():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (_REQUEST_DOC, _REQUEST_EXAMPLE, _RUNBOOK)
    )
    assert "remote-execution" in combined
    assert "required reviewer" in combined.lower()
    assert "self-hosted" in combined
    assert "sk-" not in combined
    assert "BEGIN PRIVATE KEY" not in combined
    assert "ghp_" not in combined


def test_example_request_is_benign_argv_not_shell():
    text = _REQUEST_EXAMPLE.read_text(encoding="utf-8")
    assert '"mode": "argv"' in text
    assert '"risk": "R1"' in text
    assert '"script"' not in text
