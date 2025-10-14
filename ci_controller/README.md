# ci_controller

Kubernetes-style controller for orchestrating Docker container lifecycle and job execution.

## Purpose

This module implements a **reconciliation loop pattern** (inspired by Kubernetes controllers) that continuously synchronizes desired state (jobs in database) with actual state (Docker containers). It provides:

- Automatic job execution without manual intervention
- Crash recovery and self-healing
- Orphaned resource cleanup
- Decoupled job submission from execution

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    JobController                        │
│                                                         │
│  ┌───────────────────────────────────────────────┐    │
│  │       Reconciliation Loop (every 2s)          │    │
│  │                                                │    │
│  │  1. Fetch desired state (DB jobs)             │    │
│  │  2. Fetch actual state (Docker containers)    │    │
│  │  3. Compare and reconcile differences         │    │
│  │  4. Take corrective actions                   │    │
│  │  5. Clean up orphaned resources               │    │
│  └───────────────────────────────────────────────┘    │
│                                                         │
│  Manages:                                               │
│  - Job state transitions (queued → running → completed)│
│  - Container lifecycle (create → start → monitor)      │
│  - Resource cleanup (temp dirs, containers)            │
│  - Error handling and failure recovery                 │
└─────────────────────────────────────────────────────────┘
           │                              │
           ▼                              ▼
    ┌─────────────┐              ┌──────────────────┐
    │  Repository │              │ ContainerManager │
    │  (Database) │              │     (Docker)     │
    └─────────────┘              └──────────────────┘
```

## Components

### `controller.py`

Main reconciliation loop implementation.

#### `JobController`

Continuously reconciles job state with container state, taking corrective actions when they diverge.

**Constructor:**
```python
JobController(
    repository: JobRepository,
    container_manager: ContainerManager | None = None,
    reconcile_interval: float = 2.0
)
```

**Lifecycle Methods:**
```python
async def start() -> None        # Start reconciliation loop
async def stop() -> None         # Stop and cleanup
async def reconcile_once() -> None  # Single reconciliation cycle (for testing)
```

**Job Management:**
```python
async def register_job(job_id: str, temp_dir: Path) -> None
```

### `container_manager.py`

Docker abstraction layer for container operations.

#### `ContainerManager`

Provides high-level Docker operations for CI job execution.

**Constructor:**
```python
ContainerManager(container_name_prefix: str = "")
```

The `container_name_prefix` enables namespace isolation for parallel test execution.

**Container Lifecycle:**
```python
async def create_container(job_id: str, zip_file_path: str) -> tuple[str, Path]
async def start_container(container_id: str) -> None
async def stop_container(container_id: str, timeout: int = 10) -> None
async def remove_container(container_id: str, force: bool = False) -> None
async def cleanup_container(job_id: str) -> None
```

**Monitoring:**
```python
async def get_container_info(job_id: str) -> ContainerInfo | None
async def list_ci_containers() -> list[ContainerInfo]
async def stream_logs(container_id: str, follow: bool = True) -> AsyncGenerator[str, None]
```

#### `ContainerInfo`

Represents container state from Docker's perspective:

```python
@dataclass
class ContainerInfo:
    container_id: str
    name: str                    # Job ID
    status: Literal["created", "running", "exited", "paused", "restarting", "removing", "dead"]
    exit_code: int | None
    started_at: datetime | None
    finished_at: datetime | None
```

## Reconciliation Logic

### State Machine

Each job progresses through states:

```
┌─────────┐
│ queued  │ ← Job created, waiting for execution
└────┬────┘
     │ Controller detects queued job
     │ Creates Docker container
     │ Starts container
     ▼
┌─────────┐
│ running │ ← Container executing tests
└────┬────┘
     │ Container exits
     │ Controller detects exit
     │ Reads exit code
     ▼
┌───────────┐
│ completed │ ← Job finished (success = exit code == 0)
└───────────┘
     OR
