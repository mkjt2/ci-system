# ci_persistence

Concrete implementations of the job repository interface for persistent storage.

## Purpose

This module provides database-specific implementations of the `JobRepository` interface defined in `ci_common`. It handles all data persistence concerns including schema management, transactions, and query optimization.

## Components

### `sqlite_repository.py`

SQLite implementation of the `JobRepository` interface using `aiosqlite` for async operations.

#### `SQLiteJobRepository`

Thread-safe, async SQLite storage for CI jobs and events.

**Features:**
- Async operations using `aiosqlite`
- Automatic schema creation
- Foreign key constraints for data integrity
- Indexed queries for performance
- Connection pooling and lifecycle management

**Database Schema:**

```sql
-- Jobs table: stores job metadata
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,              -- UUID
    status TEXT NOT NULL,             -- queued/running/completed/failed/cancelled
    success INTEGER,                  -- 0=false, 1=true, NULL=in-progress
    start_time TEXT,                  -- ISO 8601 timestamp
    end_time TEXT,                    -- ISO 8601 timestamp
    container_id TEXT,                -- Docker container ID
    zip_file_path TEXT                -- Path to stashed project zip
);

-- Events table: stores job event history
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    type TEXT NOT NULL,               -- "log" or "complete"
    data TEXT,                        -- Log message
    success INTEGER,                  -- 0=false, 1=true, NULL=N/A
    timestamp TEXT NOT NULL,          -- ISO 8601 timestamp
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

-- Index for fast event queries
CREATE INDEX idx_events_job_id ON events(job_id);
```

**Constructor:**
```python
SQLiteJobRepository(db_path: str = "ci_jobs.db")
```

## Usage Examples

### Basic Setup

```python
from ci_persistence.sqlite_repository import SQLiteJobRepository
from ci_common.models import Job

# Initialize repository
repo = SQLiteJobRepository("ci_jobs.db")
await repo.initialize()  # Creates tables if needed

try:
    # Use repository...
    pass
finally:
    # Always close connections
    await repo.close()
```

### Creating and Querying Jobs

```python
from ci_common.models import Job, JobEvent
from datetime import datetime
import uuid

# Create a new job
job = Job(
    id=str(uuid.uuid4()),
    status="queued",
    zip_file_path="/tmp/project_abc123.zip"
)
await repo.create_job(job)

# Update status when starting
await repo.update_job_status(
    job.id,
    status="running",
    start_time=datetime.utcnow(),
    container_id="docker_container_xyz"
)

# Add log events
log_event = JobEvent(
    type="log",
    data="Installing dependencies...\n",
    timestamp=datetime.utcnow()
)
await repo.add_event(job.id, log_event)

# Mark as completed
await repo.complete_job(
    job.id,
    success=True,
    end_time=datetime.utcnow()
)

# Retrieve job with all events
job = await repo.get_job(job.id)
print(f"Job {job.id}: {job.status}, success={job.success}")
print(f"Total events: {len(job.events)}")

# List all jobs (efficient - no events loaded)
all_jobs = await repo.list_jobs()
for j in all_jobs:
    print(f"{j.id}: {j.status}")
```

### Event Streaming

```python
# Get events from a specific index (for reconnection)
events = await repo.get_events(job_id, from_index=10)
print(f"Retrieved {len(events)} events from index 10 onward")

# Get all events
all_events = await repo.get_events(job_id, from_index=0)
```

### Configuration via Environment

```python
import os

# Custom database path
db_path = os.environ.get("CI_DB_PATH", "ci_jobs.db")
repo = SQLiteJobRepository(db_path)
```

## Design Decisions

### 1. **Async-First Architecture**
Uses `aiosqlite` for non-blocking database operations, enabling the server to handle multiple concurrent requests without blocking.

### 2. **Single Connection Per Instance**
Each repository instance maintains one connection, relying on SQLite's thread-safety and Python's async execution model.

### 3. **Lazy Connection**
Connections are established on first use, not during initialization, to avoid holding resources unnecessarily.

### 4. **Foreign Key Constraints**
Events are linked to jobs with `ON DELETE CASCADE`, ensuring referential integrity and automatic cleanup.

### 5. **Index Optimization**
The `idx_events_job_id` index speeds up common queries like "get all events for job X".

### 6. **ISO 8601 Timestamps**
All timestamps stored as ISO 8601 strings for portability and human readability.

### 7. **Boolean Storage**
SQLite has no boolean type, so we use INTEGER (0=false, 1=true, NULL=null).

### 8. **Efficient Listing**
`list_jobs()` excludes events to avoid expensive JOINs when displaying job summaries.

## Performance Characteristics

| Operation | Time Complexity | Notes |
|-----------|----------------|-------|
| `create_job()` | O(1) | Single INSERT |
| `get_job()` | O(n) | n = number of events for that job |
| `update_job_status()` | O(1) | Single UPDATE by primary key |
| `complete_job()` | O(1) | Single UPDATE by primary key |
| `add_event()` | O(1) | Single INSERT with index update |
| `get_events()` | O(n) | n = number of events from index |
| `list_jobs()` | O(m) | m = total number of jobs |

**Scalability:**
- Works well for hundreds of concurrent jobs
- For thousands of jobs, consider PostgreSQL implementation
- Event storage grows linearly with log volume

## Testing

The repository is tested in `tests/unit/test_repository.py` (~284 lines).

**Test Coverage:**
- Job CRUD operations
- Status transitions
- Event storage and retrieval
- Concurrent operations
- Edge cases (missing jobs, duplicate IDs, etc.)

**Running Tests:**
```bash
pytest tests/unit/test_repository.py -v
```

## Migration to Other Databases

To support PostgreSQL or MySQL, create a new implementation:

```python
# ci_persistence/postgres_repository.py
from ci_common.repository import JobRepository

class PostgresJobRepository(JobRepository):
    """PostgreSQL implementation with connection pooling."""

    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        # Use asyncpg for PostgreSQL

    async def initialize(self) -> None:
        # Create tables in PostgreSQL
        pass

    # Implement all JobRepository methods...
```

**Update server initialization:**
```python
# ci_server/app.py
if db_type == "postgres":
    repository = PostgresJobRepository(connection_string)
else:
    repository = SQLiteJobRepository(db_path)
```

The rest of the application remains unchanged thanks to the `JobRepository` abstraction!

## Dependencies

- `aiosqlite>=0.19.0`: Async SQLite driver
- `ci_common`: Domain models and interface

## Environment Variables

- `CI_DB_PATH`: Custom database file path (default: `ci_jobs.db`)

## Related Modules

- **ci_common**: Defines the `JobRepository` interface this module implements
- **ci_server**: Uses repository for job persistence
- **ci_controller**: Uses repository for job state management

## Known Limitations

1. **Single-file Database**: SQLite stores everything in one file. For distributed systems, use PostgreSQL.
2. **Write Concurrency**: SQLite handles concurrent reads well but serializes writes. Usually not an issue for CI workloads.
3. **No Built-in Replication**: For high availability, migrate to PostgreSQL with replication.
4. **Size Limits**: Practical limit around 100GB. Event logs can grow large over time.

## Future Enhancements

- [ ] Add event retention policy (delete old events)
- [ ] Implement database vacuuming for cleanup
- [ ] Add metrics collection (query timing, table sizes)
- [ ] Support for database backups and restore
- [ ] Query result caching for frequently accessed jobs