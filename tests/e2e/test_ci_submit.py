import subprocess
import time
import re
import signal
import json
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
    """Test that 'ci wait <job_id> --all' streams all logs and returns correct exit code."""
    # First submit a job asynchronously
    submit_result = run_ci_test("dummy_project", "submit", "test", "--async")
    assert submit_result.returncode == 0
    match = re.search(r"Job submitted: ([a-f0-9\-]{36})", submit_result.stdout)
    assert match is not None
    job_id = match.group(1)

    # Wait for the job to complete
    time.sleep(2)  # Give job time to start/complete
    # Use --all to see all logs from beginning
    wait_result = run_ci_test("dummy_project", "wait", job_id, "--all")
    output = wait_result.stdout + wait_result.stderr
    assert wait_result.returncode == 0
    assert "test_add" in output
    assert "test_subtract" in output
    assert "passed" in output.lower()


def test_ci_wait_for_failing_job(server_process):
    """Test that 'ci wait <job_id> --all' returns exit code 1 for failing tests."""
    # Submit a job with failing tests
    submit_result = run_ci_test("failing_project", "submit", "test", "--async")
    assert submit_result.returncode == 0
    match = re.search(r"Job submitted: ([a-f0-9\-]{36})", submit_result.stdout)
    assert match is not None
    job_id = match.group(1)

    # Wait for the job to complete
    time.sleep(2)
    # Use --all to see all logs
    wait_result = run_ci_test("failing_project", "wait", job_id, "--all")
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
        ["ci", "wait", job_id, "--all"],  # Use --all to see logs
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


def test_ci_wait_forward_only(server_process):
    """Test that 'ci wait <job_id>' (without --all) only shows new logs."""
    # First submit a job asynchronously
    submit_result = run_ci_test("dummy_project", "submit", "test", "--async")
    assert submit_result.returncode == 0
    match = re.search(r"Job submitted: ([a-f0-9\-]{36})", submit_result.stdout)
    assert match is not None
    job_id = match.group(1)

    # Wait for the job to complete
    time.sleep(5)  # Increased from 3 to ensure job completes

    # Wait WITHOUT --all (should only see "Job already completed" message)
    wait_result = run_ci_test("dummy_project", "wait", job_id)
    output = wait_result.stdout + wait_result.stderr

    # Should complete successfully
    assert wait_result.returncode == 0

    # Should see "already completed" message (not full test output)
    assert "already completed" in output.lower()

    # Should NOT see the full test output since we joined after completion
    # (This is the key difference from --all)


def test_ci_list(server_process):
    """Test that 'ci list' displays a table of all jobs."""
    # Submit a couple of jobs
    submit1 = run_ci_test("dummy_project", "submit", "test", "--async")
    assert submit1.returncode == 0
    match1 = re.search(r"Job submitted: ([a-f0-9\-]{36})", submit1.stdout)
    assert match1 is not None
    job_id1 = match1.group(1)

    submit2 = run_ci_test("failing_project", "submit", "test", "--async")
    assert submit2.returncode == 0
    match2 = re.search(r"Job submitted: ([a-f0-9\-]{36})", submit2.stdout)
    assert match2 is not None
    job_id2 = match2.group(1)

    # Wait for jobs to complete
    time.sleep(5)

    # Test JSON mode first (easier to parse and verify)
    json_result = run_ci_test("dummy_project", "list", "--json")
    assert json_result.returncode == 0

    # Parse JSON output
    jobs = json.loads(json_result.stdout)
    assert isinstance(jobs, list)
    assert len(jobs) == 2

    # Find our jobs in the list
    job1 = next((j for j in jobs if j["job_id"] == job_id1), None)
    job2 = next((j for j in jobs if j["job_id"] == job_id2), None)

    assert job1 is not None
    assert job1["status"] == "completed"
    assert job1["success"] is True  # dummy_project should pass
    assert job1["start_time"] is not None
    assert job1["end_time"] is not None

    assert job2 is not None
    assert job2["status"] == "completed"
    assert job2["success"] is False  # failing_project should fail
    assert job2["start_time"] is not None
    assert job2["end_time"] is not None

    # Also test human-readable table mode
    table_result = run_ci_test("dummy_project", "list")
    output = table_result.stdout

    assert table_result.returncode == 0

    # Should have table header
    assert "JOB ID" in output
    assert "STATUS" in output
    assert "START TIME" in output
    assert "END TIME" in output
    assert "SUCCESS" in output

    # Should show both job IDs
    assert job_id1 in output
    assert job_id2 in output

    # Should show completed status for both
    assert "completed" in output.lower()

    # Should show success indicators (✓ for pass, ✗ for fail)
    assert "✓" in output
    assert "✗" in output
