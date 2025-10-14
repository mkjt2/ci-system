# CI System

A simple CI system for Python projects that runs tests in isolated Docker containers with real-time streaming output.

## Features

- **API Key Authentication**: Secure access with user-based API keys
- **User Management**: Admin CLI for managing users and API keys
- **Simple CLI**: Submit test jobs with a single command
- **Async Mode**: Submit jobs in background and check results later
- **Docker Isolation**: Tests run in clean Python containers
- **Real-time Streaming**: See test output as it happens via Server-Sent Events
- **Job Management**: Track and reconnect to running jobs by ID
- **User Isolation**: Users can only see and access their own jobs
- **Easy Setup**: Minimal configuration required

## Installation

```bash
pip install -e .
```

## Authentication Setup

The CI system uses API key authentication to secure access and isolate jobs between users.

### 1. Create a User

Use the admin CLI to create a user account:

```bash
ci-admin user create --name "Your Name" --email "you@example.com"
# Output: ✓ User created successfully
#         ID:    a1b2c3d4-e5f6-7890-abcd-ef1234567890
#         Name:  Your Name
#         Email: you@example.com
```

### 2. Create an API Key

Generate an API key for the user:

```bash
ci-admin key create --email "you@example.com" --name "My Dev Key"
# Output: ✓ API key created successfully
#
#         API Key: ci_abc123def456ghi789...
#         Name:    My Dev Key
#         User:    you@example.com
#
#         ⚠️  IMPORTANT: This is the only time you'll see this key!
#            Save it securely now.
```

### 3. Configure the Client

You can provide your API key in three ways (in priority order):

#### Option 1: Command Line Flag (highest priority)
```bash
ci submit test --api-key "ci_abc123def456ghi789..."
```

#### Option 2: Environment Variable
```bash
export CI_API_KEY="ci_abc123def456ghi789..."
ci submit test
```

#### Option 3: Config File
```bash
mkdir -p ~/.ci
echo "api_key=ci_abc123def456ghi789..." > ~/.ci/config
ci submit test
```

### User Management Commands

```bash
# List all users
ci-admin user list

# Get user details
ci-admin user get --email "you@example.com"

# Deactivate a user
ci-admin user deactivate <user-id>

# Reactivate a user
ci-admin user activate <user-id>
```

### API Key Management Commands

```bash
# List all API keys for a user
ci-admin key list --email "you@example.com"

# List all API keys (requires admin)
ci-admin key list

# Revoke an API key
ci-admin key revoke <key-id>
```

### User Isolation

Each user can only see and access their own jobs. Jobs submitted with one API key are not visible to users with different API keys.

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

## Running the System

The CI system consists of two independent services that work together:

### 1. Start the Controller (Required, Singleton)

The controller executes jobs and must be started first:

```bash
# Run with default settings
ci-controller

# Or with custom configuration
ci-controller --db-path /path/to/ci_jobs.db --interval 2.0
```

The controller will:
- Initialize the database schema (create tables if needed)
- Watch for new jobs in the database
- Execute jobs in Docker containers
- Update job status as they progress

### 2. Start the Server (Can be Multi-Replica)

Once the controller is running, start one or more server instances:

```bash
# Single server instance
python -m uvicorn ci_server.app:app --port 8000

# Multiple replicas for high availability
python -m uvicorn ci_server.app:app --port 8000 &
python -m uvicorn ci_server.app:app --port 8001 &
python -m uvicorn ci_server.app:app --port 8002 &
```

**Environment Variables:**
```bash
# Shared by both services
export CI_DB_PATH=/path/to/ci_jobs.db

# Controller-specific
export CI_CONTAINER_PREFIX=ci_prod_
export CI_RECONCILE_INTERVAL=2.0
```

**Important:** The controller must be running for jobs to execute. The server only accepts job submissions via HTTP API.

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
# Run all tests (uses 1 worker by default, configured in pytest.ini)
pytest tests/ -v

# Run tests sequentially with no parallelization (useful for debugging)
pytest tests/ -v -n 0

# Run tests with auto CPU core detection
pytest tests/ -v -n auto

# Run only unit tests
pytest tests/unit/ -v

# Run only end-to-end tests
pytest tests/e2e/ -v
```

**Test Parallelization:**

Tests run with pytest-xdist using **1 worker** by default (configured in `pytest.ini` with `addopts = -n 1`). This provides basic parallelization while ensuring test stability:
- **Default mode**: `pytest tests/ -v` (uses 1 worker)
- **Sequential mode**: `pytest tests/ -v -n 0` (no parallelization, useful for debugging)
- **Auto-detect mode**: `pytest tests/ -v -n auto` (creates one worker per CPU core)

Each worker runs tests independently with isolated server ports (8001, 8002, etc.) and separate SQLite databases to prevent conflicts.

## Architecture

- **Client** (`ci_client/`): CLI tool that zips projects and submits to server
  - `cli.py`: CLI commands (`submit`, `wait`, `list`) with API key support
  - `client.py`: HTTP client with sync/async submission, SSE streaming, and Bearer token auth
- **Server** (`ci_server/`): FastAPI app that runs pytest in Docker containers
  - `app.py`: REST API endpoints with authentication middleware
  - `auth.py`: API key generation, hashing, and validation
  - `executor.py`: Docker execution with streaming output
  - `repository.py`: Abstract interface for job persistence
  - `sqlite_repository.py`: SQLite implementation (default)
  - `models.py`: Data models for jobs, events, users, and API keys
- **Admin CLI** (`ci_admin/`): User and API key management
  - `cli.py`: Commands for creating/managing users and API keys
- **Common** (`ci_common/`): Shared models and interfaces
  - `models.py`: User, APIKey, Job, and JobEvent models
  - `repository.py`: Abstract repository interface with user management
- **Persistence** (`ci_persistence/`): Database implementations
  - `sqlite_repository.py`: SQLite with users, api_keys, and jobs tables
- **Communication**: Server-Sent Events for real-time output streaming
- **Authentication**: HTTPBearer with API key validation and SHA-256 hashing
- **Job Storage**: SQLite database (persistent across server restarts)
  - Default location: `ci_jobs.db`
  - Configurable via `CI_DB_PATH` environment variable
  - Schema: users, api_keys, jobs, job_events tables
  - Ready for PostgreSQL/MySQL migration if needed

## Requirements

- Python 3.8+
- Docker (for running tests in containers)

## License

MIT
