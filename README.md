# CI System

A simple CI system for Python projects that runs tests in isolated Docker containers with real-time streaming output.

## Features

- **Simple CLI**: Submit test jobs with a single command
- **Async Mode**: Submit jobs in background and check results later
- **Docker Isolation**: Tests run in clean Python containers
- **Real-time Streaming**: See test output as it happens via Server-Sent Events
- **Job Management**: Track and reconnect to running jobs by ID
- **Easy Setup**: Minimal configuration required

## Installation

```bash
pip install -e .
```

## Usage

From your Python project root (requires `src/`, `tests/`, and `requirements.txt`):

### Synchronous Mode (default)

Submit and wait for results immediately:

```bash
ci submit test
```

The CLI will:
1. Zip your project
2. Send it to the CI server
3. Stream test results in real-time
4. Exit with code 0 (pass) or 1 (fail)

### Asynchronous Mode

Submit a job and get a job ID immediately:

```bash
ci submit test --async
# Output: Job submitted: 1222e26a-e4d2-4dda-8ffa-ba333257cc1b
```

Later, wait for the job to complete and stream logs:

```bash
# Show only new logs from current position (default)
ci wait 1222e26a-e4d2-4dda-8ffa-ba333257cc1b

# Show all logs from beginning
ci wait 1222e26a-e4d2-4dda-8ffa-ba333257cc1b --all
```

This is useful for:
- Long-running test suites where you don't want to keep terminal open
- Running multiple test jobs in parallel
- Resuming log streaming if connection is interrupted

### List Jobs

View all jobs with their status:

```bash
# Human-readable table format
ci list

# Example output:
# JOB ID                                 STATUS       START TIME             END TIME               SUCCESS
# --------------------------------------------------------------------------------------------------------------
# 1222e26a-e4d2-4dda-8ffa-ba333257cc1b   completed    2025-10-12 19:30:00    2025-10-12 19:30:05    ✓
# 7ad3f8c9-2b41-4e89-9c12-5a8e7b3d1f4e   running      2025-10-12 19:31:10    N/A                    -

# JSON format (useful for scripting)
ci list --json
```

## Running the Server

Start the CI server:

```bash
python -m uvicorn ci_server.app:app --port 8000
```

**Custom Database Path:**
```bash
CI_DB_PATH=/path/to/custom.db python -m uvicorn ci_server.app:app --port 8000
```

Jobs are persisted to the SQLite database and survive server restarts.

## Development

**Setup:**
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
```

**Run Tests:**
```bash
# Run all tests with parallel execution (default, auto-detects CPU cores)
pytest tests/ -v

# Run tests sequentially (useful for debugging)
pytest tests/ -v -n 0

# Run only unit tests
pytest tests/unit/ -v

# Run only end-to-end tests
pytest tests/e2e/ -v
```

**Test Parallelization:**

Tests automatically run in parallel using pytest-xdist with `-n auto`, which creates one worker per CPU core:
- **Local (12 cores)**: 12 parallel workers
- **GitHub Actions (4 cores)**: 4 parallel workers
- **Debug mode**: Use `-n 0` to disable parallelization

Each worker runs tests independently with isolated server ports (8001, 8002, etc.) and separate SQLite databases to prevent conflicts.

## Architecture

- **Client** (`ci_client/`): CLI tool that zips projects and submits to server
  - `cli.py`: CLI commands (`submit`, `wait`, `list`)
  - `client.py`: HTTP client with sync/async submission and SSE streaming
- **Server** (`ci_server/`): FastAPI app that runs pytest in Docker containers
  - `app.py`: REST API endpoints with persistent job storage
  - `executor.py`: Docker execution with streaming output
  - `repository.py`: Abstract interface for job persistence
  - `sqlite_repository.py`: SQLite implementation (default)
  - `models.py`: Data models for jobs and events
- **Communication**: Server-Sent Events for real-time output streaming
- **Job Storage**: SQLite database (persistent across server restarts)
  - Default location: `ci_jobs.db`
  - Configurable via `CI_DB_PATH` environment variable
  - Ready for PostgreSQL/MySQL migration if needed

## Requirements

- Python 3.8+
- Docker (for running tests in containers)

## License

MIT
