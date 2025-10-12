import subprocess
import time
import re
import signal
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


def run_ci_test(project_name, *args):
    """Helper to run ci commands on a fixture project."""
    project = Path(__file__).parent.parent / "fixtures" / project_name
    return subprocess.run(
        ["ci", *args],
        cwd=str(project),
        capture_output=True,
        text=True,
    )


def test_ci_submit_passing_tests(server_process):
    """Test that 'ci submit test' works end-to-end with passing tests."""
    result = run_ci_test("dummy_project", "submit", "test")
    output = result.stdout + result.stderr
    assert result.returncode == 0
    assert "test_add" in output
    assert "test_subtract" in output
    assert "passed" in output.lower()


def test_ci_submit_failing_tests(server_process):
    """Test that 'ci submit test' returns exit code 1 when tests fail."""
    result = run_ci_test("failing_project", "submit", "test")
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "test_multiply" in output
    assert "test_divide" in output
    assert "failed" in output.lower()


def test_ci_submit_invalid_code(server_process):
    """Test that 'ci submit test' handles invalid Python code gracefully."""
    result = run_ci_test("invalid_project", "submit", "test")
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "error" in output.lower() or "syntax" in output.lower()


def test_ci_submit_async_mode(server_process):
    """Test that 'ci submit test --async' returns job ID immediately."""
    result = run_ci_test("dummy_project", "submit", "test", "--async")
    output = result.stdout
    assert result.returncode == 0
    assert "Job submitted:" in output
    # Extract job ID (UUID format)
    match = re.search(r"Job submitted: ([a-f0-9\-]{36})", output)
    assert match is not None, "Job ID not found in output"


def test_ci_wait_for_job(server_process):
    """Test that 'ci wait <job_id>' streams logs and returns correct exit code."""
    # First submit a job asynchronously
    submit_result = run_ci_test("dummy_project", "submit", "test", "--async")
    assert submit_result.returncode == 0
    match = re.search(r"Job submitted: ([a-f0-9\-]{36})", submit_result.stdout)
    assert match is not None
    job_id = match.group(1)

    # Wait for the job to complete
    time.sleep(2)  # Give job time to start/complete
    wait_result = run_ci_test("dummy_project", "wait", job_id)
    output = wait_result.stdout + wait_result.stderr
    assert wait_result.returncode == 0
    assert "test_add" in output
    assert "test_subtract" in output
    assert "passed" in output.lower()


def test_ci_wait_for_failing_job(server_process):
    """Test that 'ci wait <job_id>' returns exit code 1 for failing tests."""
    # Submit a job with failing tests
    submit_result = run_ci_test("failing_project", "submit", "test", "--async")
    assert submit_result.returncode == 0
    match = re.search(r"Job submitted: ([a-f0-9\-]{36})", submit_result.stdout)
    assert match is not None
    job_id = match.group(1)

    # Wait for the job to complete
    time.sleep(2)
    wait_result = run_ci_test("failing_project", "wait", job_id)
    output = wait_result.stdout + wait_result.stderr
    assert wait_result.returncode == 1
    assert "test_multiply" in output or "test_divide" in output
    assert "failed" in output.lower()


def test_ci_wait_nonexistent_job(server_process):
    """Test that 'ci wait <job_id>' handles non-existent job IDs gracefully."""
    fake_job_id = "00000000-0000-0000-0000-000000000000"
    result = run_ci_test("dummy_project", "wait", fake_job_id)
    output = result.stdout + result.stderr
    # Should fail with appropriate error
    assert result.returncode == 1
    assert "error" in output.lower() or "not found" in output.lower()


def test_ci_submit_keyboard_interrupt(server_process):
    """Test that 'ci submit test' cancels the job on Ctrl-C."""
    project = Path(__file__).parent.parent / "fixtures" / "dummy_project"
    proc = subprocess.Popen(
        ["ci", "submit", "test"],
        cwd=str(project),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Wait a bit for the job to start streaming
    time.sleep(0.5)

    # Send SIGINT (Ctrl-C)
    proc.send_signal(signal.SIGINT)

    # Wait for process to finish
    stdout, stderr = proc.communicate(timeout=5)
    output = stdout + stderr

    # Should exit with code 130 (SIGINT)
    assert proc.returncode == 130

    # Should have friendly cancellation message
    assert "cancelled" in output.lower()

    # Should NOT have Python stack trace
    assert "Traceback" not in output
    assert "KeyboardInterrupt" not in output

    # TODO: Verify job was actually cancelled on server (would need job tracking)


def test_ci_wait_keyboard_interrupt(server_process):
    """Test that 'ci wait <job_id>' handles Ctrl-C gracefully."""
    # First submit a job asynchronously
    submit_result = run_ci_test("dummy_project", "submit", "test", "--async")
    assert submit_result.returncode == 0
    match = re.search(r"Job submitted: ([a-f0-9\-]{36})", submit_result.stdout)
    assert match is not None
    job_id = match.group(1)

    # Start waiting for the job
    project = Path(__file__).parent.parent / "fixtures" / "dummy_project"
    proc = subprocess.Popen(
        ["ci", "wait", job_id],
        cwd=str(project),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Wait a bit for streaming to start
    time.sleep(0.5)

    # Send SIGINT (Ctrl-C)
    proc.send_signal(signal.SIGINT)

    # Wait for process to finish
    stdout, stderr = proc.communicate(timeout=5)
    output = stdout + stderr

    # Should exit with code 130 (SIGINT)
    assert proc.returncode == 130

    # Should have friendly message
    assert "stopped waiting" in output.lower()
    assert "continues to run" in output.lower()
    assert "ci wait" in output.lower()

    # Should NOT have Python stack trace
    assert "Traceback" not in output
    assert "KeyboardInterrupt" not in output
