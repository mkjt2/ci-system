"""
Unit tests for the repository layer.

Tests the abstract repository interface and SQLite implementation
to ensure proper job persistence and retrieval.
"""

import os
import tempfile
from datetime import UTC, datetime

import pytest

from ci_common.models import APIKey, Job, JobEvent, User
from ci_persistence.sqlite_repository import SQLiteJobRepository


@pytest.fixture
async def temp_db():
    """Create a temporary database file for testing."""
    # Create a temporary file
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    # Initialize repository with temp database
    repo = SQLiteJobRepository(path)
    await repo.initialize()

    yield repo

    # Cleanup
    await repo.close()
    if os.path.exists(path):
        os.unlink(path)


@pytest.mark.asyncio
async def test_create_and_get_job(temp_db):
    """Test creating a job and retrieving it."""
    repo = temp_db

    # Create a job
    job = Job(
        id="test-job-1",
        status="queued",
        start_time=None,
        end_time=None,
        success=None,
    )

    await repo.create_job(job)

    # Retrieve the job
    retrieved = await repo.get_job("test-job-1")

    assert retrieved is not None
    assert retrieved.id == "test-job-1"
    assert retrieved.status == "queued"
    assert retrieved.success is None
    assert retrieved.start_time is None
    assert retrieved.end_time is None
    assert len(retrieved.events) == 0


@pytest.mark.asyncio
async def test_get_nonexistent_job(temp_db):
    """Test retrieving a job that doesn't exist."""
    repo = temp_db

    retrieved = await repo.get_job("nonexistent-job")
    assert retrieved is None


@pytest.mark.asyncio
async def test_update_job_status(temp_db):
    """Test updating job status and start time."""
    repo = temp_db

    # Create a job
    job = Job(id="test-job-2", status="queued")
    await repo.create_job(job)

    # Update status to running
    start_time = datetime.utcnow()
    await repo.update_job_status("test-job-2", "running", start_time=start_time)

    # Retrieve and verify
    retrieved = await repo.get_job("test-job-2")
    assert retrieved is not None
    assert retrieved.status == "running"
    assert retrieved.start_time is not None
    assert abs((retrieved.start_time - start_time).total_seconds()) < 1


@pytest.mark.asyncio
async def test_complete_job(temp_db):
    """Test marking a job as completed."""
    repo = temp_db

    # Create and start a job
    job = Job(id="test-job-3", status="queued")
    await repo.create_job(job)
    await repo.update_job_status("test-job-3", "running", start_time=datetime.utcnow())

    # Complete the job
    end_time = datetime.utcnow()
    await repo.complete_job("test-job-3", success=True, end_time=end_time)

    # Retrieve and verify
    retrieved = await repo.get_job("test-job-3")
    assert retrieved is not None
    assert retrieved.status == "completed"
    assert retrieved.success is True
    assert retrieved.end_time is not None
    assert abs((retrieved.end_time - end_time).total_seconds()) < 1


@pytest.mark.asyncio
async def test_add_and_get_events(temp_db):
    """Test adding events to a job and retrieving them."""
    repo = temp_db

    # Create a job
    job = Job(id="test-job-4", status="running")
    await repo.create_job(job)

    # Add events
    event1 = JobEvent(type="log", data="Starting tests\n", timestamp=datetime.utcnow())
    event2 = JobEvent(type="log", data="Running test 1\n", timestamp=datetime.utcnow())
    event3 = JobEvent(type="complete", success=True, timestamp=datetime.utcnow())

    await repo.add_event("test-job-4", event1)
    await repo.add_event("test-job-4", event2)
    await repo.add_event("test-job-4", event3)

    # Retrieve all events
    events = await repo.get_events("test-job-4")
    assert len(events) == 3
    assert events[0].type == "log"
    assert events[0].data == "Starting tests\n"
    assert events[1].type == "log"
    assert events[1].data == "Running test 1\n"
    assert events[2].type == "complete"
    assert events[2].success is True