┌─────────┐
│ failed  │ ← Job encountered error (container lost, creation failed)
└─────────┘
```

### Reconciliation Scenarios

The controller handles these scenarios automatically:

#### 1. **Normal Execution**
```
DB: queued, no container_id
Docker: no container
Action: Create and start container, update DB to "running"
```

#### 2. **Container Running**
```
DB: running, container_id=abc123
Docker: container abc123 status=running
Action: Nothing (healthy state)
```

#### 3. **Container Finished**
```
DB: running, container_id=abc123
Docker: container abc123 status=exited, exit_code=0
Action: Mark job completed with success=true
```

#### 4. **Container Lost (Crash Recovery)**
```
DB: running, container_id=abc123
Docker: no container abc123
Action: Mark job as failed with reason "Container lost during execution"
```

#### 5. **Orphaned Container**
```
DB: no job with ID xyz
Docker: container xyz exists
Action: Remove container xyz (cleanup)
```

#### 6. **Stale Queued Job**
```
DB: queued, no container_id (from server restart)
Docker: no container
Action: Start the job by creating container
```

### Self-Healing Properties

The controller automatically recovers from:

- **Server crashes**: On restart, reconcile loop detects incomplete jobs and resumes or fails them
- **Docker daemon restarts**: Detects missing containers and fails corresponding jobs
- **Network issues**: Retries operations on next reconciliation cycle
- **Resource leaks**: Cleans up orphaned containers and temp directories

## Usage Examples

### Basic Setup

```python
from ci_controller.controller import JobController
from ci_controller.container_manager import ContainerManager
from ci_persistence.sqlite_repository import SQLiteJobRepository

# Initialize components
repository = SQLiteJobRepository("ci_jobs.db")
await repository.initialize()

container_manager = ContainerManager(container_name_prefix="ci_test_")
controller = JobController(
    repository=repository,
    container_manager=container_manager,
    reconcile_interval=2.0  # Reconcile every 2 seconds
)

# Start controller (runs in background)
await controller.start()

# Controller is now running and will:
# - Pick up new jobs from the database
# - Monitor running containers
# - Clean up completed jobs
# - Handle failures

# Later, stop controller
await controller.stop()
await repository.close()
```

### Integration with Server

```python
# ci_server/app.py
from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    global repository, job_controller

    # Startup
    repository = SQLiteJobRepository(get_database_path())
    await repository.initialize()

    container_manager = ContainerManager(container_name_prefix=get_container_prefix())
    job_controller = JobController(
        repository=repository,
        container_manager=container_manager,
        reconcile_interval=2.0
    )
    await job_controller.start()

    yield

    # Shutdown
    await job_controller.stop()
    await repository.close()

app = FastAPI(lifespan=lifespan)
```

### Submitting Jobs

Jobs are submitted by creating a database entry - the controller handles execution automatically:

```python
import tempfile
import uuid
from ci_common.models import Job

# 1. Stash the zip file
job_id = str(uuid.uuid4())
fd, zip_file_path = tempfile.mkstemp(suffix=".zip", prefix=f"ci_job_{job_id}_")
with os.fdopen(fd, "wb") as f:
    f.write(zip_data)

# 2. Create job in database
job = Job(
    id=job_id,
    status="queued",
    zip_file_path=zip_file_path
)
await repository.create_job(job)

# 3. Controller automatically picks up job and executes it!
# No need to manually create or start containers
```

### Monitoring Container Logs

```python
# Stream logs from a running container
container_id = "abc123"

async for log_line in container_manager.stream_logs(container_id, follow=True):
    print(log_line, end="")

# Get completed container logs
async for log_line in container_manager.stream_logs(container_id, follow=False):
    print(log_line, end="")
```

### Manual Reconciliation (Testing)

```python
# Trigger a single reconciliation cycle (useful for testing)
await controller.reconcile_once()

