import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
import requests

# Generate a unique prefix for this test session to avoid inter-run container conflicts
# This is shared across all workers in a single pytest run
SESSION_ID = os.urandom(3).hex()  # 6-character hex string


@pytest.fixture
def test_db_path():
    """Create a temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="ci_test_")
    os.close(fd)
    yield path
    # Clean up test database after test
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def worker_id(request):
    """Get the worker ID for parallel test execution (pytest-xdist)."""
    # When running without xdist, worker_id won't exist in config
    if hasattr(request.config, "workerinput"):
        return request.config.workerinput["workerid"]
    return "master"


def wait_for_server_ready(proc, port, max_wait=10):
    """
    Wait for server to be ready and handle startup failures.

    Args:
        proc: subprocess.Popen instance of the server
        port: Port number the server should be listening on
        max_wait: Maximum seconds to wait

    Raises:
        RuntimeError: If server crashes or doesn't become ready
    """
    wait_interval = 0.2

    for _ in range(int(max_wait / wait_interval)):
        # Check if server crashed
        if proc.poll() is not None:
            # Server crashed, get stderr
            _, stderr = proc.communicate()
            raise RuntimeError(
                f"Server crashed during startup on port {port}. "
                f"stderr: {stderr.decode()}"
            )

        # Try to connect
        try:
            response = requests.get(f"http://localhost:{port}/jobs", timeout=1)
            if response.status_code == 200:
                return  # Server is ready
        except requests.exceptions.RequestException:
            pass

        time.sleep(wait_interval)

    # Server didn't become ready in time
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    raise RuntimeError(
        f"Server on port {port} did not become ready within {max_wait} seconds"
    )


@pytest.fixture
def server_process(test_db_path, worker_id, monkeypatch):
    """Start the CI server and tear it down after the test."""
    # Use a unique port for each worker to support parallel test execution
    # worker_id is 'master' when not running in parallel, or 'gw0', 'gw1', etc. when parallel
    if worker_id == "master":
        port = 8000
        # Use session ID for container prefix even in single-worker mode
        # This prevents inter-run conflicts with containers from previous test runs
        container_prefix = f"{SESSION_ID}_"
    else:
        # Extract worker number from 'gw0', 'gw1', etc.
        worker_num = int(worker_id.replace("gw", ""))
        port = 8000 + worker_num + 1
        # Use session ID + worker ID as container prefix to isolate Docker containers
        # Format: {session_id}_{worker_id}_ (e.g., "a3b5f2_gw0_")
        container_prefix = f"{SESSION_ID}_{worker_id}_"

    # Set environment variables for both server and client
    monkeypatch.setenv("CI_DB_PATH", test_db_path)
    monkeypatch.setenv("CI_SERVER_URL", f"http://localhost:{port}")
    monkeypatch.setenv("CI_CONTAINER_PREFIX", container_prefix)

    # Start server with environment variables
    env = os.environ.copy()
    env["CI_DB_PATH"] = test_db_path
    env["CI_SERVER_URL"] = f"http://localhost:{port}"
    env["CI_CONTAINER_PREFIX"] = container_prefix

    proc = subprocess.Popen(
        ["python", "-m", "uvicorn", "ci_server.app:app", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    # Wait for server to be ready (with health check)
    wait_for_server_ready(proc, port)

    try:
        yield proc
    finally:
        # Teardown: stop server and clean up containers (guaranteed to run)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            # Best effort - kill if terminate fails
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass

        # Clean up all containers with this worker's prefix
        # This ensures no containers are left behind after tests
        try:
            cleanup_result = subprocess.run(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    "ancestor=python:3.12-slim",
                    "--format",
                    "{{.Names}}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if cleanup_result.returncode == 0 and cleanup_result.stdout.strip():
                for container_name in cleanup_result.stdout.strip().split("\n"):
                    if container_name and container_name.startswith(container_prefix):
                        try:
                            subprocess.run(
                                ["docker", "rm", "-f", container_name],
                                capture_output=True,
                                timeout=10,
                            )
                        except Exception:
                            # Best effort - continue cleaning up other containers
                            pass
        except Exception:
            # Best effort - don't fail the test if cleanup fails
            pass


def run_ci_test(project_name, *args, env=None):
    """Helper to run ci commands on a fixture project."""
    project = Path(__file__).parent.parent / "fixtures" / project_name
    return subprocess.run(
        ["ci", *args],
        cwd=str(project),
        capture_output=True,
        text=True,
        env=env,
    )


def wait_for_job_completion(job_id, timeout=15):
    """
    Poll for job completion with timeout.

    This is more reliable than fixed sleep times, especially under heavy load
    during parallel test execution.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        result = run_ci_test("dummy_project", "list", "--json")
        if result.returncode == 0:
            jobs = json.loads(result.stdout)
            job = next((j for j in jobs if j["job_id"] == job_id), None)
            if job and job["status"] == "completed":
                return True
        time.sleep(0.5)
    return False


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

    # Wait for the job to complete (poll instead of fixed sleep for reliability)
    assert wait_for_job_completion(job_id), f"Job {job_id} did not complete in time"

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

    # Wait for jobs to complete (poll instead of fixed sleep for reliability)
    assert wait_for_job_completion(job_id1), f"Job {job_id1} did not complete in time"
    assert wait_for_job_completion(job_id2), f"Job {job_id2} did not complete in time"

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