@pytest.mark.asyncio
async def test_get_events_from_index(temp_db):
    """Test retrieving events starting from a specific index."""
    repo = temp_db

    # Create a job
    job = Job(id="test-job-5", status="running")
    await repo.create_job(job)

    # Add multiple events
    for i in range(5):
        event = JobEvent(type="log", data=f"Event {i}\n", timestamp=datetime.utcnow())
        await repo.add_event("test-job-5", event)

    # Get events from index 2
    events = await repo.get_events("test-job-5", from_index=2)
    assert len(events) == 3
    assert events[0].data == "Event 2\n"
    assert events[1].data == "Event 3\n"
    assert events[2].data == "Event 4\n"


@pytest.mark.asyncio
async def test_list_jobs(temp_db):
    """Test listing all jobs."""
    repo = temp_db

    # Create multiple jobs
    job1 = Job(
        id="job-1",
        status="completed",
        success=True,
        start_time=datetime.utcnow(),
        end_time=datetime.utcnow(),
    )
    job2 = Job(
        id="job-2",
        status="running",
        start_time=datetime.utcnow(),
    )
    job3 = Job(id="job-3", status="queued")

    await repo.create_job(job1)
    await repo.create_job(job2)
    await repo.create_job(job3)

    # Add some events to job1
    await repo.add_event("job-1", JobEvent(type="log", data="test\n"))

    # List all jobs
    jobs = await repo.list_jobs()
    assert len(jobs) == 3

    # Find each job
    job_ids = {job.id for job in jobs}
    assert "job-1" in job_ids
    assert "job-2" in job_ids
    assert "job-3" in job_ids

    # Verify events are not loaded (for efficiency)
    for job in jobs:
        assert len(job.events) == 0


@pytest.mark.asyncio
async def test_job_persistence_across_connections(temp_db):
    """Test that jobs persist when repository is closed and reopened."""
    repo = temp_db
    db_path = repo.db_path

    # Create a job
    job = Job(
        id="persistent-job",
        status="completed",
        success=True,
        start_time=datetime.utcnow(),
        end_time=datetime.utcnow(),
    )
    await repo.create_job(job)
    await repo.add_event("persistent-job", JobEvent(type="log", data="Test log\n"))
    await repo.add_event("persistent-job", JobEvent(type="complete", success=True))

    # Close the repository
    await repo.close()

    # Create a new repository instance with the same database
    new_repo = SQLiteJobRepository(db_path)
    await new_repo.initialize()

    try:
        # Retrieve the job
        retrieved = await new_repo.get_job("persistent-job")
        assert retrieved is not None
        assert retrieved.id == "persistent-job"
        assert retrieved.status == "completed"
        assert retrieved.success is True
        assert len(retrieved.events) == 2
        assert retrieved.events[0].data == "Test log\n"
        assert retrieved.events[1].success is True
    finally:
        await new_repo.close()


@pytest.mark.asyncio
async def test_job_event_to_dict(temp_db):
    """Test JobEvent serialization to dictionary."""
    event_log = JobEvent(type="log", data="Test message\n")
    event_complete = JobEvent(type="complete", success=True)

    log_dict = event_log.to_dict()
    assert log_dict["type"] == "log"
    assert log_dict["data"] == "Test message\n"
    assert "success" not in log_dict

    complete_dict = event_complete.to_dict()
    assert complete_dict["type"] == "complete"
    assert complete_dict["success"] is True
    assert "data" not in complete_dict


@pytest.mark.asyncio
async def test_job_event_from_dict(temp_db):
    """Test JobEvent deserialization from dictionary."""
    log_dict = {"type": "log", "data": "Test message\n"}
    complete_dict = {"type": "complete", "success": False}

    timestamp = datetime.utcnow()

    log_event = JobEvent.from_dict(log_dict, timestamp=timestamp)
    assert log_event.type == "log"
    assert log_event.data == "Test message\n"
    assert log_event.success is None
    assert log_event.timestamp == timestamp

    complete_event = JobEvent.from_dict(complete_dict, timestamp=timestamp)
    assert complete_event.type == "complete"
    assert complete_event.success is False
    assert complete_event.data is None
    assert complete_event.timestamp == timestamp