# Verify job state changed
job = await repository.get_job(job_id)
assert job.status == "running"
```

## Design Decisions

### 1. **Event-Driven Architecture**
Instead of callbacks or webhooks, the controller uses polling. This provides:
- Simplicity: No complex event handling
- Reliability: Missed events are detected on next cycle
- Debuggability: Clear execution flow

### 2. **Declarative API**
Jobs declare desired state ("I want to run tests"). The controller figures out how to achieve it.

### 3. **Idempotency**
All reconciliation actions are idempotent - running reconciliation multiple times produces the same result.

### 4. **Edge-Triggered vs Level-Triggered**
The controller is **level-triggered**: it acts based on current state, not state changes. This means:
- Missed events don't matter
- Recovery from crashes is automatic
- State consistency is guaranteed

### 5. **Separation of Concerns**
- **Controller**: Orchestration and business logic
- **ContainerManager**: Docker-specific operations
- **Repository**: Data persistence
- **Server**: HTTP API and user interaction

### 6. **Resource Lifecycle Management**
The controller tracks temporary directories and ensures cleanup:
- Temp dirs created during container creation
- Tracked in `active_jobs` map
- Cleaned up when job completes
- Force-cleaned on controller shutdown

## Performance Characteristics

**Reconciliation Interval:** 2 seconds by default
- Trade-off between responsiveness and CPU usage
- Faster intervals detect state changes quicker
- Slower intervals reduce Docker API calls

**Scalability:**
- Handles dozens of concurrent jobs easily
- Limited by Docker daemon capacity (~100 containers)
- Database queries are efficient (indexed lookups)

**Latency:**
- Job pickup: 0-2 seconds (depends on reconciliation timing)
- Failure detection: 0-2 seconds
- Orphan cleanup: 0-2 seconds

## Testing

The controller is tested in `tests/unit/test_job_controller.py` (~228 lines).

**Test Coverage:**
- Job state transitions
- Container lifecycle
- Crash recovery scenarios
- Orphaned resource cleanup
- Concurrent job execution
- Error handling

**Running Tests:**
```bash
pytest tests/unit/test_job_controller.py -v
```

**Mocking Strategy:**
Tests use mock repositories and container managers to avoid actual Docker operations.

## Error Handling

### Transient Errors
- Docker API failures are retried on next reconciliation
- Logged as warnings, not failures

### Permanent Failures
- Container creation failures mark job as "failed"
- Lost containers mark job as "failed"
- Error messages stored in logs

### Graceful Degradation
- Controller continues running even if individual jobs fail
- Failed reconciliation cycles are logged and retried
- Controller never crashes the server

## Configuration

### Environment Variables

- `CI_CONTAINER_PREFIX`: Namespace prefix for container isolation (default: "")
- `CI_DB_PATH`: Database path for job storage (default: "ci_jobs.db")

### Tuning Parameters

```python
controller = JobController(
    repository=repository,
    container_manager=container_manager,
    reconcile_interval=2.0  # Adjust based on workload
)
```

**Recommendations:**
- Development: 1.0 second (fast feedback)
- Production: 2.0 seconds (balanced)
- High load: 5.0 seconds (reduce Docker API load)

## Dependencies

- Python 3.8+
- `ci_common`: Domain models and repository interface
- `ci_persistence`: Database implementations
- Docker daemon running locally

## Related Modules

- **ci_server**: Uses controller for job execution
- **ci_common**: Provides models and interfaces
- **ci_persistence**: Provides job storage

## Known Limitations

1. **Single Node**: Controller runs on one server. For distributed systems, use leader election.
2. **No Job Prioritization**: Jobs are processed in order discovered (FIFO-ish).
3. **No Resource Limits**: Doesn't enforce limits on concurrent jobs or container resources.
4. **No Job Cancellation**: Once started, jobs run to completion (future enhancement).

## Future Enhancements

- [ ] Job prioritization (high/normal/low priority)
- [ ] Job cancellation API
- [ ] Resource quotas (max concurrent jobs, CPU limits)
- [ ] Job retry logic (auto-retry on transient failures)
- [ ] Metrics and observability (Prometheus exporter)
- [ ] Distributed controller with leader election (HA)
- [ ] Job scheduling (cron-style recurring jobs)
- [ ] Job dependencies (run job B after job A succeeds)
