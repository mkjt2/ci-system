# ci_client

Command-line interface and Python client library for interacting with the CI system.

## Purpose

This module provides two interfaces for the CI system:

1. **CLI Tool** (`cli.py`): User-facing command-line interface for submitting jobs and viewing results
2. **Client Library** (`client.py`): Python API for programmatic interaction

Both interfaces handle project packaging, HTTP communication, and Server-Sent Events (SSE) parsing.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      ci_client                          │
│                                                         │
│  ┌────────────────────────────────────────────────┐    │
│  │              CLI (cli.py)                      │    │
│  │                                                │    │
│  │  Commands:                                     │    │
│  │  - ci submit test [--async]                    │    │
│  │  - ci wait <job_id> [--all]                    │    │
│  │  - ci list [--json]                            │    │
│  └───────────────────┬────────────────────────────┘    │
│                      │                                  │
│                      ▼                                  │
│  ┌────────────────────────────────────────────────┐    │
│  │         Client Library (client.py)             │    │
│  │                                                │    │
│  │  Functions:                                    │    │
│  │  - submit_tests_streaming()                    │    │
│  │  - submit_tests_async()                        │    │
│  │  - wait_for_job()                              │    │
│  │  - list_jobs()                                 │    │
│  │  - create_project_zip()                        │    │
│  └────────────────────────────────────────────────┘    │
└─────────────────────┬───────────────────────────────────┘
                      │
                      │ HTTP/SSE
                      ▼
              ┌───────────────┐
              │   ci_server   │
              │   (FastAPI)   │
              └───────────────┘
```

## CLI Commands

### `ci submit test`

Submit tests and stream results in real-time (synchronous mode).

**Usage:**
```bash
ci submit test
```

**Behavior:**
1. Zips current directory (excludes `.` and `__pycache__`)
2. POSTs to server `/submit-stream` endpoint
3. Displays job ID to stderr (for reconnection)
4. Streams logs to stdout in real-time
5. Exits with code 0 (pass) or 1 (fail)

**Example:**
```bash
cd /path/to/python/project
ci submit test
# Job ID: 550e8400-e29b-41d4-a716-446655440000
# You can reconnect from another terminal with: ci wait 550e8400-e29b-41d4-a716-446655440000
#
# Installing dependencies...
# Running tests...
# test_calculator.py::test_add PASSED
# test_calculator.py::test_subtract PASSED
# All tests passed!
```

**Cancellation:**
Press Ctrl+C to cancel (exit code 130). Job continues running on server.

### `ci submit test --async`

Submit tests asynchronously and return job ID immediately.

**Usage:**
```bash
ci submit test --async
```

**Behavior:**
1. Zips current directory
2. POSTs to server `/submit-async` endpoint
3. Prints job ID to stdout
4. Exits immediately with code 0

**Example:**
```bash
cd /path/to/python/project
ci submit test --async
# Job submitted: 550e8400-e29b-41d4-a716-446655440000
```

**Use Cases:**
- Long-running test suites where you don't want to keep terminal open
- Running multiple test jobs in parallel
- CI/CD pipelines that submit jobs and check results later

### `ci wait <job_id>`

Wait for job completion and stream logs (forward-only by default).

**Usage:**
```bash
ci wait <job_id> [--all]
```

**Options:**
- `--all`: Show all logs from beginning (default: only show new logs)

**Behavior:**
1. Connects to server `/jobs/{job_id}/stream` endpoint
2. Streams logs to stdout
3. Exits with code 0 (pass) or 1 (fail)

**Examples:**
```bash
# Only show new logs (forward-only mode)
ci wait 550e8400-e29b-41d4-a716-446655440000

