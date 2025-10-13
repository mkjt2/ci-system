"""
Data models for CI job storage.

These models represent the domain objects used throughout the application,
independent of the underlying storage mechanism.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class JobEvent:
    """
    Represents a single event in a job's lifecycle.

    Events are emitted during job execution (logs, completion, errors).
    """

    type: str  # "log" or "complete"
    data: str | None = None  # Log message for "log" type
    success: bool | None = None  # Result for "complete" type
    timestamp: datetime | None = None  # When the event occurred

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary format (for JSON serialization)."""
        result: dict[str, Any] = {"type": self.type}
        if self.data is not None:
            result["data"] = self.data
        if self.success is not None:
            result["success"] = self.success
        return result

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], timestamp: datetime | None = None
    ) -> "JobEvent":
        """Create event from dictionary format."""
        return cls(
            type=data["type"],
            data=data.get("data"),
            success=data.get("success"),
            timestamp=timestamp,
        )


@dataclass
class Job:
    """
    Represents a CI test job with its metadata and execution history.

    Jobs progress through states: queued -> running -> completed
    Additional states: cancelled, failed
    """

    id: str
    status: str  # "queued", "running", "completed", "cancelled", or "failed"
    events: list[JobEvent] = field(default_factory=list)
    success: bool | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    container_id: str | None = None  # Docker container ID for this job

    def to_dict(self) -> dict[str, Any]:
        """Convert job to dictionary format (for API responses)."""
        return {
            "id": self.id,
            "status": self.status,
            "events": [event.to_dict() for event in self.events],
            "success": self.success,
            "start_time": self.start_time.isoformat() + "Z"
            if self.start_time
            else None,
            "end_time": self.end_time.isoformat() + "Z" if self.end_time else None,
        }

    def to_summary_dict(self) -> dict[str, Any]:
        """Convert job to summary format (without events, for listings)."""
        return {
            "job_id": self.id,
            "status": self.status,
            "success": self.success,
            "start_time": self.start_time.isoformat() + "Z"
            if self.start_time
            else None,
            "end_time": self.end_time.isoformat() + "Z" if self.end_time else None,
        }
