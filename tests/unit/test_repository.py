"""
Unit tests for the repository layer.

Tests the abstract repository interface and SQLite implementation
to ensure proper job persistence and retrieval.
"""

import os
import tempfile
from datetime import datetime

import pytest
from ci_common.models import Job, JobEvent
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
