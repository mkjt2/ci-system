import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from ci_common.models import Job, JobEvent
from ci_common.repository import JobRepository
from ci_controller.container_manager import ContainerManager
from ci_persistence.sqlite_repository import SQLiteJobRepository

from .executor import run_tests_in_docker_streaming

# Configure logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global instances (initialized at startup)
repository: JobRepository | None = None
container_manager: ContainerManager | None = None


def get_database_path() -> str:
    """
    Get the database path from environment or use default.

    Returns:
        Path to the SQLite database file

    Environment variables:
    - CI_DB_PATH: Custom database path (useful for testing)
    """
    return os.environ.get("CI_DB_PATH", "ci_jobs.db")


def get_container_prefix() -> str:
    """
    Get the container name prefix from environment.

    Returns:
        Container name prefix for Docker isolation

    Environment variables:
    - CI_CONTAINER_PREFIX: Prefix for container names (useful for parallel testing)
    """
    return os.environ.get("CI_CONTAINER_PREFIX", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI app.

    Handles startup and shutdown events:
    - Startup: Connect to database (schema managed by ci-controller)
    - Shutdown: Close database connections

    Note: The ci-controller service must be running separately to execute jobs.
    The server only handles HTTP API requests and database operations.
    """
    global repository, container_manager

    # Startup: Connect to the database (controller initializes schema)
    db_path = get_database_path()
    repository = SQLiteJobRepository(db_path)
    # NOTE: Controller owns schema initialization via repository.initialize()
    # Server only connects to existing database

    # Initialize container manager for log streaming (read-only operations)
    container_prefix = get_container_prefix()
    container_manager = ContainerManager(container_name_prefix=container_prefix)

    yield

    # Shutdown: Close repository connections
    if repository:
        await repository.close()


app = FastAPI(lifespan=lifespan)


def get_repository() -> JobRepository:
    """
    Get the global repository instance.

    Returns:
        The initialized JobRepository

    Raises:
        RuntimeError: If repository is not initialized
    """
    if repository is None:
        raise RuntimeError("Repository not initialized")
    return repository


def get_container_manager() -> ContainerManager:
    """
    Get the global container manager instance.

    Returns:
        The initialized ContainerManager

    Raises:
        RuntimeError: If container manager is not initialized
    """
    if container_manager is None:
        raise RuntimeError("Container manager not initialized")
    return container_manager




async def process_job_async(job_id: str, zip_data: bytes) -> None:
    """
    Process a job asynchronously and store output in job store.

    Args:
        job_id: UUID of the job to process
        zip_data: Zipped project data to test

    This function runs in the background and updates the job store
    with events as they occur during test execution.
    """
    repo = get_repository()

    # Update job status to running
    await repo.update_job_status(job_id, "running", start_time=datetime.utcnow())

    try:
        # Stream events from Docker execution and store them
        async for event_dict in run_tests_in_docker_streaming(zip_data):
            # Convert dict to JobEvent and store
            event = JobEvent.from_dict(event_dict, timestamp=datetime.utcnow())
            await repo.add_event(job_id, event)

            # If this is a completion event, mark job as completed
            if event.type == "complete":
                await repo.complete_job(
                    job_id, success=event.success or False, end_time=datetime.utcnow()
                )
    except Exception as e:
        # Handle any unexpected errors during job processing
        error_event = JobEvent(
            type="log", data=f"Error: {e}\n", timestamp=datetime.utcnow()
        )
        await repo.add_event(job_id, error_event)

        complete_event = JobEvent(
            type="complete", success=False, timestamp=datetime.utcnow()
        )
        await repo.add_event(job_id, complete_event)

        await repo.complete_job(job_id, success=False, end_time=datetime.utcnow())


async def stream_job_events(
    job_id: str, request: Request | None = None, from_beginning: bool = True
) -> AsyncGenerator[str, None]:
    """
    Helper function to stream job events as SSE.

    Streams logs directly from Docker container in real-time.

    Args:
        job_id: UUID of the job to stream
        request: Optional FastAPI request to check for client disconnection
        from_beginning: If True, stream all logs. If False, only stream new logs.

    Yields:
        SSE-formatted event strings
    """
    repo = get_repository()
    cm = get_container_manager()

    job = await repo.get_job(job_id)

    if job is None:
        yield f"data: {json.dumps({'type': 'log', 'data': 'Job not found.\\n'})}\n\n"
        yield f"data: {json.dumps({'type': 'complete', 'success': False})}\n\n"
        return

    # Wait for job to start running (with timeout)
    max_wait = 30  # 30 seconds timeout
    waited = 0
    while job.status == "queued" and waited < max_wait:
        await asyncio.sleep(0.5)
        waited += 0.5
        job = await repo.get_job(job_id)
        if job is None:
            yield f"data: {json.dumps({'type': 'log', 'data': 'Job disappeared.\\n'})}\n\n"
            yield f"data: {json.dumps({'type': 'complete', 'success': False})}\n\n"
            return

    # Check if job is in a terminal state
    if job.status in ["completed", "failed", "cancelled"]:
        # If not requesting from beginning, just notify that job is done
        # (forward-only mode: only show events from when you join, no historical logs)
        if not from_beginning:
            yield f"data: {json.dumps({'type': 'log', 'data': 'Job already completed.\\n'})}\n\n"
            success = job.success if job.success is not None else False
            yield f"data: {json.dumps({'type': 'complete', 'success': success})}\n\n"
            return

        # Otherwise stream all logs from completed container (when --all is used)
        if job.container_id:
            try:
                async for log_line in cm.stream_logs(job.container_id, follow=False):
                    yield f"data: {json.dumps({'type': 'log', 'data': log_line})}\n\n"

                    # Check if client disconnected
                    if request and await request.is_disconnected():
                        return
            except Exception:
                # Container might be gone, that's ok
                pass

        # Send completion event with final status
        success = job.success if job.success is not None else False
        yield f"data: {json.dumps({'type': 'complete', 'success': success})}\n\n"
        return

    # Job is running - stream logs from Docker
    if job.status == "running" and job.container_id:
        try:
            # Stream logs directly from Docker (with --follow for real-time)
            async for log_line in cm.stream_logs(job.container_id, follow=True):
                yield f"data: {json.dumps({'type': 'log', 'data': log_line})}\n\n"

                # Check if client disconnected
                if request and await request.is_disconnected():
                    return

                # Periodically check if job completed
                # (Docker logs stream will end when container exits)
        except Exception as e:
            # Log streaming failed
            yield f"data: {json.dumps({'type': 'log', 'data': f'Error streaming logs: {e}\\n'})}\n\n"

    # Job finished, wait for reconciliation loop to finalize it
    # (The reconciliation loop sets the success field based on container exit code)
    max_wait = 5  # 5 seconds max wait for finalization
    waited = 0
    final_job = await repo.get_job(job_id)
    while final_job and final_job.success is None and waited < max_wait:
        await asyncio.sleep(0.1)
        waited += 0.1
        final_job = await repo.get_job(job_id)

    if final_job:
        success = final_job.success if final_job.success is not None else False
        yield f"data: {json.dumps({'type': 'complete', 'success': success})}\n\n"
    else:
        # Job disappeared
        yield f"data: {json.dumps({'type': 'complete', 'success': False})}\n\n"


@app.post("/submit")
async def submit_job(request: Request, file: UploadFile = File(...)):
    """
    Run tests in Docker, stream results in real-time via SSE.

    Uses controller pattern: stashes the zip file, creates a queued job,
    and the controller's reconciliation loop handles container creation
    and execution.
    """
    import tempfile

    job_id = str(uuid.uuid4())
    zip_data = await file.read()
    repo = get_repository()

    # Stash the zip file to a temporary location
    fd, zip_file_path = tempfile.mkstemp(suffix=".zip", prefix=f"ci_job_{job_id}_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(zip_data)
    except Exception:
        os.close(fd)
        raise

    # Create job entry in the database with zip file path
    job = Job(
        id=job_id,
        status="queued",
        zip_file_path=zip_file_path,
    )
    await repo.create_job(job)

    # Controller will pick up the queued job and start it
    # Stream the results as they become available
    return StreamingResponse(
        stream_job_events(job_id, request, from_beginning=True),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/submit-stream")
async def submit_job_stream(request: Request, file: UploadFile = File(...)):
    """
    Run tests in Docker, stream results in real-time via SSE.

    Creates a job ID and tracks the job so users can reconnect with 'ci wait'.
    First sends the job ID, then streams all events. Uses controller pattern.
    """
    import tempfile

    job_id = str(uuid.uuid4())
    zip_data = await file.read()
    repo = get_repository()

    # Stash the zip file to a temporary location
    fd, zip_file_path = tempfile.mkstemp(suffix=".zip", prefix=f"ci_job_{job_id}_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(zip_data)
    except Exception:
        os.close(fd)
        raise

    # Create job entry in the database with zip file path
    job = Job(
        id=job_id,
        status="queued",
        zip_file_path=zip_file_path,
    )
    await repo.create_job(job)

    async def event_generator():
        # First, send the job ID so client can print it
        yield f"data: {json.dumps({'type': 'job_id', 'job_id': job_id})}\n\n"

        # Then stream all job events
        async for event in stream_job_events(job_id, request, from_beginning=True):
            yield event

    # Controller will pick up the queued job and start it
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/submit-async")
async def submit_job_async(file: UploadFile = File(...)) -> dict[str, str]:
    """
    Submit a job and return job ID immediately. Job runs in background.

    Args:
        file: Uploaded zip file containing the project

    Returns:
        Dictionary with job_id that can be used to query job status

    Uses controller pattern: stashes zip file, creates queued job, and
    returns immediately. Controller handles execution in background.
    """
    import tempfile

    job_id = str(uuid.uuid4())
    zip_data = await file.read()
    repo = get_repository()

    # Stash the zip file to a temporary location
    fd, zip_file_path = tempfile.mkstemp(suffix=".zip", prefix=f"ci_job_{job_id}_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(zip_data)
    except Exception:
        os.close(fd)
        raise

    # Create job entry in the database with zip file path
    job = Job(
        id=job_id,
        status="queued",
        zip_file_path=zip_file_path,
    )
    await repo.create_job(job)

    # Controller will pick up the queued job and start it
    return {"job_id": job_id}


@app.get("/jobs/{job_id}/stream")
async def stream_job_logs(
    job_id: str, from_beginning: bool = False
) -> StreamingResponse:
    """
    Stream logs for a job via Server-Sent Events (SSE).

    Args:
        job_id: UUID of the job to stream logs for
        from_beginning: If True, streams all past events first. If False (default),
                       only streams new events from current position forward.

    Returns:
        StreamingResponse with SSE format events

    Raises:
        HTTPException: 404 if job_id not found

    By default (from_beginning=False), only streams new events. This is useful
    for monitoring a running job from another terminal without seeing all history.
    With from_beginning=True, replays all events from the start.
    """
    repo = get_repository()
    job = await repo.get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return StreamingResponse(
        stream_job_events(job_id, request=None, from_beginning=from_beginning),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/jobs")
async def list_jobs() -> list[dict[str, Any]]:
    """
    List all jobs with their status and metadata.

    Returns:
        List of job dictionaries with job_id, status, success, start_time, and end_time
    """
    repo = get_repository()
    jobs = await repo.list_jobs()
    return [job.to_summary_dict() for job in jobs]


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> dict[str, Any]:
    """
    Get job status and metadata (non-streaming).

    Args:
        job_id: UUID of the job to query

    Returns:
        Dictionary with job_id, status (queued/running/completed), and success (bool or None)

    Raises:
        HTTPException: 404 if job_id not found
    """
    repo = get_repository()
    job = await repo.get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return job.to_summary_dict()