# User management tests


@pytest.mark.asyncio
async def test_create_and_get_user(temp_db):
    """Test creating a user and retrieving it."""
    repo = temp_db

    # Create a user
    user = User(
        id="user-123",
        name="Alice",
        email="alice@example.com",
        created_at=datetime.now(UTC),
        is_active=True,
    )

    await repo.create_user(user)

    # Retrieve by ID
    retrieved = await repo.get_user("user-123")
    assert retrieved is not None
    assert retrieved.id == "user-123"
    assert retrieved.name == "Alice"
    assert retrieved.email == "alice@example.com"
    assert retrieved.is_active is True


@pytest.mark.asyncio
async def test_get_user_by_email(temp_db):
    """Test retrieving a user by email address."""
    repo = temp_db

    # Create a user
    user = User(
        id="user-456",
        name="Bob",
        email="bob@example.com",
        created_at=datetime.now(UTC),
    )
    await repo.create_user(user)

    # Retrieve by email
    retrieved = await repo.get_user_by_email("bob@example.com")
    assert retrieved is not None
    assert retrieved.id == "user-456"
    assert retrieved.name == "Bob"
    assert retrieved.email == "bob@example.com"


@pytest.mark.asyncio
async def test_get_nonexistent_user(temp_db):
    """Test retrieving a user that doesn't exist."""
    repo = temp_db

    # By ID
    retrieved = await repo.get_user("nonexistent-user")
    assert retrieved is None

    # By email
    retrieved = await repo.get_user_by_email("nonexistent@example.com")
    assert retrieved is None


@pytest.mark.asyncio
async def test_list_users(temp_db):
    """Test listing all users."""
    repo = temp_db

    # Create multiple users
    user1 = User(
        id="user-1",
        name="Alice",
        email="alice@example.com",
        created_at=datetime.now(UTC),
    )
    user2 = User(
        id="user-2",
        name="Bob",
        email="bob@example.com",
        created_at=datetime.now(UTC),
    )
    user3 = User(
        id="user-3",
        name="Charlie",
        email="charlie@example.com",
        created_at=datetime.now(UTC),
        is_active=False,
    )

    await repo.create_user(user1)
    await repo.create_user(user2)
    await repo.create_user(user3)

    # List all users
    users = await repo.list_users()
    assert len(users) == 3

    # Find each user
    user_ids = {user.id for user in users}
    assert "user-1" in user_ids
    assert "user-2" in user_ids
    assert "user-3" in user_ids

    # Check active/inactive status preserved
    charlie = next(u for u in users if u.id == "user-3")
    assert charlie.is_active is False


@pytest.mark.asyncio
async def test_update_user_active_status(temp_db):
    """Test updating user active status (deactivation/reactivation)."""
    repo = temp_db

    # Create an active user
    user = User(
        id="user-789",
        name="Dave",
        email="dave@example.com",
        created_at=datetime.now(UTC),
        is_active=True,
    )
    await repo.create_user(user)

    # Deactivate the user
    await repo.update_user_active_status("user-789", False)

    # Verify deactivation
    retrieved = await repo.get_user("user-789")
    assert retrieved is not None
    assert retrieved.is_active is False

    # Reactivate the user
    await repo.update_user_active_status("user-789", True)

    # Verify reactivation
    retrieved = await repo.get_user("user-789")
    assert retrieved is not None
    assert retrieved.is_active is True


@pytest.mark.asyncio
async def test_user_email_uniqueness(temp_db):
    """Test that user email must be unique."""
    repo = temp_db

    # Create first user
    user1 = User(
        id="user-001",
        name="Alice",
        email="duplicate@example.com",
        created_at=datetime.now(UTC),
    )
    await repo.create_user(user1)

    # Try to create second user with same email
    user2 = User(
        id="user-002",
        name="Bob",
        email="duplicate@example.com",  # Duplicate!
        created_at=datetime.now(UTC),
    )

    # Should raise an exception
    with pytest.raises(Exception):  # SQLite will raise IntegrityError
        await repo.create_user(user2)


