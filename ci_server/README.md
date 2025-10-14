# ci_server

FastAPI-based REST API server for job submission, status tracking, and real-time log streaming.

## Purpose

This module provides the HTTP interface for the CI system, enabling clients to:
- Submit test jobs (synchronous and asynchronous modes)
- Stream real-time logs via Server-Sent Events (SSE)
- Query job status and history
- List all jobs with filtering

The server delegates execution to the `JobController` and persistence to the `JobRepository`, acting as a thin API layer.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        ci_server                            │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              FastAPI Application                    │   │
│  │                                                     │   │
│  │  POST   /submit          - Sync submit & stream    │   │
│  │  POST   /submit-stream   - Sync with job ID        │   │
│  │  POST   /submit-async    - Async submit            │   │
│  │  GET    /jobs/{id}/stream - Stream job logs        │   │
│  │  GET    /jobs/{id}       - Get job status          │   │
│  │  GET    /jobs            - List all jobs           │   │
│  └─────────────────────────────────────────────────────┘   │
│                     │                    │                  │
│                     ▼                    ▼                  │
│           ┌──────────────────┐  ┌─────────────────┐        │
│           │  JobController   │  │  JobRepository  │        │
│           │  (Execution)     │  │  (Persistence)  │        │
│           └──────────────────┘  └─────────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

## Components

### `app.py`

Main FastAPI application with endpoint definitions.

#### Lifecycle Management

**Startup:**
1. Initialize database repository
2. Create job controller with container manager
3. Start reconciliation loop
4. Perform initial reconciliation (crash recovery)

**Shutdown:**
1. Stop job controller gracefully
2. Close database connections
3. Clean up active resources

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    repository = SQLiteJobRepository(get_database_path())
    await repository.initialize()

    container_manager = ContainerManager(container_name_prefix=get_container_prefix())
    job_controller = JobController(repository, container_manager, reconcile_interval=2.0)
    await job_controller.start()

    yield

    # Shutdown
    await job_controller.stop()
    await repository.close()
```

#### API Endpoints

### POST `/submit`

Submit job and stream results in real-time (synchronous mode).

**Request:**
- Content-Type: `multipart/form-data`
- Body: `file` - Project zip file

**Response:**
- Content-Type: `text/event-stream`
- Format: Server-Sent Events (SSE)

**SSE Events:**
```json
data: {"type": "log", "data": "Installing dependencies...\n"}

data: {"type": "log", "data": "Running tests...\n"}

data: {"type": "complete", "success": true}
```

**Example:**
```bash
curl -N -X POST http://localhost:8000/submit \
  -F "file=@project.zip"
```

### POST `/submit-stream`

Same as `/submit` but sends job ID first for client display.

**SSE Events:**
```json
data: {"type": "job_id", "job_id": "550e8400-e29b-41d4-a716-446655440000"}

data: {"type": "log", "data": "Installing dependencies...\n"}

data: {"type": "complete", "success": true}
```

This enables clients to display the job ID so users can reconnect from another terminal.

### POST `/submit-async`

Submit job asynchronously and return job ID immediately.

**Request:**
- Content-Type: `multipart/form-data`
- Body: `file` - Project zip file

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Example:**
```bash
curl -X POST http://localhost:8000/submit-async \
  -F "file=@project.zip"
# {"job_id":"550e8400-e29b-41d4-a716-446655440000"}
```

### GET `/jobs/{job_id}/stream`

Stream logs for a specific job via SSE.

**Query Parameters:**
- `from_beginning` (bool): If true, stream all logs. If false (default), only stream new logs.

**Response:**
- Content-Type: `text/event-stream`
- Format: Server-Sent Events (SSE)

**Behavior:**
- **Queued jobs**: Waits up to 30s for job to start, then streams logs
- **Running jobs**: Streams logs in real-time from Docker container
- **Completed jobs**:
  - `from_beginning=false`: Immediately sends completion event
  - `from_beginning=true`: Replays all logs from container, then sends completion

**Example:**
```bash
# Only show new logs (forward-only mode)
curl -N http://localhost:8000/jobs/550e8400-e29b-41d4-a716-446655440000/stream

