"""
SQLite implementation of the job repository.

Uses aiosqlite for async operations and provides thread-safe access.
Can be easily replaced with PostgreSQL/MySQL implementations.
"""

from datetime import datetime

import aiosqlite

from ci_common.models import APIKey, Job, JobEvent, User
from ci_common.repository import JobRepository


class SQLiteJobRepository(JobRepository):
    """
    SQLite-based job storage implementation.

    Uses a single database file with multiple tables:
    - users: Stores user accounts
    - api_keys: Stores API keys (hashed) with foreign key to users
    - jobs: Stores job metadata with foreign key to users
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
        - users table: User accounts (id, name, email, created_at, is_active)
        - api_keys table: API keys with foreign key to users
        - jobs table: Job metadata and status with foreign key to users
        - events table: Sequential events for each job
        """
        conn = await self._get_connection()

        # Create users table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
        """)

        # Create API keys table with foreign key to users
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                key_hash TEXT UNIQUE NOT NULL,
                name TEXT,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Create index on key_hash for faster lookups
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash
            ON api_keys(key_hash)
        """)

        # Check if jobs table exists and has user_id column
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM pragma_table_info('jobs') WHERE name='user_id'"
        )
        row = await cursor.fetchone()
        has_user_id = row is not None and row[0] > 0

        if not has_user_id:
            # Create jobs table with user_id
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    success INTEGER,
                    start_time TEXT,
                    end_time TEXT,
                    container_id TEXT,
                    zip_file_path TEXT,
                    user_id TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
        else:
            # Table exists with user_id, just ensure it exists
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    success INTEGER,
                    start_time TEXT,
                    end_time TEXT,
                    container_id TEXT,
                    zip_file_path TEXT,
                    user_id TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

        # Create index on user_id for faster job filtering
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_user_id
            ON jobs(user_id)
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
            INSERT INTO jobs (id, status, success, start_time, end_time, container_id, zip_file_path, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.status,
                job.success,
                job.start_time.isoformat() if job.start_time else None,
                job.end_time.isoformat() if job.end_time else None,
                job.container_id,
                job.zip_file_path,
                job.user_id,
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
            "SELECT id, status, success, start_time, end_time, container_id, zip_file_path, user_id FROM jobs WHERE id = ?",
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
            user_id,
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
            user_id=user_id,
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
            SELECT id, status, success, start_time, end_time, container_id, zip_file_path, user_id
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
                user_id,
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
                    user_id=user_id,
                    events=[],  # Don't load events for listing efficiency
                )
            )

        return jobs

    async def list_user_jobs(self, user_id: str) -> list[Job]:
        """
        List all jobs belonging to a specific user.

        Args:
            user_id: UUID of the user

        Returns:
            List of Job objects owned by the user
        """
        conn = await self._get_connection()

        cursor = await conn.execute(
            """
            SELECT id, status, success, start_time, end_time, container_id, zip_file_path, user_id
            FROM jobs
            WHERE user_id = ?
            ORDER BY start_time DESC
            """,
            (user_id,),
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
                user_id,
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
                    user_id=user_id,
                    events=[],  # Don't load events for listing efficiency
                )
            )

        return jobs

    # User management methods

    async def create_user(self, user: User) -> None:
        """
        Create a new user in the database.

        Args:
            user: User object to persist

        Raises:
            Exception: If user with same email already exists
        """
        conn = await self._get_connection()

        await conn.execute(
            """
            INSERT INTO users (id, name, email, created_at, is_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user.id,
                user.name,
                user.email,
                user.created_at.isoformat(),
                1 if user.is_active else 0,
            ),
        )
        await conn.commit()

    async def get_user(self, user_id: str) -> User | None:
        """
        Retrieve a user by their ID.

        Args:
            user_id: UUID of the user to retrieve

        Returns:
            User object if found, None otherwise
        """
        conn = await self._get_connection()

        cursor = await conn.execute(
            "SELECT id, name, email, created_at, is_active FROM users WHERE id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        user_id, name, email, created_at_str, is_active = row
        return User(
            id=user_id,
            name=name,
            email=email,
            created_at=datetime.fromisoformat(created_at_str),
            is_active=bool(is_active),
        )

    async def get_user_by_email(self, email: str) -> User | None:
        """
        Retrieve a user by their email address.

        Args:
            email: Email address of the user

        Returns:
            User object if found, None otherwise
        """
        conn = await self._get_connection()

        cursor = await conn.execute(
            "SELECT id, name, email, created_at, is_active FROM users WHERE email = ?",
            (email,),
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        user_id, name, email, created_at_str, is_active = row
        return User(
            id=user_id,
            name=name,
            email=email,
            created_at=datetime.fromisoformat(created_at_str),
            is_active=bool(is_active),
        )

    async def list_users(self) -> list[User]:
        """
        List all users in the system.

        Returns:
            List of User objects
        """
        conn = await self._get_connection()

        cursor = await conn.execute(
            """
            SELECT id, name, email, created_at, is_active
            FROM users
            ORDER BY created_at DESC
            """
        )

        rows = await cursor.fetchall()

        users = []
        for row in rows:
            user_id, name, email, created_at_str, is_active = row
            users.append(
                User(
                    id=user_id,
                    name=name,
                    email=email,
                    created_at=datetime.fromisoformat(created_at_str),
                    is_active=bool(is_active),
                )
            )

        return users

    async def update_user_active_status(self, user_id: str, is_active: bool) -> None:
        """
        Update a user's active status (for deactivation/reactivation).

        Args:
            user_id: UUID of the user
            is_active: New active status

        Raises:
            Exception: If user not found
        """
        conn = await self._get_connection()

        await conn.execute(
            "UPDATE users SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, user_id),
        )
        await conn.commit()

    # API Key management methods

    async def create_api_key(self, api_key: APIKey) -> None:
        """
        Create a new API key in the database.

        Args:
            api_key: APIKey object to persist (with hashed key)

        Raises:
            Exception: If API key with same hash already exists
        """
        conn = await self._get_connection()

        await conn.execute(
            """
            INSERT INTO api_keys (id, user_id, key_hash, name, created_at, last_used_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                api_key.id,
                api_key.user_id,
                api_key.key_hash,
                api_key.name,
                api_key.created_at.isoformat(),
                api_key.last_used_at.isoformat() if api_key.last_used_at else None,
                1 if api_key.is_active else 0,
            ),
        )
        await conn.commit()

    async def get_api_key_by_hash(self, key_hash: str) -> APIKey | None:
        """
        Retrieve an API key by its hash.

        Args:
            key_hash: SHA-256 hash of the API key

        Returns:
            APIKey object if found, None otherwise
        """
        conn = await self._get_connection()

        cursor = await conn.execute(
            """
            SELECT id, user_id, key_hash, name, created_at, last_used_at, is_active
            FROM api_keys
            WHERE key_hash = ?
            """,
            (key_hash,),
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        (
            key_id,
            user_id,
            key_hash,
            name,
            created_at_str,
            last_used_at_str,
            is_active,
        ) = row
        return APIKey(
            id=key_id,
            user_id=user_id,
            key_hash=key_hash,
            name=name,
            created_at=datetime.fromisoformat(created_at_str),
            last_used_at=datetime.fromisoformat(last_used_at_str)
            if last_used_at_str
            else None,
            is_active=bool(is_active),
        )

    async def list_user_api_keys(self, user_id: str) -> list[APIKey]:
        """
        List all API keys belonging to a specific user.

        Args:
            user_id: UUID of the user

        Returns:
            List of APIKey objects owned by the user
        """
        conn = await self._get_connection()

        cursor = await conn.execute(
            """
            SELECT id, user_id, key_hash, name, created_at, last_used_at, is_active
            FROM api_keys
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        )

        rows = await cursor.fetchall()

        api_keys = []
        for row in rows:
            (
                key_id,
                user_id,
                key_hash,
                name,
                created_at_str,
                last_used_at_str,
                is_active,
            ) = row
            api_keys.append(
                APIKey(
                    id=key_id,
                    user_id=user_id,
                    key_hash=key_hash,
                    name=name,
                    created_at=datetime.fromisoformat(created_at_str),
                    last_used_at=datetime.fromisoformat(last_used_at_str)
                    if last_used_at_str
                    else None,
                    is_active=bool(is_active),
                )
            )

        return api_keys

    async def revoke_api_key(self, key_id: str) -> None:
        """
        Revoke an API key (set is_active to False).

        Args:
            key_id: UUID of the API key to revoke

        Raises:
            Exception: If API key not found
        """
        conn = await self._get_connection()

        await conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE id = ?",
            (key_id,),
        )
        await conn.commit()

    async def update_api_key_last_used(self, key_id: str, timestamp: datetime) -> None:
        """
        Update the last_used_at timestamp for an API key.

        Args:
            key_id: UUID of the API key
            timestamp: Timestamp of last use

        Raises:
            Exception: If API key not found
        """
        conn = await self._get_connection()

        await conn.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
            (timestamp.isoformat(), key_id),
        )
        await conn.commit()
