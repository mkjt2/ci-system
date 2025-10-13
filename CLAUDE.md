# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Claude Code Instructions

- Rerun unit / end to end tests often after every significant code change.
- New features and functionality changes MUST be developed using TDD.
- Tests should not be flaky. Avoid techniques that can introduce flakiness if possible.
- Ensure code is well structured, modular, and adheres to best practices.
- Remember to use assertions to verify key invariants.
- Write clear, concise code.
- Code should be annotated with type hints diligently.
- Code should be well commented, especially with intended functionality.
- Always update README.md to reflect functionality changes.
- Type checkers and lints should be run on all code (including tests)
- 

## Project Overview

A simple CI system for Python projects. Users submit projects via CLI, which zips and sends them to a FastAPI server. The server runs pytest in a Docker container and streams results back in real-time.

## Architecture

**Client** (`ci_client/`):
- `cli.py` - CLI entry point, handles `ci submit test [--async]` and `ci wait <job_id>` commands
- `client.py` - HTTP client with streaming support (SSE), async submission, and job waiting

**Server** (`ci_server/`):
- `app.py` - FastAPI app with multiple endpoints:
  - `/submit` - Synchronous streaming endpoint (SSE). Creates a job, processes in background, streams results.
  - `/submit-stream` - Same as `/submit` but also sends job_id event first for client display
  - `/submit-async` - Asynchronous submission, returns job ID immediately
  - `/jobs/{job_id}` - Get job status (queued/running/completed)
  - `/jobs/{job_id}/stream` - Stream job logs via SSE (supports reconnection)
  - All streaming endpoints now use unified `stream_job_events()` helper to reduce code duplication
- `executor.py` - Executes pytest in Docker container, supports streaming output
- `repository.py` - Abstract interface for job persistence (supports multiple backends)
- `sqlite_repository.py` - SQLite implementation of repository (default)
- `models.py` - Data models for Job and JobEvent
- Persistent job store - SQLite database tracks job state and events (survives restarts)

**Flow (Synchronous)**:
1. User runs `ci submit test` from project root
2. CLI zips project (excluding `.` and `__pycache__`)
3. Client POSTs zip to `/submit`
4. Server creates job in database (status: queued)
5. Server starts async job processing in background (via `process_job_async`)
6. Server streams job events back to client as they become available
7. Background worker extracts project, mounts read-only into Docker, runs pytest
8. Worker stores all events in database as they occur
9. CLI prints output in real-time, exits with pytest's exit code

**Flow (Asynchronous)**:
1. User runs `ci submit test --async` from project root
2. CLI zips project and POSTs to `/submit-async`
3. Server generates job ID, stores job metadata, starts processing in background
4. Server returns job ID immediately
5. CLI prints job ID and exits
6. User later runs `ci wait <job_id>`
7. Client GETs `/jobs/{job_id}/stream` which streams all events (past and future)
8. CLI prints output in real-time, exits with pytest's exit code

## Usage

**Expected project structure**:
- `src/` - Source code
- `tests/` - Test files
- `requirements.txt` - Python dependencies (must include pytest)

**Running tests**:
```bash
# Synchronous mode (default)
ci submit test

# Asynchronous mode
ci submit test --async
# Returns: Job submitted: <job_id>

# Wait for async job
ci wait <job_id>
```

## Development

**Setup**:
```bash
source .venv/bin/activate
pip install -e .
```

**Run E2E tests**:
```bash
pytest tests/e2e/ -v
```

**Start server manually**:
```bash
python -m uvicorn ci_server.app:app --port 8000
```

**Test fixtures**: Located in `tests/fixtures/` with dummy projects for passing tests, failing tests, and invalid Python code.