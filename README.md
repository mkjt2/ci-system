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
ci wait 1222e26a-e4d2-4dda-8ffa-ba333257cc1b
```

This is useful for:
- Long-running test suites where you don't want to keep terminal open
- Running multiple test jobs in parallel
- Resuming log streaming if connection is interrupted

## Running the Server

Start the CI server:

```bash
python -m uvicorn ci_server.app:app --port 8000
```

## Development

**Setup:**
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
```

**Run Tests:**
```bash
pytest tests/e2e/ -v
```

## Architecture

- **Client** (`ci_client/`): CLI tool that zips projects and submits to server
  - `cli.py`: CLI commands (`submit`, `wait`)
  - `client.py`: HTTP client with sync/async submission and SSE streaming
- **Server** (`ci_server/`): FastAPI app that runs pytest in Docker containers
  - `app.py`: REST API endpoints with in-memory job store
  - `executor.py`: Docker execution with streaming output
- **Communication**: Server-Sent Events for real-time output streaming
- **Job Storage**: In-memory (jobs do not survive server restarts)

## Requirements

- Python 3.8+
- Docker (for running tests in containers)

## License

MIT
