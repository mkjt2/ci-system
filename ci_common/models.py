"""
Data models for CI job storage.

These models represent the domain objects used throughout the application,
independent of the underlying storage mechanism.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
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
class User:
    """
    Represents a user account in the CI system.

    Users own API keys and jobs, providing authentication and authorization.
    """

    id: str  # UUID
    name: str  # Display name
    email: str  # Email address (unique)
    created_at: datetime
    is_active: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert user to dictionary format (for API responses)."""
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "created_at": self.created_at.isoformat() + "Z",
            "is_active": self.is_active,
        }


@dataclass
class APIKey:
    """
    Represents an API key for authentication.

    API keys are hashed before storage (SHA-256). The plaintext key is only
    shown once during creation and must be saved by the user.
    """

    id: str  # UUID (internal ID, not the actual key)
    user_id: str  # Owner of this API key
    key_hash: str  # SHA-256 hash of the actual API key
    name: str | None = None  # Optional description (e.g., "Production Key")
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime | None = None  # Updated on each use
    is_active: bool = True  # For revocation

    def to_dict(self) -> dict[str, Any]:
        """Convert API key to dictionary format (for API responses)."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "created_at": self.created_at.isoformat() + "Z",
            "last_used_at": self.last_used_at.isoformat() + "Z"
            if self.last_used_at
            else None,
            "is_active": self.is_active,
        }


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
    zip_file_path: str | None = None  # Path to stashed zip file (for queued jobs)
    user_id: str | None = None  # Owner of this job (for access control)

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