# Show all logs from beginning
curl -N http://localhost:8000/jobs/550e8400-e29b-41d4-a716-446655440000/stream?from_beginning=true
```

### GET `/jobs/{job_id}`

Get job status and metadata (non-streaming).

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "success": true,
  "start_time": "2025-10-13T10:30:00Z",
  "end_time": "2025-10-13T10:30:15Z"
}
```

**Example:**
```bash
curl http://localhost:8000/jobs/550e8400-e29b-41d4-a716-446655440000
```

### GET `/jobs`

List all jobs with metadata.

**Response:**
```json
[
  {
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "completed",
    "success": true,
    "start_time": "2025-10-13T10:30:00Z",
    "end_time": "2025-10-13T10:30:15Z"
  },
  {
    "job_id": "7ad3f8c9-2b41-4e89-9c12-5a8e7b3d1f4e",
    "status": "running",
    "success": null,
    "start_time": "2025-10-13T10:35:00Z",
    "end_time": null
  }
]
```

**Example:**
```bash
curl http://localhost:8000/jobs
```

### `executor.py`

Legacy Docker execution module (mostly superseded by controller).

#### `run_tests_in_docker_streaming()`

Executes pytest in a Docker container and streams output.

**Note:** This function is currently unused in the controller pattern but kept for potential direct execution scenarios.

```python
async def run_tests_in_docker_streaming(zip_data: bytes) -> AsyncGenerator[dict, None]:
    """
    Execute tests in Docker container and stream events.

    Yields:
        dict: Events with keys 'type', 'data', 'success'
    """
```

## Usage Examples

### Starting the Server

**Development:**
```bash
python -m uvicorn ci_server.app:app --reload --port 8000
```

**Production:**
```bash
uvicorn ci_server.app:app --host 0.0.0.0 --port 8000 --workers 4
```

**Custom Database:**
```bash
CI_DB_PATH=/data/ci_jobs.db uvicorn ci_server.app:app --port 8000
```

**Parallel Testing (Namespace Isolation):**
```bash
# Terminal 1
CI_DB_PATH=test1.db CI_CONTAINER_PREFIX=test1_ uvicorn ci_server.app:app --port 8001

# Terminal 2
CI_DB_PATH=test2.db CI_CONTAINER_PREFIX=test2_ uvicorn ci_server.app:app --port 8002
```

### Submitting Jobs via curl

**Synchronous submission:**
```bash
cd /path/to/your/project
zip -r project.zip . -x '.*' -x '__pycache__/*'
curl -N -X POST http://localhost:8000/submit -F "file=@project.zip"
```

**Asynchronous submission:**
```bash
zip -r project.zip . -x '.*' -x '__pycache__/*'
JOB_ID=$(curl -X POST http://localhost:8000/submit-async -F "file=@project.zip" | jq -r .job_id)
echo "Job ID: $JOB_ID"

# Wait for completion
curl -N http://localhost:8000/jobs/$JOB_ID/stream?from_beginning=true
```

### Integration with CI Client

```python
from ci_client.client import submit_tests_streaming

for event in submit_tests_streaming(Path.cwd(), server_url="http://localhost:8000"):
    if event["type"] == "log":
        print(event["data"], end="")
    elif event["type"] == "complete":
        success = event["success"]
        sys.exit(0 if success else 1)
```

## Design Decisions

### 1. **Server-Sent Events (SSE)**
Uses SSE instead of WebSockets for real-time streaming because:
- Simpler protocol (just HTTP)
- Automatic reconnection in browsers
- Works through HTTP proxies
- Unidirectional (server → client) is sufficient

### 2. **Controller-Based Execution**
The server doesn't execute jobs directly. Instead:
- Server creates "queued" job in database
- Controller picks up job and executes it
- Server streams logs from Docker via controller

**Benefits:**
- Server remains stateless
- Crash recovery is automatic
- Clear separation of concerns

### 3. **Zip File Stashing**
When jobs are submitted:
1. Zip file is written to temp file
2. Path is stored in database (`zip_file_path`)
3. Controller reads from path when starting job

**Why not store in database?**
- Avoids large BLOBs in SQLite
- Better performance
- Easier cleanup

