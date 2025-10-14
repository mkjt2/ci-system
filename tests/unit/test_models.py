"""
Unit tests for ci_common.models.

Tests the Job and JobEvent data models to ensure proper serialization,
deserialization, and edge case handling.
"""

from datetime import datetime, timezone

from ci_common.models import Job, JobEvent


class TestJobEvent:
    """Test suite for JobEvent class."""

    def test_log_event_to_dict(self):
        """Test that log events serialize correctly to dict."""
        event = JobEvent(type="log", data="Test output line\n")
        result = event.to_dict()

        assert result == {"type": "log", "data": "Test output line\n"}
        assert "success" not in result  # Should not include None success
        assert "timestamp" not in result  # Timestamps not included in dict

    def test_complete_event_to_dict_success(self):
        """Test that successful complete events serialize correctly."""
        event = JobEvent(type="complete", success=True)
        result = event.to_dict()

        assert result == {"type": "complete", "success": True}
        assert "data" not in result  # Should not include None data

    def test_complete_event_to_dict_failure(self):
        """Test that failed complete events serialize correctly."""
        event = JobEvent(type="complete", success=False)
        result = event.to_dict()

        assert result == {"type": "complete", "success": False}

    def test_event_with_all_fields(self):
        """Test event with both data and success fields."""
        timestamp = datetime.now(timezone.utc)
        event = JobEvent(
            type="complete", data="All tests passed!", success=True, timestamp=timestamp
        )
        result = event.to_dict()

        assert result == {
            "type": "complete",
            "data": "All tests passed!",
            "success": True,
        }
        # Note: timestamp is stored but not serialized to dict

    def test_from_dict_log_event(self):
        """Test creating log event from dictionary."""
        data = {"type": "log", "data": "Test output"}
        timestamp = datetime.now(timezone.utc)

        event = JobEvent.from_dict(data, timestamp=timestamp)

        assert event.type == "log"
        assert event.data == "Test output"
        assert event.success is None
        assert event.timestamp == timestamp

    def test_from_dict_complete_event(self):
        """Test creating complete event from dictionary."""
        data = {"type": "complete", "success": True}

        event = JobEvent.from_dict(data)

        assert event.type == "complete"
        assert event.success is True
        assert event.data is None
        assert event.timestamp is None

    def test_from_dict_minimal(self):
        """Test creating event from minimal dictionary (only type)."""
        data = {"type": "error"}

        event = JobEvent.from_dict(data)

        assert event.type == "error"
        assert event.data is None
        assert event.success is None
        assert event.timestamp is None

    def test_round_trip_conversion(self):
        """Test that to_dict -> from_dict preserves data."""
        original = JobEvent(type="log", data="Test message", success=None)
        as_dict = original.to_dict()
        restored = JobEvent.from_dict(as_dict)

        assert restored.type == original.type
        assert restored.data == original.data
        assert restored.success == original.success
        # Note: timestamp is lost in round-trip since it's not in dict


