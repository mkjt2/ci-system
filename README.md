# CI System

A simple CI system for Python projects that runs tests in isolated Docker containers with real-time streaming output.

## Features

- **Simple CLI**: Submit test jobs with a single command
- **Docker Isolation**: Tests run in clean Python containers
- **Real-time Streaming**: See test output as it happens via Server-Sent Events
- **Easy Setup**: Minimal configuration required

## Installation

```bash
pip install -e .
```

## Usage

From your Python project root (requires `src/`, `tests/`, and `requirements.txt`):

```bash
ci submit test
```

The CLI will:
1. Zip your project
2. Send it to the CI server
3. Stream test results in real-time
4. Exit with code 0 (pass) or 1 (fail)

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
- **Server** (`ci_server/`): FastAPI app that runs pytest in Docker containers
- **Communication**: Server-Sent Events for real-time output streaming

## Requirements

- Python 3.8+
- Docker (for running tests in containers)

## License

MIT
