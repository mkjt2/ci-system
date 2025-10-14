"""
End-to-end tests for authenticated CI client operations.

Tests the full authentication flow:
1. Create user and API key via admin CLI
2. Configure client with API key
3. Submit jobs with authentication
4. Verify authentication failures without valid API key

These tests are written TDD-style before implementing client authentication.
"""

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
import requests

# Generate a unique prefix for this test session to avoid inter-run container conflicts
SESSION_ID = os.urandom(3).hex()  # 6-character hex string


@pytest.fixture
def test_db_path():
    """Create a temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="ci_auth_test_")
    os.close(fd)

    # Initialize the database
    import asyncio

    from ci_persistence.sqlite_repository import SQLiteJobRepository

    async def init_db():
        repo = SQLiteJobRepository(path)
        await repo.initialize()
        await repo.close()

    asyncio.run(init_db())

    yield path

    # Clean up test database after test
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def test_user_and_key(test_db_path):
    """Create a test user and API key."""
    # Create user with full environment (including PATH for ci-admin command)
    env = os.environ.copy()
    env["CI_DB_PATH"] = test_db_path

    result = subprocess.run(
        [
            "ci-admin",
            "user",
            "create",
            "--name",
            "Test User",
            "--email",
            "test@example.com",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0

    # Extract user ID
    match = re.search(r"([a-f0-9\-]{36})", result.stdout)
    assert match is not None
    user_id = match.group(1)

    # Create API key
    result = subprocess.run(
        [
            "ci-admin",
            "key",
            "create",
            "--user-id",
            user_id,
            "--name",
            "Test Key",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0

    # Extract API key
    match = re.search(r"(ci_[A-Za-z0-9_-]{40,})", result.stdout)
    assert match is not None
    api_key = match.group(1)

    return {"user_id": user_id, "api_key": api_key}


@pytest.fixture
def worker_id(request):
    """Get the worker ID for parallel test execution."""
    if hasattr(request.config, "workerinput"):
        return request.config.workerinput["workerid"]
    return "master"


def wait_for_server_ready(port, max_wait=10):
    """
    Wait for server to be ready by checking if it responds to requests.

    Note: We don't check a specific endpoint because authentication tests
    will test auth failures. We just check if the server is listening.
    """
    wait_interval = 0.2
    for _ in range(int(max_wait / wait_interval)):
        try:
            # Just try to connect - any response (even 403) means server is up
            requests.get(f"http://localhost:{port}/", timeout=1)
            return  # Server is ready
        except requests.exceptions.ConnectionError:
            pass  # Server not ready yet
        except requests.exceptions.RequestException:
            # Any other exception (including HTTPError) means server is listening
            return
        time.sleep(wait_interval)
    raise RuntimeError(f"Server on port {port} did not become ready within {max_wait} seconds")


@pytest.fixture
def controller_process(test_db_path, worker_id):
    """Start the CI controller and tear it down after the test."""
    if worker_id == "master":
        container_prefix = f"{SESSION_ID}_"
    else:
        container_prefix = f"{SESSION_ID}_{worker_id}_"

    env = os.environ.copy()
    env["CI_DB_PATH"] = test_db_path
    env["CI_CONTAINER_PREFIX"] = container_prefix

    proc = subprocess.Popen(
        ["python", "-m", "ci_controller"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    time.sleep(1)  # Wait for controller to initialize

    if proc.poll() is not None:
        _, stderr = proc.communicate()
        raise RuntimeError(f"Controller crashed during startup. stderr: {stderr.decode()}")

    try:
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.fixture
def server_process(test_db_path, worker_id, controller_process, monkeypatch):
    """Start the CI server and tear it down after the test."""
    if worker_id == "master":
        port = 8000
        container_prefix = f"{SESSION_ID}_"
    else:
        worker_num = int(worker_id.replace("gw", ""))
        port = 8000 + worker_num + 1
        container_prefix = f"{SESSION_ID}_{worker_id}_"

    monkeypatch.setenv("CI_DB_PATH", test_db_path)
    monkeypatch.setenv("CI_SERVER_URL", f"http://localhost:{port}")
    monkeypatch.setenv("CI_CONTAINER_PREFIX", container_prefix)

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

    wait_for_server_ready(port)

    try:
        yield proc
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass

        # Clean up containers
        try:
            cleanup_result = subprocess.run(
                ["docker", "ps", "-a", "--filter", "ancestor=python:3.12-slim", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                check=True,
            )
            for container_name in cleanup_result.stdout.splitlines():
                if container_name.startswith(container_prefix):
                    subprocess.run(["docker", "rm", "-f", container_name], check=False)
        except Exception:
            pass


def run_ci_command(*args, env=None, project="dummy_project"):
    """Helper to run ci commands."""
    project_path = Path(__file__).parent.parent / "fixtures" / project
    cmd_env = os.environ.copy()
    if env:
        cmd_env.update(env)

    return subprocess.run(
        ["ci", *args],
        cwd=str(project_path),
        capture_output=True,
        text=True,
        env=cmd_env,
    )


class TestClientAuthentication:
    """Test suite for client authentication via API keys."""

    def test_submit_without_api_key_fails(self, test_db_path, server_process):
        """Test that submitting without API key returns authentication error."""
        result = run_ci_command(
            "submit", "test", "--async", env={"CI_DB_PATH": test_db_path}
        )

        # Should fail with authentication error
        assert result.returncode == 1
        output = result.stderr.lower()
        assert "authentication" in output or "unauthorized" in output or "403" in output

    def test_submit_with_invalid_api_key_fails(self, test_db_path, server_process):
        """Test that submitting with invalid API key returns authentication error."""
        result = run_ci_command(
            "submit",
            "test",
            "--async",
            env={"CI_DB_PATH": test_db_path, "CI_API_KEY": "ci_invalid_key_12345"},
        )

        # Should fail with authentication error
        assert result.returncode == 1
        output = result.stderr.lower()
        assert "authentication" in output or "unauthorized" in output or "401" in output

    def test_submit_with_valid_api_key_succeeds(
        self, test_db_path, test_user_and_key, server_process
    ):
        """Test that submitting with valid API key succeeds."""
        result = run_ci_command(
            "submit",
            "test",
            "--async",
            env={
                "CI_DB_PATH": test_db_path,
                "CI_API_KEY": test_user_and_key["api_key"],
            },
        )

        # Should succeed and return job ID
        assert result.returncode == 0
        assert "Job submitted:" in result.stdout
        match = re.search(r"Job submitted: ([a-f0-9\-]{36})", result.stdout)
        assert match is not None

    def test_list_jobs_without_api_key_fails(self, test_db_path, server_process):
        """Test that listing jobs without API key returns authentication error."""
        result = run_ci_command("list", env={"CI_DB_PATH": test_db_path})

        # Should fail with authentication error
        assert result.returncode == 1
        output = result.stderr.lower()
        assert "authentication" in output or "unauthorized" in output or "403" in output

    def test_list_jobs_with_api_key_succeeds(self, test_db_path, test_user_and_key, server_process):
        """Test that listing jobs with valid API key succeeds."""
        result = run_ci_command(
            "list",
            "--json",
            env={
                "CI_DB_PATH": test_db_path,
                "CI_API_KEY": test_user_and_key["api_key"],
            },
        )

        # Should succeed (even if empty list)
        assert result.returncode == 0
        jobs = json.loads(result.stdout)
        assert isinstance(jobs, list)

    def test_wait_for_job_without_api_key_fails(self, test_db_path, server_process):
        """Test that waiting for job without API key returns authentication error."""
        fake_job_id = "00000000-0000-0000-0000-000000000000"
        result = run_ci_command("wait", fake_job_id, env={"CI_DB_PATH": test_db_path})

        # Should fail with authentication error (before 404)
        assert result.returncode == 1
        output = result.stderr.lower()
        assert "authentication" in output or "unauthorized" in output or "403" in output

    def test_revoked_api_key_fails(self, test_db_path, test_user_and_key, server_process):
        """Test that using a revoked API key returns authentication error."""
        # First, verify the key works
        result = run_ci_command(
            "list",
            "--json",
            env={
                "CI_DB_PATH": test_db_path,
                "CI_API_KEY": test_user_and_key["api_key"],
            },
        )
        assert result.returncode == 0

        # Revoke the key
        env = os.environ.copy()
        env["CI_DB_PATH"] = test_db_path

        list_result = subprocess.run(
            [
                "ci-admin",
                "key",
                "list",
                "--user-id",
                test_user_and_key["user_id"],
                "--json",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert list_result.returncode == 0
        keys = json.loads(list_result.stdout)
        key_id = keys[0]["id"]

        revoke_result = subprocess.run(
            ["ci-admin", "key", "revoke", key_id],
            capture_output=True,
            text=True,
            env=env,
        )
        assert revoke_result.returncode == 0

        # Now the key should not work
        result = run_ci_command(
            "list",
            "--json",
            env={
                "CI_DB_PATH": test_db_path,
                "CI_API_KEY": test_user_and_key["api_key"],
            },
        )
        assert result.returncode == 1
        output = result.stderr.lower()
        assert "revoked" in output or "invalid" in output or "401" in output


class TestAPIKeyConfiguration:
    """Test suite for API key configuration methods."""

    def test_api_key_from_environment_variable(
        self, test_db_path, test_user_and_key, server_process
    ):
        """Test that API key can be set via CI_API_KEY environment variable."""
        result = run_ci_command(
            "list",
            "--json",
            env={
                "CI_DB_PATH": test_db_path,
                "CI_API_KEY": test_user_and_key["api_key"],
            },
        )

        assert result.returncode == 0

    def test_api_key_from_config_file(self, test_db_path, test_user_and_key, server_process):
        """Test that API key can be set via config file (~/.ci/config)."""
        # Create temporary config file
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / ".ci"
            config_dir.mkdir()
            config_file = config_dir / "config"

            # Write API key to config file
            config_file.write_text(f"api_key={test_user_and_key['api_key']}\n")

            # Set HOME to tmpdir so client reads our config
            env = {
                "CI_DB_PATH": test_db_path,
                "HOME": tmpdir,
            }

            result = run_ci_command("list", "--json", env=env)

            assert result.returncode == 0

    def test_api_key_from_command_line_flag(self, test_db_path, test_user_and_key, server_process):
        """Test that API key can be passed via --api-key flag."""
        result = run_ci_command(
            "list",
            "--json",
            "--api-key",
            test_user_and_key["api_key"],
            env={"CI_DB_PATH": test_db_path},
        )

        assert result.returncode == 0

    def test_command_line_flag_overrides_environment(
        self, test_db_path, test_user_and_key, server_process
    ):
        """Test that --api-key flag overrides CI_API_KEY environment variable."""
        # Set environment to invalid key
        result = run_ci_command(
            "list",
            "--json",
            "--api-key",
            test_user_and_key["api_key"],  # Valid key via flag
            env={
                "CI_DB_PATH": test_db_path,
                "CI_API_KEY": "ci_invalid_key_12345",  # Invalid via env
            },
        )

        # Should succeed because flag overrides env
        assert result.returncode == 0

    def test_missing_api_key_shows_helpful_error(self, test_db_path, server_process):
        """Test that missing API key shows helpful error message."""
        result = run_ci_command("submit", "test", "--async", env={"CI_DB_PATH": test_db_path})

        assert result.returncode == 1
        output = result.stderr

        # Should mention how to set API key
        assert "CI_API_KEY" in output or "api-key" in output or "config" in output


class TestUserIsolation:
    """Test suite for user isolation - users can only see their own jobs."""

    def test_users_see_only_their_own_jobs(self, test_db_path, server_process):
        """Test that users can only see jobs they created."""
        # Create two users with API keys
        env = os.environ.copy()
        env["CI_DB_PATH"] = test_db_path

        user1_result = subprocess.run(
            [
                "ci-admin",
                "user",
                "create",
                "--name",
                "User One",
                "--email",
                "user1@example.com",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        match = re.search(r"([a-f0-9\-]{36})", user1_result.stdout)
        assert match is not None
        user1_id = match.group(1)

        key1_result = subprocess.run(
            [
                "ci-admin",
                "key",
                "create",
                "--user-id",
                user1_id,
                "--name",
                "User 1 Key",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        match = re.search(r"(ci_[A-Za-z0-9_-]{40,})", key1_result.stdout)
        assert match is not None
        api_key1 = match.group(1)

        user2_result = subprocess.run(
            [
                "ci-admin",
                "user",
                "create",
                "--name",
                "User Two",
                "--email",
                "user2@example.com",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        match = re.search(r"([a-f0-9\-]{36})", user2_result.stdout)
        assert match is not None
        user2_id = match.group(1)

        key2_result = subprocess.run(
            [
                "ci-admin",
                "key",
                "create",
                "--user-id",
                user2_id,
                "--name",
                "User 2 Key",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        match = re.search(r"(ci_[A-Za-z0-9_-]{40,})", key2_result.stdout)
        assert match is not None
        api_key2 = match.group(1)

        # User 1 submits a job
        submit1 = run_ci_command(
            "submit",
            "test",
            "--async",
            env={"CI_DB_PATH": test_db_path, "CI_API_KEY": api_key1},
        )
        assert submit1.returncode == 0
        match = re.search(r"Job submitted: ([a-f0-9\-]{36})", submit1.stdout)
        assert match is not None
        job1_id = match.group(1)

        # User 2 submits a job
        submit2 = run_ci_command(
            "submit",
            "test",
            "--async",
            env={"CI_DB_PATH": test_db_path, "CI_API_KEY": api_key2},
        )
        assert submit2.returncode == 0
        match = re.search(r"Job submitted: ([a-f0-9\-]{36})", submit2.stdout)
        assert match is not None
        job2_id = match.group(1)

        # User 1 lists jobs - should only see their own
        list1 = run_ci_command(
            "list",
            "--json",
            env={"CI_DB_PATH": test_db_path, "CI_API_KEY": api_key1},
        )
        assert list1.returncode == 0
        jobs1 = json.loads(list1.stdout)
        job_ids1 = [j["job_id"] for j in jobs1]
        assert job1_id in job_ids1
        assert job2_id not in job_ids1

        # User 2 lists jobs - should only see their own
        list2 = run_ci_command(
            "list",
            "--json",
            env={"CI_DB_PATH": test_db_path, "CI_API_KEY": api_key2},
        )
        assert list2.returncode == 0
        jobs2 = json.loads(list2.stdout)
        job_ids2 = [j["job_id"] for j in jobs2]
        assert job2_id in job_ids2
        assert job1_id not in job_ids2

    def test_user_cannot_access_other_users_job(self, test_db_path, server_process):
        """Test that a user cannot wait for another user's job."""
        # Create two users
        env = os.environ.copy()
        env["CI_DB_PATH"] = test_db_path

        user1_result = subprocess.run(
            [
                "ci-admin",
                "user",
                "create",
                "--name",
                "User One",
                "--email",
                "user1@example.com",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        match = re.search(r"([a-f0-9\-]{36})", user1_result.stdout)
        assert match is not None
        user1_id = match.group(1)

        key1_result = subprocess.run(
            [
                "ci-admin",
                "key",
                "create",
                "--user-id",
                user1_id,
                "--name",
                "User 1 Key",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        match = re.search(r"(ci_[A-Za-z0-9_-]{40,})", key1_result.stdout)
        assert match is not None
        api_key1 = match.group(1)

        user2_result = subprocess.run(
            [
                "ci-admin",
                "user",
                "create",
                "--name",
                "User Two",
                "--email",
                "user2@example.com",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        match = re.search(r"([a-f0-9\-]{36})", user2_result.stdout)
        assert match is not None
        user2_id = match.group(1)

        key2_result = subprocess.run(
            [
                "ci-admin",
                "key",
                "create",
                "--user-id",
                user2_id,
                "--name",
                "User 2 Key",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        match = re.search(r"(ci_[A-Za-z0-9_-]{40,})", key2_result.stdout)
        assert match is not None
        api_key2 = match.group(1)

        # User 1 submits a job
        submit1 = run_ci_command(
            "submit",
            "test",
            "--async",
            env={"CI_DB_PATH": test_db_path, "CI_API_KEY": api_key1},
        )
        assert submit1.returncode == 0
        match = re.search(r"Job submitted: ([a-f0-9\-]{36})", submit1.stdout)
        assert match is not None
        job1_id = match.group(1)

        # User 2 tries to wait for User 1's job
        wait_result = run_ci_command(
            "wait",
            job1_id,
            env={"CI_DB_PATH": test_db_path, "CI_API_KEY": api_key2},
        )

        # Should fail with access denied
        assert wait_result.returncode == 1
        output = wait_result.stderr.lower()
        assert "access denied" in output or "forbidden" in output or "403" in output