def test_job_persistence_across_server_restart(server_process, test_db_path):
    """Test that jobs persist when the server is restarted."""
    # Get the current server URL from environment
    server_url = os.environ.get("CI_SERVER_URL", "http://localhost:8000")
    port = server_url.split(":")[-1]

    # Submit a job and wait for it to complete
    submit_result = run_ci_test("dummy_project", "submit", "test", "--async")
    assert submit_result.returncode == 0
    match = re.search(r"Job submitted: ([a-f0-9\-]{36})", submit_result.stdout)
    assert match is not None
    job_id = match.group(1)

    # Wait for job to complete (poll instead of fixed sleep for reliability)
    assert wait_for_job_completion(job_id), f"Job {job_id} did not complete in time"

    # Verify job exists and is completed
    list_result = run_ci_test("dummy_project", "list", "--json")
    assert list_result.returncode == 0
    jobs_before = json.loads(list_result.stdout)
    job_before = next((j for j in jobs_before if j["job_id"] == job_id), None)
    assert job_before is not None
    assert job_before["status"] == "completed"
    assert job_before["success"] is True

    # Restart the server
    server_process.terminate()
    server_process.wait(timeout=5)
    time.sleep(1)

    # Start a new server process with the same database and port
    env = os.environ.copy()
    env["CI_DB_PATH"] = test_db_path
    env["CI_SERVER_URL"] = server_url
    env["CI_CONTAINER_PREFIX"] = os.environ.get("CI_CONTAINER_PREFIX", "")

    new_proc = subprocess.Popen(
        ["python", "-m", "uvicorn", "ci_server.app:app", "--port", port],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    # Wait for server to be ready
    wait_for_server_ready(new_proc, int(port))

    try:
        # Verify job still exists after restart
        list_result = run_ci_test("dummy_project", "list", "--json")
        assert list_result.returncode == 0
        jobs_after = json.loads(list_result.stdout)
        job_after = next((j for j in jobs_after if j["job_id"] == job_id), None)

        assert job_after is not None, f"Job {job_id} not found after server restart"
        assert job_after["status"] == "completed"
        assert job_after["success"] is True
        assert job_after["start_time"] == job_before["start_time"]
        assert job_after["end_time"] == job_before["end_time"]

        # Also verify we can still wait for the job and see logs
        wait_result = run_ci_test("dummy_project", "wait", job_id, "--all")
        output = wait_result.stdout + wait_result.stderr
        assert wait_result.returncode == 0
        assert "test_add" in output
        assert "test_subtract" in output
    finally:
        new_proc.terminate()
        new_proc.wait(timeout=5)