# Show all logs from beginning
ci wait 550e8400-e29b-41d4-a716-446655440000 --all
```

**Use Cases:**
- Reconnecting to a job after disconnection
- Monitoring a job from a different terminal
- Checking results of an async job

**Cancellation:**
Press Ctrl+C to stop waiting (exit code 130). Job continues running on server.

### `ci list`

List all jobs with their status.

**Usage:**
```bash
ci list [--json]
```

**Options:**
- `--json`: Output in JSON format (default: human-readable table)

**Table Format:**
```bash
ci list
# JOB ID                                 STATUS       START TIME             END TIME               SUCCESS
# --------------------------------------------------------------------------------------------------------------
# 550e8400-e29b-41d4-a716-446655440000   completed    2025-10-13 10:30:00    2025-10-13 10:30:15    ✓
# 7ad3f8c9-2b41-4e89-9c12-5a8e7b3d1f4e   running      2025-10-13 10:35:00    N/A                    -
# 9f8c1e2a-4b7d-4c89-a231-8e5f6d3c2b1a   failed       2025-10-13 10:25:00    2025-10-13 10:25:08    ✗
```

**JSON Format:**
```bash
ci list --json
# [
#   {
#     "job_id": "550e8400-e29b-41d4-a716-446655440000",
#     "status": "completed",
#     "success": true,
#     "start_time": "2025-10-13T10:30:00Z",
#     "end_time": "2025-10-13T10:30:15Z"
#   },
#   {
#     "job_id": "7ad3f8c9-2b41-4e89-9c12-5a8e7b3d1f4e",
#     "status": "running",
#     "success": null,
#     "start_time": "2025-10-13T10:35:00Z",
#     "end_time": null
#   }
# ]
```

## Client Library API

### `create_project_zip(project_dir: Path) -> bytes`

Create a zip archive of a project directory.

**Exclusions:**
- Hidden files and directories (starting with `.`)
- `__pycache__` directories
- Directories themselves (only files)

**Example:**
```python
from pathlib import Path
from ci_client.client import create_project_zip

zip_data = create_project_zip(Path("/path/to/project"))
print(f"Zip size: {len(zip_data)} bytes")
```

### `submit_tests_streaming(project_dir: Path, server_url: str) -> Generator[dict, None, None]`

Submit tests and stream events via Server-Sent Events.

**Parameters:**
- `project_dir`: Path to project root
- `server_url`: CI server URL (default: `http://localhost:8000`)

**Yields:**
- `{"type": "job_id", "job_id": str}` - Job identifier
- `{"type": "log", "data": str}` - Log output
- `{"type": "complete", "success": bool}` - Completion status

**Example:**
```python
from pathlib import Path
from ci_client.client import submit_tests_streaming

for event in submit_tests_streaming(Path.cwd()):
    if event["type"] == "job_id":
        print(f"Job ID: {event['job_id']}")
    elif event["type"] == "log":
        print(event["data"], end="")
    elif event["type"] == "complete":
        if event["success"]:
            print("Tests passed!")
        else:
            print("Tests failed!")
```

### `submit_tests_async(project_dir: Path, server_url: str) -> str`

Submit tests asynchronously and get job ID immediately.

**Parameters:**
- `project_dir`: Path to project root
- `server_url`: CI server URL (default: `http://localhost:8000`)

**Returns:**
- `str`: Job ID (UUID)

**Raises:**
- `RuntimeError`: If submission fails

**Example:**
```python
from pathlib import Path
from ci_client.client import submit_tests_async, wait_for_job

# Submit job
job_id = submit_tests_async(Path.cwd())
print(f"Job submitted: {job_id}")

# Do other work...

# Wait for completion
for event in wait_for_job(job_id, from_beginning=True):
    if event["type"] == "log":
        print(event["data"], end="")
    elif event["type"] == "complete":
        success = event["success"]
```

### `wait_for_job(job_id: str, server_url: str, from_beginning: bool) -> Generator[dict, None, None]`

Stream logs for a specific job via SSE.

**Parameters:**
- `job_id`: Job UUID
- `server_url`: CI server URL (default: `http://localhost:8000`)
- `from_beginning`: If True, stream all logs. If False (default), only new logs.

**Yields:**
- `{"type": "log", "data": str}` - Log output
- `{"type": "complete", "success": bool}` - Completion status

**Example:**
```python
from ci_client.client import wait_for_job

# Only show new logs (forward-only)
for event in wait_for_job("550e8400-e29b-41d4-a716-446655440000"):
    if event["type"] == "log":
        print(event["data"], end="")

# Show all logs from beginning
for event in wait_for_job("550e8400-e29b-41d4-a716-446655440000", from_beginning=True):
    if event["type"] == "log":
        print(event["data"], end="")
```

### `list_jobs(server_url: str) -> list[dict]`

Fetch all jobs from the server.

**Parameters:**
- `server_url`: CI server URL (default: `http://localhost:8000`)

**Returns:**
- List of job dictionaries with keys: `job_id`, `status`, `success`, `start_time`, `end_time`

**Raises:**
- `RuntimeError`: If request fails