class TestJob:
    """Test suite for Job class."""

    def test_minimal_job_to_dict(self):
        """Test serializing a minimal job (just id and status)."""
        job = Job(id="test-job-1", status="queued")
        result = job.to_dict()

        assert result == {
            "id": "test-job-1",
            "status": "queued",
            "events": [],
            "success": None,
            "start_time": None,
            "end_time": None,
        }

    def test_job_with_timestamps_to_dict(self):
        """Test that timestamps are serialized to ISO format with Z suffix."""
        start = datetime(2024, 1, 15, 10, 30, 45, tzinfo=timezone.utc)
        end = datetime(2024, 1, 15, 10, 35, 20, tzinfo=timezone.utc)

        job = Job(
            id="test-job-2",
            status="completed",
            success=True,
            start_time=start,
            end_time=end,
        )
        result = job.to_dict()

        # Note: isoformat() on UTC datetime includes +00:00, then Z is appended
        assert result["start_time"] == "2024-01-15T10:30:45+00:00Z"
        assert result["end_time"] == "2024-01-15T10:35:20+00:00Z"

    def test_job_with_events_to_dict(self):
        """Test that job events are serialized correctly."""
        job = Job(
            id="test-job-3",
            status="completed",
            events=[
                JobEvent(type="log", data="Starting tests\n"),
                JobEvent(type="log", data="test_example.py::test_one PASSED\n"),
                JobEvent(type="complete", success=True),
            ],
            success=True,
        )
        result = job.to_dict()

        assert len(result["events"]) == 3
        assert result["events"][0] == {"type": "log", "data": "Starting tests\n"}
        assert result["events"][1] == {
            "type": "log",
            "data": "test_example.py::test_one PASSED\n",
        }
        assert result["events"][2] == {"type": "complete", "success": True}

    def test_completed_job_to_dict(self):
        """Test serializing a fully populated completed job."""
        start = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 15, 10, 31, 0, tzinfo=timezone.utc)

        job = Job(
            id="550e8400-e29b-41d4-a716-446655440000",
            status="completed",
            events=[
                JobEvent(type="log", data="Test output\n"),
                JobEvent(type="complete", success=True),
            ],
            success=True,
            start_time=start,
            end_time=end,
            container_id="container-123",
            zip_file_path="/tmp/job-550e8400.zip",
        )
        result = job.to_dict()

        assert result["id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert result["status"] == "completed"
        assert result["success"] is True
        assert result["start_time"] == "2024-01-15T10:30:00+00:00Z"
        assert result["end_time"] == "2024-01-15T10:31:00+00:00Z"
        assert len(result["events"]) == 2
        # Note: container_id and zip_file_path are not included in to_dict

    def test_job_to_summary_dict_minimal(self):
        """Test summary format for minimal job."""
        job = Job(id="test-job-4", status="queued")
        result = job.to_summary_dict()

        assert result == {
            "job_id": "test-job-4",
            "status": "queued",
            "success": None,
            "start_time": None,
            "end_time": None,
        }
        assert "events" not in result  # Summary excludes events
        assert "id" not in result  # Summary uses "job_id" instead of "id"

    def test_job_to_summary_dict_completed(self):
        """Test summary format for completed job with all fields."""
        start = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 15, 10, 31, 0, tzinfo=timezone.utc)

        job = Job(
            id="test-job-5",
            status="completed",
            success=True,
            start_time=start,
            end_time=end,
            events=[JobEvent(type="log", data="Hidden in summary")],
        )
        result = job.to_summary_dict()

        assert result == {
            "job_id": "test-job-5",
            "status": "completed",
            "success": True,
            "start_time": "2024-01-15T10:30:00+00:00Z",
            "end_time": "2024-01-15T10:31:00+00:00Z",
        }
        assert "events" not in result

    def test_job_to_summary_dict_failed(self):
        """Test summary format for failed job."""
        start = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 15, 10, 30, 30, tzinfo=timezone.utc)

        job = Job(
            id="test-job-6",
            status="completed",
            success=False,
            start_time=start,
            end_time=end,
        )
        result = job.to_summary_dict()

        assert result["success"] is False
        assert result["status"] == "completed"

    def test_job_states(self):
        """Test various job states are preserved."""
        states = ["queued", "running", "completed", "cancelled", "failed"]

        for state in states:
            job = Job(id=f"job-{state}", status=state)
            assert job.to_dict()["status"] == state
            assert job.to_summary_dict()["status"] == state

    def test_job_with_none_timestamps(self):
        """Test that None timestamps serialize to None (not crash)."""
        job = Job(id="test-job-7", status="queued", start_time=None, end_time=None)
        result = job.to_dict()

        assert result["start_time"] is None
        assert result["end_time"] is None

    def test_job_with_empty_events(self):
        """Test that empty events list serializes correctly."""
        job = Job(id="test-job-8", status="queued", events=[])
        result = job.to_dict()

        assert result["events"] == []

    def test_job_container_and_zip_fields(self):
        """Test that container_id and zip_file_path are stored but not serialized."""
        job = Job(
            id="test-job-9",
            status="running",
            container_id="container-abc-123",
            zip_file_path="/tmp/job-test-job-9.zip",
        )

        # Fields should be accessible
        assert job.container_id == "container-abc-123"
        assert job.zip_file_path == "/tmp/job-test-job-9.zip"

        # But not included in serialization
        result = job.to_dict()
        assert "container_id" not in result
        assert "zip_file_path" not in result

        summary = job.to_summary_dict()
        assert "container_id" not in summary
        assert "zip_file_path" not in summary

    def test_datetime_without_timezone_info(self):
        """Test that naive datetimes (without timezone) still serialize."""
        # Create naive datetime (no timezone)
        start = datetime(2024, 1, 15, 10, 30, 0)

        job = Job(id="test-job-10", status="running", start_time=start)
        result = job.to_dict()

        # Should still produce ISO format (without Z suffix since no timezone)
        assert result["start_time"] == "2024-01-15T10:30:00Z"
