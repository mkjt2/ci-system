# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Claude Code Instructions

- Rerun unit / end to end tests often after every significant code change.
- New features and functionality changes MUST be developed using TDD.
- Tests should not be flaky. Avoid techniques that can introduce flakiness if possible.
- Ensure code is well structured, modular, and avoid code duplication.
- Remember to use assertions to verify key invariants.
- Write clear, concise code.
- Code should be annotated with type hints diligently
- Code should be well commented, especially with intended functionality.
- Always update README.md to reflect functionality changes.
- Type checkers and lints should be run on all code (including tests)
- 

## Project Overview

A simple CI system for Python projects with API key authentication and user management. Users submit projects via CLI, which zips and sends them to a FastAPI server. A separate controller service executes jobs in Docker containers and streams results back in real-time.

## Architecture

The system is organized into separate packages following clean architecture principles:

**Client** (`ci_client/`):
- `cli.py` - CLI entry point, handles `ci submit test [--async]`, `ci wait <job_id>`, and `ci list` commands
- `client.py` - HTTP client with streaming support (SSE), async submission, job waiting, and Bearer token auth
- API key authentication support (via CLI flag, environment variable, or config file)

**Server** (`ci_server/`):
- `app.py` - FastAPI app with multiple endpoints (stateless, multi-replica capable):
  - `/submit` - Synchronous streaming endpoint (SSE)
  - `/submit-stream` - Same as `/submit` but also sends job_id event first for client display
  - `/submit-async` - Asynchronous submission, returns job ID immediately
  - `/jobs/{job_id}` - Get job status (queued/running/completed)
  - `/jobs/{job_id}/stream` - Stream job logs via SSE (supports reconnection)
  - `/jobs` - List all jobs for authenticated user
  - All endpoints require HTTPBearer authentication with API keys
- `auth.py` - API key generation, hashing (SHA-256), and validation
- `executor.py` - Legacy Docker execution module (mostly unused in controller pattern)
- Backward compatibility wrappers for models, repository, sqlite_repository (point to ci_common and ci_persistence)

**Controller** (`ci_controller/`):
- `controller.py` - Kubernetes-style reconciliation loop (singleton service)
  - Continuously syncs desired state (database) with actual state (Docker)
  - Handles job state transitions: queued → running → completed
  - Self-healing: recovers from crashes, cleans up orphaned resources
- `container_manager.py` - Docker abstraction layer for container lifecycle management
- Must run as singleton (exactly one instance)
- Initializes database schema on startup

**Common** (`ci_common/`):
- `models.py` - Domain models: Job, JobEvent, User, APIKey
- `repository.py` - Abstract repository interface (dependency inversion)
- Technology-agnostic, no database or framework dependencies

**Persistence** (`ci_persistence/`):
- `sqlite_repository.py` - Concrete SQLite implementation of JobRepository
- Async operations using aiosqlite
- Schema: users, api_keys, jobs, job_events tables with foreign key constraints
- User isolation enforced at database level

**Admin CLI** (`ci_admin/`):
- `cli.py` - User and API key management commands
- Commands: user create/list/get/activate/deactivate, key create/list/revoke

**Flow (Synchronous)**:
1. User authenticates with API key (via CLI flag, env var, or config file)
2. User runs `ci submit test` from project root
3. CLI zips project (excluding `.` and `__pycache__`)
4. Client POSTs zip to `/submit-stream` with Bearer token
5. Server validates API key and creates job in database (status: queued, user_id set)
6. Server streams job_id event to client for display
7. Controller picks up job in reconciliation loop (runs every 2s)
8. Controller extracts project, creates and starts Docker container
9. Controller updates job status to "running" in database
10. Server streams logs from Docker container to client in real-time
11. Container completes, controller marks job as completed with success status
12. CLI prints output in real-time, exits with pytest's exit code

**Flow (Asynchronous)**:
1. User authenticates with API key
2. User runs `ci submit test --async` from project root
3. CLI zips project and POSTs to `/submit-async` with Bearer token
4. Server validates API key, generates job ID, stores job metadata (status: queued, user_id set)
5. Server returns job ID immediately
6. CLI prints job ID and exits
7. Controller picks up and executes job in background
8. User later runs `ci wait <job_id>` (with same or different API key for same user)
9. Client GETs `/jobs/{job_id}/stream` which streams all events (past and future)
10. CLI prints output in real-time, exits with pytest's exit code

**Flow (List Jobs)**:
1. User authenticates with API key
2. User runs `ci list` or `ci list --json`
3. Client GETs `/jobs` with Bearer token
4. Server validates API key and returns only jobs belonging to that user (user isolation)
5. CLI displays jobs in table or JSON format

## Usage

**Prerequisites**:
1. Create a user account:
   ```bash
   ci-admin user create --name "Your Name" --email "you@example.com"
   ```

2. Generate an API key:
   ```bash
   ci-admin key create --email "you@example.com" --name "Dev Key"
   # Save the API key output (shown only once!)
   ```

3. Configure authentication (choose one):
   ```bash
   # Option 1: CLI flag
   ci submit test --api-key "ci_abc123..."

   # Option 2: Environment variable
   export CI_API_KEY="ci_abc123..."

   # Option 3: Config file
   echo "api_key=ci_abc123..." > ~/.ci/config
   ```

**Expected project structure**:
- `src/` - Source code
- `tests/` - Test files
- `requirements.txt` - Python dependencies (must include pytest)

**Submitting tests**:
```bash
# Synchronous mode (default) - streams results in real-time
ci submit test

# Asynchronous mode - returns job ID immediately
ci submit test --async
# Returns: Job submitted: <job_id>

# Wait for async job (forward-only, shows only new logs)
ci wait <job_id>

# Wait for async job (from beginning, shows all logs)
ci wait <job_id> --all

# List all your jobs
ci list

# List in JSON format
ci list --json
```

## Development

**Setup**:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
```

**Running the system**:

The system requires two services to run:

1. Start the controller (singleton, initializes DB schema):
   ```bash
   ci-controller
   # Or with custom settings:
   # ci-controller --db-path /path/to/db --interval 2.0
   ```

2. Start the server (can run multiple replicas):
   ```bash
   python -m uvicorn ci_server.app:app --port 8000
   ```

**Run tests**:
```bash
# All tests (runs with 1 worker by default, configured in pytest.ini)
pytest tests/ -v

# Sequential mode (no parallelization, useful for debugging)
pytest tests/ -v -n 0

# Auto-detect CPUs for parallel execution
pytest tests/ -v -n auto

# Unit tests only
pytest tests/unit/ -v

# End-to-end tests only
pytest tests/e2e/ -v
```

**Test fixtures**: Located in `tests/fixtures/` with dummy projects for passing tests, failing tests, and invalid Python code.

**Type checking and linting**:
```bash
# Type checking (must pass for all code including tests)
pyright

# Linting
ruff check .

# Auto-formatting
ruff format .
```