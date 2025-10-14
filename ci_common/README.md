# ci_common

Shared domain models and interfaces used across all CI system components.

## Purpose

This module contains the core domain objects and abstract interfaces that define the CI system's data model. It acts as a contract between different layers of the application, ensuring consistency and enabling dependency inversion.

## Components

### `models.py`

Domain objects representing the CI system's core entities:

#### `User`
Represents a user account with authentication and authorization metadata.

```python
@dataclass
class User:
    id: str                      # Unique user identifier (UUID)
    name: str                    # Display name
    email: str                   # Email address (unique)
    created_at: datetime         # Account creation timestamp
    is_active: bool              # Whether user can authenticate
```

**Methods:**
- `to_dict()`: Serialize to dictionary for JSON responses

#### `APIKey`
Represents an API key for user authentication.

```python
@dataclass
class APIKey:
    id: str                      # Unique key identifier (UUID)
    user_id: str                 # Owner user ID
    name: str                    # Key description/name
    key_hash: str                # SHA-256 hash of the key
    created_at: datetime         # Key creation timestamp
    last_used_at: datetime | None # Last authentication timestamp
    is_active: bool              # Whether key can authenticate
```

**Methods:**
- `to_dict()`: Serialize to dictionary (excludes key_hash for security)

**Security Notes:**
- API keys are never stored in plaintext (only SHA-256 hashes)
- Keys use 240-bit entropy with `ci_` prefix
- Keys are shown only once during creation

#### `JobEvent`
Represents a single event in a job's lifecycle (logs, completion, errors).

```python
@dataclass
class JobEvent:
    type: str                    # "log" or "complete"
    data: str | None             # Log message for "log" type
    success: bool | None         # Result for "complete" type
    timestamp: datetime | None   # When the event occurred
```

**Methods:**
- `to_dict()`: Serialize to dictionary for JSON responses
- `from_dict(data, timestamp)`: Deserialize from dictionary

#### `Job`
Represents a CI test job with metadata and execution history.

```python
@dataclass
class Job:
    id: str                      # Unique job identifier (UUID)
    user_id: str                 # Owner user ID (for isolation)
    status: str                  # "queued", "running", "completed", "failed", "cancelled"
    events: list[JobEvent]       # Historical events (mostly unused in controller pattern)
    success: bool | None         # Final result (True=pass, False=fail, None=in-progress)
    start_time: datetime | None  # When job started running
    end_time: datetime | None    # When job completed
    container_id: str | None     # Docker container ID
    zip_file_path: str | None    # Path to stashed project zip file
```

**User Isolation:**
- Each job is associated with a user via `user_id`
- Users can only access their own jobs
- Enforced at both database and API levels

**Job Lifecycle:**
```
queued → running → completed (success=True/False)
                → failed
                → cancelled
```

**Methods:**
- `to_dict()`: Full serialization including events
- `to_summary_dict()`: Lightweight serialization without events (for listings)

### `repository.py`

Abstract base class defining the persistence interface.

#### `JobRepository` (ABC)
Provides a database-agnostic interface for job storage, retrieval, and user management.

**User Management:**
```python
async def create_user(user: User) -> None
async def get_user(user_id: str) -> User | None
async def get_user_by_email(email: str) -> User | None
async def list_users() -> list[User]
async def update_user_active(user_id: str, is_active: bool) -> None
```

**API Key Management:**
```python
async def create_api_key(api_key: APIKey) -> None
async def get_api_key_by_hash(key_hash: str) -> APIKey | None
async def list_api_keys(user_id: str | None = None) -> list[APIKey]
async def revoke_api_key(key_id: str) -> None
async def update_api_key_last_used(key_id: str, timestamp: datetime) -> None
```

**Job Operations:**
```python
async def create_job(job: Job) -> None
async def get_job(job_id: str, user_id: str | None = None) -> Job | None
async def list_jobs(user_id: str | None = None) -> list[Job]
```

**Note:** Job operations now support optional `user_id` parameter for user isolation.

**State Management:**
```python
async def update_job_status(
    job_id: str,
    status: str,
    start_time: datetime | None = None,
    container_id: str | None = None
) -> None

async def complete_job(
    job_id: str,
    success: bool,
    end_time: datetime
) -> None
```

**Event Management:**
```python
async def add_event(job_id: str, event: JobEvent) -> None
async def get_events_since(job_id: str, last_event_id: int) -> list[JobEvent]
```

**Lifecycle:**
```python
async def initialize() -> None  # Setup (create tables, etc.)
async def close() -> None       # Cleanup (close connections)
```

## Design Principles

### 1. **Domain-Driven Design**
Models represent business concepts, not database tables. The domain layer is independent of infrastructure concerns.

### 2. **Dependency Inversion**
The `JobRepository` interface allows high-level modules (server, controller) to depend on abstractions rather than concrete implementations.

```
┌─────────────────┐
│   ci_server     │─────┐
└─────────────────┘     │
                        │ depends on
┌─────────────────┐     │
│  ci_controller  │─────┤
└─────────────────┘     │
                        ▼
                ┌─────────────────┐
                │   ci_common     │ (abstractions)
                │  - Job, Event   │
                │  - Repository   │
                └─────────────────┘
                        ▲
                        │ implements
                        │
                ┌─────────────────┐
                │ ci_persistence  │
                │ - SQLite impl   │
                └─────────────────┘
```

### 3. **Immutability**
Domain objects use `@dataclass` for value semantics and clear structure.

### 4. **Technology Agnostic**
No database-specific code or HTTP framework dependencies. This enables:
- Easy testing with mock repositories
- Switching databases (SQLite → PostgreSQL) without changing business logic
- Reusing models across different interfaces (REST API, CLI, gRPC)

## Usage Examples

### Creating and Managing Jobs

```python
from ci_common.models import Job, JobEvent
from datetime import datetime

# Create a new job
job = Job(
    id="550e8400-e29b-41d4-a716-446655440000",
    status="queued",
    zip_file_path="/tmp/project.zip"
)

# Add events during execution
log_event = JobEvent(
    type="log",
    data="Running tests...\n",
    timestamp=datetime.utcnow()
)

complete_event = JobEvent(
    type="complete",
    success=True,
    timestamp=datetime.utcnow()
)
```

### Using the Repository Interface

```python
from ci_common.repository import JobRepository
from ci_persistence.sqlite_repository import SQLiteJobRepository

# Initialize repository (concrete implementation)
repo: JobRepository = SQLiteJobRepository("jobs.db")
await repo.initialize()

# Create job
await repo.create_job(job)

# Update status as job progresses
await repo.update_job_status(
    job.id,
    "running",
    start_time=datetime.utcnow(),
    container_id="abc123"
)

# Complete job
await repo.complete_job(
    job.id,
    success=True,
    end_time=datetime.utcnow()
)

# Retrieve job
retrieved = await repo.get_job(job.id)
print(f"Job status: {retrieved.status}, success: {retrieved.success}")

# Cleanup
await repo.close()
```

## Dependencies

- Python 3.8+
- No external dependencies (pure Python)

## Testing

Since this module contains only domain models and interfaces, tests should focus on:
- Serialization/deserialization (`to_dict()`, `from_dict()`)
- Data validation
- Repository implementations (in `ci_persistence`)

## Related Modules

- **ci_persistence**: Concrete implementations of `JobRepository`
- **ci_server**: Uses models for API responses and repository for data access
- **ci_controller**: Uses models and repository for job orchestration
- **ci_client**: Uses model dictionaries for displaying job information