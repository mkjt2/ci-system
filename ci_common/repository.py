"""
Abstract repository interface for job persistence.

This module defines the contract that any database implementation must follow,
allowing easy swapping between SQLite, PostgreSQL, MySQL, etc.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from .models import Job, JobEvent


class JobRepository(ABC):
    """
    Abstract base class for job storage operations.

    Implementations must provide thread-safe/async-safe access to job data
    and handle their own connection management.
    """

    @abstractmethod
    async def create_job(self, job: Job) -> None:
        """
        Create a new job in the database.

        Args:
            job: Job object to persist

        Raises:
            Exception: If job with same ID already exists
        """
        pass

    @abstractmethod
    async def get_job(self, job_id: str) -> Job | None:
        """
        Retrieve a job by its ID.

        Args:
            job_id: UUID of the job to retrieve

        Returns:
            Job object if found, None otherwise
        """
        pass

    @abstractmethod
    async def update_job_status(
        self,
        job_id: str,
        status: str,
        start_time: datetime | None = None,
        container_id: str | None = None,
    ) -> None:
        """
        Update a job's status and optionally its start time and container ID.

        Args:
            job_id: UUID of the job to update
            status: New status ("queued", "running", "completed", "cancelled", "failed")
            start_time: Optional timestamp when job started running
            container_id: Optional Docker container ID

        Raises:
            Exception: If job not found
        """
        pass

    @abstractmethod
    async def complete_job(
        self, job_id: str, success: bool, end_time: datetime
    ) -> None:
        """
        Mark a job as completed with final result.

        Args:
            job_id: UUID of the job to complete
            success: Whether the job succeeded
            end_time: Timestamp when job completed

        Raises:
            Exception: If job not found
        """
        pass

    @abstractmethod
    async def add_event(self, job_id: str, event: JobEvent) -> None:
        """
        Add an event to a job's history.

        Args:
            job_id: UUID of the job
            event: Event to add

        Raises:
            Exception: If job not found
        """
        pass

    @abstractmethod
    async def get_events(self, job_id: str, from_index: int = 0) -> list[JobEvent]:
        """
        Get events for a job, optionally from a specific index.

        Args:
            job_id: UUID of the job
            from_index: Starting index (0-based) for event retrieval

        Returns:
            List of events from the specified index onward

        Raises:
            Exception: If job not found
        """
        pass

    @abstractmethod
    async def list_jobs(self) -> list[Job]:
        """
        List all jobs (without full event history for efficiency).

        Returns:
            List of Job objects with metadata but empty/minimal events
        """
        pass

    @abstractmethod
    async def initialize(self) -> None:
        """
        Initialize the database (create tables, etc.).

        Called once at application startup.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Close database connections and cleanup resources.

        Called at application shutdown.
        """
        pass