# API Key management tests


@pytest.mark.asyncio
async def test_create_and_get_api_key(temp_db):
    """Test creating an API key and retrieving it by hash."""
    repo = temp_db

    # Create a user first
    user = User(
        id="user-123",
        name="Alice",
        email="alice@example.com",
        created_at=datetime.now(UTC),
    )
    await repo.create_user(user)

    # Create an API key
    api_key = APIKey(
        id="key-456",
        user_id="user-123",
        key_hash="abc123hash",
        name="Test Key",
        created_at=datetime.now(UTC),
        is_active=True,
    )
    await repo.create_api_key(api_key)

    # Retrieve by hash
    retrieved = await repo.get_api_key_by_hash("abc123hash")
    assert retrieved is not None
    assert retrieved.id == "key-456"
    assert retrieved.user_id == "user-123"
    assert retrieved.key_hash == "abc123hash"
    assert retrieved.name == "Test Key"
    assert retrieved.is_active is True
    assert retrieved.last_used_at is None


@pytest.mark.asyncio
async def test_get_nonexistent_api_key(temp_db):
    """Test retrieving an API key that doesn't exist."""
    repo = temp_db

    retrieved = await repo.get_api_key_by_hash("nonexistent-hash")
    assert retrieved is None


@pytest.mark.asyncio
async def test_list_user_api_keys(temp_db):
    """Test listing all API keys for a user."""
    repo = temp_db

    # Create a user
    user = User(
        id="user-789",
        name="Bob",
        email="bob@example.com",
        created_at=datetime.now(UTC),
    )
    await repo.create_user(user)

    # Create multiple API keys for this user
    key1 = APIKey(
        id="key-1",
        user_id="user-789",
        key_hash="hash1",
        name="Key 1",
        created_at=datetime.now(UTC),
    )
    key2 = APIKey(
        id="key-2",
        user_id="user-789",
        key_hash="hash2",
        name="Key 2",
        created_at=datetime.now(UTC),
    )
    key3 = APIKey(
        id="key-3",
        user_id="user-789",
        key_hash="hash3",
        is_active=False,  # Revoked key
        created_at=datetime.now(UTC),
    )

    await repo.create_api_key(key1)
    await repo.create_api_key(key2)
    await repo.create_api_key(key3)

    # List all keys for this user
    keys = await repo.list_user_api_keys("user-789")
    assert len(keys) == 3

    # Find each key
    key_ids = {key.id for key in keys}
    assert "key-1" in key_ids
    assert "key-2" in key_ids
    assert "key-3" in key_ids

    # Check revoked key
    key3_retrieved = next(k for k in keys if k.id == "key-3")
    assert key3_retrieved.is_active is False


@pytest.mark.asyncio
async def test_revoke_api_key(temp_db):
    """Test revoking an API key."""
    repo = temp_db

    # Create user and API key
    user = User(
        id="user-999",
        name="Charlie",
        email="charlie@example.com",
        created_at=datetime.now(UTC),
    )
    await repo.create_user(user)

    api_key = APIKey(
        id="key-revoke",
        user_id="user-999",
        key_hash="revoke_hash",
        created_at=datetime.now(UTC),
        is_active=True,
    )
    await repo.create_api_key(api_key)

    # Revoke the key
    await repo.revoke_api_key("key-revoke")

    # Verify revocation
    retrieved = await repo.get_api_key_by_hash("revoke_hash")
    assert retrieved is not None
    assert retrieved.is_active is False


@pytest.mark.asyncio
async def test_update_api_key_last_used(temp_db):
    """Test updating the last_used_at timestamp."""
    repo = temp_db

    # Create user and API key
    user = User(
        id="user-888",
        name="Dave",
        email="dave@example.com",
        created_at=datetime.now(UTC),
    )
    await repo.create_user(user)

    api_key = APIKey(
        id="key-lastused",
        user_id="user-888",
        key_hash="lastused_hash",
        created_at=datetime.now(UTC),
        last_used_at=None,
    )
    await repo.create_api_key(api_key)

    # Update last_used_at
    timestamp = datetime.now(UTC)
    await repo.update_api_key_last_used("key-lastused", timestamp)

    # Verify update
    retrieved = await repo.get_api_key_by_hash("lastused_hash")
    assert retrieved is not None
    assert retrieved.last_used_at is not None
    assert abs((retrieved.last_used_at - timestamp).total_seconds()) < 1


