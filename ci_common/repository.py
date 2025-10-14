"""
Abstract repository interface for job persistence.

This module defines the contract that any database implementation must follow,
allowing easy swapping between SQLite, PostgreSQL, MySQL, etc.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from .models import APIKey, Job, JobEvent, User


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
    async def list_user_jobs(self, user_id: str) -> list[Job]:
        """
        List all jobs belonging to a specific user.

        Args:
            user_id: UUID of the user

        Returns:
            List of Job objects owned by the user
        """
        pass

    # User management methods

    @abstractmethod
    async def create_user(self, user: User) -> None:
        """
        Create a new user in the database.

        Args:
            user: User object to persist

        Raises:
            Exception: If user with same email already exists
        """
        pass

    @abstractmethod
    async def get_user(self, user_id: str) -> User | None:
        """
        Retrieve a user by their ID.

        Args:
            user_id: UUID of the user to retrieve

        Returns:
            User object if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_user_by_email(self, email: str) -> User | None:
        """
        Retrieve a user by their email address.

        Args:
            email: Email address of the user

        Returns:
            User object if found, None otherwise
        """
        pass

    @abstractmethod
    async def list_users(self) -> list[User]:
        """
        List all users in the system.

        Returns:
            List of User objects
        """
        pass

    @abstractmethod
    async def update_user_active_status(self, user_id: str, is_active: bool) -> None:
        """
        Update a user's active status (for deactivation/reactivation).

        Args:
            user_id: UUID of the user
            is_active: New active status

        Raises:
            Exception: If user not found
        """
        pass

    # API Key management methods

    @abstractmethod
    async def create_api_key(self, api_key: APIKey) -> None:
        """
        Create a new API key in the database.

        Args:
            api_key: APIKey object to persist (with hashed key)

        Raises:
            Exception: If API key with same hash already exists
        """
        pass

    @abstractmethod
    async def get_api_key_by_hash(self, key_hash: str) -> APIKey | None:
        """
        Retrieve an API key by its hash.

        Args:
            key_hash: SHA-256 hash of the API key

        Returns:
            APIKey object if found, None otherwise
        """
        pass

    @abstractmethod
    async def list_user_api_keys(self, user_id: str) -> list[APIKey]:
        """
        List all API keys belonging to a specific user.

        Args:
            user_id: UUID of the user

        Returns:
            List of APIKey objects owned by the user
        """
        pass

    @abstractmethod
    async def revoke_api_key(self, key_id: str) -> None:
        """
        Revoke an API key (set is_active to False).

        Args:
            key_id: UUID of the API key to revoke

        Raises:
            Exception: If API key not found
        """
        pass

    @abstractmethod
    async def update_api_key_last_used(self, key_id: str, timestamp: datetime) -> None:
        """
        Update the last_used_at timestamp for an API key.

        Args:
            key_id: UUID of the API key
            timestamp: Timestamp of last use

        Raises:
            Exception: If API key not found
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
