"""
SQLite implementation of the job repository.

Uses aiosqlite for async operations and provides thread-safe access.
Can be easily replaced with PostgreSQL/MySQL implementations.
"""

from datetime import datetime

import aiosqlite

from .models import Job, JobEvent
from .repository import JobRepository


class SQLiteJobRepository(JobRepository):
    """
    SQLite-based job storage implementation.

    Uses a single database file with two tables:
    - jobs: Stores job metadata
    - events: Stores job events with foreign key to jobs
    """

    def __init__(self, db_path: str = "ci_jobs.db"):
        """
        Initialize the SQLite repository.

        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None

    async def _get_connection(self) -> aiosqlite.Connection:
        """Get or create database connection."""
        if self._connection is None:
            self._connection = await aiosqlite.connect(self.db_path)
            # Enable foreign key constraints
            await self._connection.execute("PRAGMA foreign_keys = ON")
        return self._connection

    async def initialize(self) -> None:
        """
        Create database tables if they don't exist.

        Schema:
        - jobs table: Stores job metadata and status
        - events table: Stores sequential events for each job
        """
        conn = await self._get_connection()

        # Create jobs table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                success INTEGER,
                start_time TEXT,
                end_time TEXT,
                container_id TEXT,
                zip_file_path TEXT
            )
        """)

        # Create events table with foreign key to jobs
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                type TEXT NOT NULL,
                data TEXT,
                success INTEGER,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            )
        """)

        # Create index on job_id for faster event queries
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_job_id
            ON events(job_id)
        """)

        await conn.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None

    async def create_job(self, job: Job) -> None:
        """
        Create a new job in the database.

        Args:
            job: Job object to persist
        """
        conn = await self._get_connection()

        await conn.execute(
            """
            INSERT INTO jobs (id, status, success, start_time, end_time, container_id, zip_file_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.status,
                job.success,
                job.start_time.isoformat() if job.start_time else None,
                job.end_time.isoformat() if job.end_time else None,
                job.container_id,
                job.zip_file_path,
            ),
        )
        await conn.commit()

    async def get_job(self, job_id: str) -> Job | None:
        """
        Retrieve a job with all its events.

        Args:
            job_id: UUID of the job to retrieve

        Returns:
            Job object if found, None otherwise
        """
        conn = await self._get_connection()

        # Get job metadata
        cursor = await conn.execute(
            "SELECT id, status, success, start_time, end_time, container_id, zip_file_path FROM jobs WHERE id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        # Parse job data
        (
            job_id,
            status,
            success,
            start_time_str,
            end_time_str,
            container_id,
            zip_file_path,
        ) = row
        start_time = datetime.fromisoformat(start_time_str) if start_time_str else None
        end_time = datetime.fromisoformat(end_time_str) if end_time_str else None

        # Get all events for this job
        events = await self.get_events(job_id)

        return Job(
            id=job_id,
            status=status,
            success=bool(success) if success is not None else None,
            start_time=start_time,
            end_time=end_time,
            container_id=container_id,
            zip_file_path=zip_file_path,
            events=events,
        )

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
        """
        conn = await self._get_connection()

        # Build dynamic SQL based on what's being updated
        updates = ["status = ?"]
        params = [status]

        if start_time is not None:
            updates.append("start_time = ?")
            params.append(start_time.isoformat())

        if container_id is not None:
            updates.append("container_id = ?")
            params.append(container_id)

        params.append(job_id)  # WHERE clause parameter

        sql = f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?"
        await conn.execute(sql, params)
        await conn.commit()

    async def complete_job(
        self, job_id: str, success: bool, end_time: datetime
    ) -> None:
        """
        Mark a job as completed with final result.

        Args:
            job_id: UUID of the job to complete
            success: Whether the job succeeded
            end_time: Timestamp when job completed
        """
        conn = await self._get_connection()

        await conn.execute(
            "UPDATE jobs SET status = ?, success = ?, end_time = ? WHERE id = ?",
            ("completed", 1 if success else 0, end_time.isoformat(), job_id),
        )

        await conn.commit()

    async def add_event(self, job_id: str, event: JobEvent) -> None:
        """
        Add an event to a job's history.

        Args:
            job_id: UUID of the job
            event: Event to add
        """
        conn = await self._get_connection()

        timestamp = event.timestamp or datetime.utcnow()

        await conn.execute(
            """
            INSERT INTO events (job_id, type, data, success, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                job_id,
                event.type,
                event.data,
                1 if event.success is True else (0 if event.success is False else None),
                timestamp.isoformat(),
            ),
        )

        await conn.commit()

    async def get_events(self, job_id: str, from_index: int = 0) -> list[JobEvent]:
        """
        Get events for a job, optionally from a specific index.

        Args:
            job_id: UUID of the job
            from_index: Starting index (0-based) for event retrieval

        Returns:
            List of events from the specified index onward
        """
        conn = await self._get_connection()

        cursor = await conn.execute(
            """
            SELECT type, data, success, timestamp
            FROM events
            WHERE job_id = ?
            ORDER BY id
            LIMIT -1 OFFSET ?
            """,
            (job_id, from_index),
        )

        rows = await cursor.fetchall()

        events = []
        for row in rows:
            event_type, data, success_val, timestamp_str = row
            events.append(
                JobEvent(
                    type=event_type,
                    data=data,
                    success=bool(success_val) if success_val is not None else None,
                    timestamp=datetime.fromisoformat(timestamp_str),
                )
            )

        return events

    async def list_jobs(self) -> list[Job]:
        """
        List all jobs (without full event history for efficiency).

        Returns:
            List of Job objects with metadata but empty events list
        """
        conn = await self._get_connection()

        cursor = await conn.execute(
            """
            SELECT id, status, success, start_time, end_time, container_id, zip_file_path
            FROM jobs
            ORDER BY start_time DESC
            """
        )

        rows = await cursor.fetchall()

        jobs = []
        for row in rows:
            (
                job_id,
                status,
                success,
                start_time_str,
                end_time_str,
                container_id,
                zip_file_path,
            ) = row
            jobs.append(
                Job(
                    id=job_id,
                    status=status,
                    success=bool(success) if success is not None else None,
                    start_time=datetime.fromisoformat(start_time_str)
                    if start_time_str
                    else None,
                    end_time=datetime.fromisoformat(end_time_str)
                    if end_time_str
                    else None,
                    container_id=container_id,
                    zip_file_path=zip_file_path,
                    events=[],  # Don't load events for listing efficiency
                )
            )

        return jobs
