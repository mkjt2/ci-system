# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A simple CI system for Python projects. Users submit projects via CLI, which zips and sends them to a FastAPI server. The server runs pytest in a Docker container and streams results back in real-time.

## Architecture

**Client** (`ci_client/`):
- `cli.py` - CLI entry point, handles `ci submit test` command
- `client.py` - HTTP client with streaming support (SSE)

**Server** (`ci_server/`):
- `app.py` - FastAPI app with `/submit` (blocking) and `/submit-stream` (SSE) endpoints
- `executor.py` - Executes pytest in Docker container, supports streaming output

**Flow**:
1. User runs `ci submit test` from project root
2. CLI zips project (excluding `.` and `__pycache__`)
3. Client POSTs zip to `/submit-stream`
4. Server extracts to temp dir, mounts read-only into Docker
5. Docker runs `pip install -q -r requirements.txt && python -m pytest -v`
6. Output streams back to client as Server-Sent Events
7. CLI prints output in real-time, exits with pytest's exit code

## Usage

**Expected project structure**:
- `src/` - Source code
- `tests/` - Test files
- `requirements.txt` - Python dependencies (must include pytest)

**Running tests**:
```bash
ci submit test
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