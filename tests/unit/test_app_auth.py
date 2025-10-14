"""
Unit tests for FastAPI authentication and authorization.

Tests the authentication and authorization logic in the FastAPI endpoints,
ensuring that users must be authenticated and can only access their own jobs.
"""

import tempfile
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from ci_common.models import APIKey, Job, User
from ci_persistence.sqlite_repository import SQLiteJobRepository
from ci_server.app import app, get_repository
from ci_server.auth import generate_api_key, hash_api_key


@pytest.fixture
async def test_db():
    """Create a temporary test database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    import os

    os.close(fd)

    repo = SQLiteJobRepository(path)
    await repo.initialize()

    yield repo

    await repo.close()
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
async def test_users(test_db):
    """Create test users with API keys."""
    # Create two test users
    user1 = User(
        id="user-1",
        name="Alice",
        email="alice@example.com",
        created_at=datetime.now(UTC),
        is_active=True,
    )
    user2 = User(
        id="user-2",
        name="Bob",
        email="bob@example.com",
        created_at=datetime.now(UTC),
        is_active=True,
    )

    await test_db.create_user(user1)
    await test_db.create_user(user2)

    # Create API keys for both users
    key1_plaintext = generate_api_key()
    key2_plaintext = generate_api_key()

    api_key1 = APIKey(
        id="key-1",
        user_id="user-1",
        key_hash=hash_api_key(key1_plaintext),
        name="Alice's Key",
        created_at=datetime.now(UTC),
        is_active=True,
    )
    api_key2 = APIKey(
        id="key-2",
        user_id="user-2",
        key_hash=hash_api_key(key2_plaintext),
        name="Bob's Key",
        created_at=datetime.now(UTC),
        is_active=True,
    )

    await test_db.create_api_key(api_key1)
    await test_db.create_api_key(api_key2)

    return {
        "user1": user1,
        "user2": user2,
        "key1": key1_plaintext,
        "key2": key2_plaintext,
    }


@pytest.fixture
def test_client(test_db):
    """Create a test client with overridden repository."""

    def override_get_repository():
        return test_db

    app.dependency_overrides[get_repository] = override_get_repository

    client = TestClient(app)

    yield client

    # Cleanup
    app.dependency_overrides.clear()


class TestAuthentication:
    """Test suite for authentication."""

    def test_submit_without_auth(self, test_client):
        """Test that submitting without API key returns 403 (FastAPI HTTPBearer behavior)."""
        # Create a dummy zip file
        files = {"file": ("test.zip", b"dummy zip content", "application/zip")}

        response = test_client.post("/submit-async", files=files)

        # HTTPBearer returns 403 when no credentials provided
        assert response.status_code == 403
        assert "detail" in response.json()

    def test_submit_with_invalid_key(self, test_client):
        """Test that submitting with invalid API key returns 401."""
        files = {"file": ("test.zip", b"dummy zip content", "application/zip")}
        headers = {"Authorization": "Bearer ci_invalid_key_1234567890"}

        response = test_client.post("/submit-async", files=files, headers=headers)

        assert response.status_code == 401
        assert "Invalid or revoked API key" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_submit_with_revoked_key(self, test_client, test_db, test_users):
        """Test that revoked API key returns 401."""
        # Revoke user1's API key
        await test_db.revoke_api_key("key-1")

        files = {"file": ("test.zip", b"dummy zip content", "application/zip")}
        headers = {"Authorization": f"Bearer {test_users['key1']}"}

        response = test_client.post("/submit-async", files=files, headers=headers)

        assert response.status_code == 401
        assert "Invalid or revoked API key" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_submit_with_inactive_user(self, test_client, test_db, test_users):
        """Test that inactive user returns 401."""
        # Deactivate user1
        await test_db.update_user_active_status("user-1", False)

        files = {"file": ("test.zip", b"dummy zip content", "application/zip")}
        headers = {"Authorization": f"Bearer {test_users['key1']}"}

        response = test_client.post("/submit-async", files=files, headers=headers)

        assert response.status_code == 401
        assert "User not found or inactive" in response.json()["detail"]

    def test_list_jobs_without_auth(self, test_client):
        """Test that listing jobs without API key returns 403 (FastAPI HTTPBearer behavior)."""
        response = test_client.get("/jobs")

        # HTTPBearer returns 403 when no credentials provided
        assert response.status_code == 403

    def test_get_job_without_auth(self, test_client):
        """Test that getting job status without API key returns 403 (FastAPI HTTPBearer behavior)."""
        response = test_client.get("/jobs/test-job-123")

        # HTTPBearer returns 403 when no credentials provided
        assert response.status_code == 403


class TestAuthorization:
    """Test suite for authorization (job ownership)."""

    @pytest.mark.asyncio
    async def test_list_jobs_shows_only_user_jobs(
        self, test_client, test_db, test_users
    ):
        """Test that users only see their own jobs."""
        # Create jobs for both users
        job1 = Job(id="job-1", status="queued", user_id="user-1")
        job2 = Job(id="job-2", status="queued", user_id="user-1")
        job3 = Job(id="job-3", status="queued", user_id="user-2")

        await test_db.create_job(job1)
        await test_db.create_job(job2)
        await test_db.create_job(job3)

        # User 1 lists jobs
        headers = {"Authorization": f"Bearer {test_users['key1']}"}
        response = test_client.get("/jobs", headers=headers)

        assert response.status_code == 200
        jobs = response.json()
        assert len(jobs) == 2
        job_ids = {job["job_id"] for job in jobs}
        assert "job-1" in job_ids
        assert "job-2" in job_ids
        assert "job-3" not in job_ids

        # User 2 lists jobs
        headers = {"Authorization": f"Bearer {test_users['key2']}"}
        response = test_client.get("/jobs", headers=headers)

        assert response.status_code == 200
        jobs = response.json()
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == "job-3"

    @pytest.mark.asyncio
    async def test_get_job_by_owner_succeeds(self, test_client, test_db, test_users):
        """Test that users can access their own jobs."""
        # Create job for user 1
        job = Job(id="job-alice", status="completed", user_id="user-1", success=True)
        await test_db.create_job(job)

        # User 1 accesses their job
        headers = {"Authorization": f"Bearer {test_users['key1']}"}
        response = test_client.get("/jobs/job-alice", headers=headers)

        assert response.status_code == 200
        job_data = response.json()
        assert job_data["job_id"] == "job-alice"
        assert job_data["status"] == "completed"
        assert job_data["success"] is True

    @pytest.mark.asyncio
    async def test_get_job_by_non_owner_fails(self, test_client, test_db, test_users):
        """Test that users cannot access other users' jobs (403)."""
        # Create job for user 1
        job = Job(id="job-alice", status="completed", user_id="user-1", success=True)
        await test_db.create_job(job)

        # User 2 tries to access user 1's job
        headers = {"Authorization": f"Bearer {test_users['key2']}"}
        response = test_client.get("/jobs/job-alice", headers=headers)

        assert response.status_code == 403
        assert "Access denied" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_get_nonexistent_job_returns_404(
        self, test_client, test_db, test_users
    ):
        """Test that requesting a non-existent job returns 404."""
        headers = {"Authorization": f"Bearer {test_users['key1']}"}
        response = test_client.get("/jobs/nonexistent-job", headers=headers)

        assert response.status_code == 404
        assert "Job not found" in response.json()["detail"]

    # Note: Streaming tests require Docker/container manager and are covered in E2E tests
    # They are skipped here because they hang waiting for containers that don't exist in unit tests