@pytest.mark.asyncio
async def test_api_key_hash_uniqueness(temp_db):
    """Test that API key hash must be unique."""
    repo = temp_db

    # Create a user
    user = User(
        id="user-001",
        name="Alice",
        email="alice@example.com",
        created_at=datetime.now(UTC),
    )
    await repo.create_user(user)

    # Create first API key
    key1 = APIKey(
        id="key-001",
        user_id="user-001",
        key_hash="duplicate_hash",
        created_at=datetime.now(UTC),
    )
    await repo.create_api_key(key1)

    # Try to create second API key with same hash
    key2 = APIKey(
        id="key-002",
        user_id="user-001",
        key_hash="duplicate_hash",  # Duplicate!
        created_at=datetime.now(UTC),
    )

    # Should raise an exception
    with pytest.raises(Exception):  # SQLite will raise IntegrityError
        await repo.create_api_key(key2)


# Job ownership tests


@pytest.mark.asyncio
async def test_create_job_with_user_id(temp_db):
    """Test creating a job with user ownership."""
    repo = temp_db

    # Create a user
    user = User(
        id="user-123",
        name="Alice",
        email="alice@example.com",
        created_at=datetime.now(UTC),
    )
    await repo.create_user(user)

    # Create a job owned by this user
    job = Job(
        id="job-owned",
        status="queued",
        user_id="user-123",
    )
    await repo.create_job(job)

    # Retrieve and verify ownership
    retrieved = await repo.get_job("job-owned")
    assert retrieved is not None
    assert retrieved.user_id == "user-123"


@pytest.mark.asyncio
async def test_list_user_jobs(temp_db):
    """Test listing jobs filtered by user."""
    repo = temp_db

    # Create two users
    user1 = User(
        id="user-1",
        name="Alice",
        email="alice@example.com",
        created_at=datetime.now(UTC),
    )
    user2 = User(
        id="user-2",
        name="Bob",
        email="bob@example.com",
        created_at=datetime.now(UTC),
    )
    await repo.create_user(user1)
    await repo.create_user(user2)

    # Create jobs for each user
    job1 = Job(id="job-1", status="completed", user_id="user-1", success=True)
    job2 = Job(id="job-2", status="running", user_id="user-1")
    job3 = Job(id="job-3", status="queued", user_id="user-2")
    job4 = Job(id="job-4", status="completed", user_id="user-2", success=False)

    await repo.create_job(job1)
    await repo.create_job(job2)
    await repo.create_job(job3)
    await repo.create_job(job4)

    # List jobs for user-1
    user1_jobs = await repo.list_user_jobs("user-1")
    assert len(user1_jobs) == 2
    user1_job_ids = {job.id for job in user1_jobs}
    assert "job-1" in user1_job_ids
    assert "job-2" in user1_job_ids
    assert "job-3" not in user1_job_ids
    assert "job-4" not in user1_job_ids

    # List jobs for user-2
    user2_jobs = await repo.list_user_jobs("user-2")
    assert len(user2_jobs) == 2
    user2_job_ids = {job.id for job in user2_jobs}
    assert "job-3" in user2_job_ids
    assert "job-4" in user2_job_ids
    assert "job-1" not in user2_job_ids
    assert "job-2" not in user2_job_ids


@pytest.mark.asyncio
async def test_list_user_jobs_empty(temp_db):
    """Test listing jobs for a user with no jobs."""
    repo = temp_db

    # Create a user
    user = User(
        id="user-no-jobs",
        name="Charlie",
        email="charlie@example.com",
        created_at=datetime.now(UTC),
    )
    await repo.create_user(user)

    # List jobs (should be empty)
    jobs = await repo.list_user_jobs("user-no-jobs")
    assert len(jobs) == 0