### 4. **Direct Docker Log Streaming**
Logs are streamed directly from Docker, not stored in database:
- Reduces database writes
- Lower latency
- Unlimited log size
- Containers act as log storage

**Trade-off:** Logs disappear when container is removed.

### 5. **Two Streaming Modes**
- `from_beginning=true`: Replay all logs (useful for reconnection)
- `from_beginning=false`: Forward-only (useful for monitoring from another terminal)

### 6. **Graceful Shutdown**
Lifespan context manager ensures:
- Controller stops reconciliation loop
- Active jobs continue running
- Database connections close cleanly
- No resource leaks

## Performance Characteristics

**Throughput:**
- Concurrent submissions: Limited by Docker capacity (~100 containers)
- Database operations: Fast (indexed queries)
- SSE connections: Thousands per server (async I/O)

**Latency:**
- Job submission: < 100ms (just database write)
- Job pickup: 0-2 seconds (reconciliation interval)
- Log streaming: Real-time (Docker logs --follow)

**Resource Usage:**
- Memory: Minimal per SSE connection (~10KB)
- CPU: Mostly idle (I/O bound)
- Disk: Grows with job history and temp files

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CI_DB_PATH` | SQLite database path | `ci_jobs.db` |
| `CI_CONTAINER_PREFIX` | Container namespace prefix | `""` |

### Tuning Parameters

**Reconciliation interval:**
```python
job_controller = JobController(
    repository=repository,
    container_manager=container_manager,
    reconcile_interval=2.0  # Seconds between reconciliation cycles
)
```

**Server workers (production):**
```bash
# Multiple workers for high throughput
uvicorn ci_server.app:app --workers 4

# Single worker for development
uvicorn ci_server.app:app --reload
```

## Testing

### Unit Tests
Component-specific tests in `tests/unit/`

### End-to-End Tests
Full workflow tests in `tests/e2e/test_ci_submit.py`

**Running E2E Tests:**
```bash
pytest tests/e2e/ -v
```

**E2E Test Features:**
- Automatic server startup/shutdown
- Isolated test databases per worker
- Namespace isolation for parallel execution
- Health checking for server readiness
- Automatic container cleanup

## Error Handling

### HTTP Errors

| Status Code | Scenario |
|-------------|----------|
| 404 | Job ID not found |
| 500 | Internal server error (logged) |

### Streaming Errors

**Client disconnection:**
- Detected via `await request.is_disconnected()`
- Stream terminated gracefully
- Job continues running on server

**Container errors:**
- Streamed as log events
- Completion event marks success=false

## Security Considerations

1. **File Upload Size**: No limit currently - add middleware for production
2. **Rate Limiting**: Not implemented - add for production
3. **Authentication**: None - add JWT/OAuth for production
4. **Input Validation**: Zip files are extracted without scanning - add virus scanning
5. **Resource Limits**: No container resource limits - add CPU/memory caps

## Dependencies

- `fastapi>=0.104.0`: Web framework
- `uvicorn>=0.24.0`: ASGI server
- `python-multipart>=0.0.6`: Multipart form handling
- `ci_common`: Domain models and interfaces
- `ci_persistence`: Database implementations
- `ci_controller`: Job execution

## Related Modules

- **ci_client**: Consumes this API
- **ci_controller**: Executes jobs
- **ci_common**: Shared models
- **ci_persistence**: Data storage

## Known Limitations

1. **No Job Cancellation**: Once submitted, jobs run to completion
2. **No Resource Limits**: Containers can consume unlimited CPU/memory
3. **No Authentication**: Anyone can submit jobs
4. **Single Region**: All operations are local
5. **No Job Prioritization**: FIFO processing order

## Future Enhancements

- [ ] Job cancellation endpoint (DELETE /jobs/{job_id})
- [ ] Authentication and authorization (JWT tokens)
- [ ] Rate limiting per user/IP
- [ ] Container resource limits (CPU, memory)
- [ ] Job prioritization (priority queue)
- [ ] Webhook notifications (on job completion)
- [ ] GraphQL API for complex queries
- [ ] Metrics endpoint for Prometheus
- [ ] Health check endpoint
- [ ] Pagination for job listings
- [ ] Filtering and search for jobs
- [ ] Job artifacts storage (test reports, coverage)