class TestJobCreationWithAuth:
    """Test suite for job creation with authentication."""

    @pytest.mark.asyncio
    async def test_submit_async_creates_job_with_user_id(
        self, test_client, test_db, test_users
    ):
        """Test that submitting a job associates it with the authenticated user."""
        files = {"file": ("test.zip", b"PK\x03\x04" + b"x" * 100, "application/zip")}
        headers = {"Authorization": f"Bearer {test_users['key1']}"}

        response = test_client.post("/submit-async", files=files, headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data

        # Verify job was created with correct user_id
        job = await test_db.get_job(data["job_id"])
        assert job is not None
        assert job.user_id == "user-1"
        assert job.status == "queued"

    @pytest.mark.asyncio
    async def test_different_users_create_separate_jobs(
        self, test_client, test_db, test_users
    ):
        """Test that different users create independent jobs."""
        files = {"file": ("test.zip", b"PK\x03\x04" + b"x" * 100, "application/zip")}

        # User 1 submits a job
        headers1 = {"Authorization": f"Bearer {test_users['key1']}"}
        response1 = test_client.post("/submit-async", files=files, headers=headers1)
        job1_id = response1.json()["job_id"]

        # User 2 submits a job
        headers2 = {"Authorization": f"Bearer {test_users['key2']}"}
        response2 = test_client.post("/submit-async", files=files, headers=headers2)
        job2_id = response2.json()["job_id"]

        # Verify both jobs exist with correct ownership
        job1 = await test_db.get_job(job1_id)
        job2 = await test_db.get_job(job2_id)

        assert job1.user_id == "user-1"
        assert job2.user_id == "user-2"

        # Verify user 1 can only see their job
        headers = {"Authorization": f"Bearer {test_users['key1']}"}
        response = test_client.get("/jobs", headers=headers)
        jobs = response.json()
        job_ids = {job["job_id"] for job in jobs}

        assert job1_id in job_ids
        assert job2_id not in job_ids


class TestAuthenticationErrors:
    """Test suite for various authentication error cases."""

    def test_missing_bearer_prefix(self, test_client, test_users):
        """Test that missing 'Bearer' prefix in Authorization header fails."""
        headers = {"Authorization": test_users["key1"]}  # Missing "Bearer "

        response = test_client.get("/jobs", headers=headers)

        # FastAPI's HTTPBearer will reject this with 403
        assert response.status_code == 403

    def test_malformed_api_key(self, test_client):
        """Test that malformed API key returns 401."""
        headers = {"Authorization": "Bearer not-a-valid-key"}

        response = test_client.get("/jobs", headers=headers)

        assert response.status_code == 401

    def test_empty_authorization_header(self, test_client):
        """Test that empty Authorization header returns 403 (HTTPBearer behavior)."""
        headers = {"Authorization": ""}

        response = test_client.get("/jobs", headers=headers)

        # HTTPBearer returns 403 for empty/malformed authorization header
        assert response.status_code == 403
