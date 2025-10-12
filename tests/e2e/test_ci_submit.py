import subprocess
import time
from pathlib import Path
import pytest


@pytest.fixture
def server_process():
    """Start the CI server and tear it down after the test."""
    proc = subprocess.Popen(
        ["python", "-m", "uvicorn", "ci_server.app:app", "--port", "8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(2)
    yield proc
    proc.terminate()
    proc.wait(timeout=5)


def run_ci_test(project_name):
    """Helper to run ci submit test on a fixture project."""
    project = Path(__file__).parent.parent / "fixtures" / project_name
    return subprocess.run(
        ["ci", "submit", "test"],
        cwd=str(project),
        capture_output=True,
        text=True,
    )


def test_ci_submit_passing_tests(server_process):
    """Test that 'ci submit test' works end-to-end with passing tests."""
    result = run_ci_test("dummy_project")
    output = result.stdout + result.stderr
    assert result.returncode == 0
    assert "test_add" in output
    assert "test_subtract" in output
    assert "passed" in output.lower()


def test_ci_submit_failing_tests(server_process):
    """Test that 'ci submit test' returns exit code 1 when tests fail."""
    result = run_ci_test("failing_project")
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "test_multiply" in output
    assert "test_divide" in output
    assert "failed" in output.lower()


def test_ci_submit_invalid_code(server_process):
    """Test that 'ci submit test' handles invalid Python code gracefully."""
    result = run_ci_test("invalid_project")
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "error" in output.lower() or "syntax" in output.lower()