**Example:**
```python
from ci_client.client import list_jobs

jobs = list_jobs()
for job in jobs:
    print(f"{job['job_id']}: {job['status']} (success={job['success']})")
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CI_SERVER_URL` | CI server base URL | `http://localhost:8000` |

**Example:**
```bash
export CI_SERVER_URL=http://ci-server.example.com:8000
ci submit test
```

## Installation

The client is installed as part of the ci-system package:

```bash
pip install -e .
```

This creates the `ci` command-line tool.

## Usage Patterns

### Pattern 1: Quick Testing (Synchronous)

For immediate feedback during development:

```bash
cd /path/to/project
ci submit test
```

### Pattern 2: Background Testing (Asynchronous)

For long-running tests or when you want to do other work:

```bash
# Submit job
JOB_ID=$(ci submit test --async | grep -oE '[a-f0-9-]{36}')

# Do other work...

# Check results later
ci wait $JOB_ID --all
```

### Pattern 3: Monitoring from Multiple Terminals

Terminal 1 (submit):
```bash
ci submit test
# Job ID: 550e8400-e29b-41d4-a716-446655440000
```

Terminal 2 (monitor):
```bash
ci wait 550e8400-e29b-41d4-a716-446655440000
# Streams new logs as they appear
```

### Pattern 4: CI/CD Integration

```bash
#!/bin/bash
# submit_tests.sh

# Submit job
JOB_ID=$(ci submit test --async | grep -oE '[a-f0-9-]{36}')
echo "Submitted job: $JOB_ID"

# Save job ID for later retrieval
echo $JOB_ID > job_id.txt

# Wait for completion
ci wait $JOB_ID --all

# Exit with test result code
EXIT_CODE=$?
exit $EXIT_CODE
```

### Pattern 5: Batch Job Submission

```bash
#!/bin/bash
# Run tests on multiple projects in parallel

for project in project1 project2 project3; do
  cd $project
  JOB_ID=$(ci submit test --async | grep -oE '[a-f0-9-]{36}')
  echo "$project: $JOB_ID"
  cd ..
done

# Wait for all to complete
ci list
```

## Error Handling

### Network Errors

If the server is unreachable:
```
Error: Error submitting to CI server: HTTPConnectionPool(host='localhost', port=8000)...
```

**Solution:** Ensure server is running and `CI_SERVER_URL` is correct.

### Keyboard Interrupt (Ctrl+C)

Gracefully handled with exit code 130:
```
^C
Job cancelled by user.
```

The job continues running on the server. Use `ci wait <job_id>` to reconnect.

### Invalid Job ID

```
Error: Error waiting for job: 404 Client Error: Not Found for url...
```

**Solution:** Check job ID is correct using `ci list`.

## Testing

The client is tested as part of E2E tests in `tests/e2e/test_ci_submit.py`.

**Running Tests:**
```bash
pytest tests/e2e/ -v
```

## Dependencies

- `requests>=2.31.0`: HTTP client with SSE support
- Python 3.8+: Core language features

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Tests passed or command succeeded |
| 1 | Tests failed or command failed |
| 130 | User cancelled with Ctrl+C |

## Performance Characteristics

**Network Efficiency:**
- Streaming uses chunked transfer encoding
- No buffering of large responses
- Low memory footprint

**Responsiveness:**
- Real-time log streaming (< 100ms latency)
- Immediate job ID return in async mode
- Fast project zipping (< 1s for typical projects)

## Related Modules

- **ci_server**: Server that this client communicates with
- **ci_common**: Shared data models (job format)

## Known Limitations

1. **No Progress Indicator**: Long zipping operations show no progress
2. **No Retry Logic**: Failed requests don't retry automatically
3. **No Connection Pooling**: Each command creates new connection
4. **No Job Filtering**: `ci list` shows all jobs (no search/filter)
5. **No Cancellation**: Can't cancel jobs from client

## Future Enhancements

- [ ] Progress indicator for zip creation
- [ ] Automatic retry with exponential backoff
- [ ] Connection pooling for efficiency
- [ ] Job filtering and search (`ci list --status=running`)
- [ ] Job cancellation (`ci cancel <job_id>`)
- [ ] Colorized output for better readability
- [ ] Watch mode (`ci watch <job_id>`) with auto-reconnect
- [ ] Configuration file (`~/.ci/config.yaml`)
- [ ] Shell completion (bash, zsh, fish)
- [ ] TUI (Terminal User Interface) for interactive job management