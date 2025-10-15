import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from ci_common.models import Job, User
from ci_common.repository import JobRepository
from ci_controller.container_manager import ContainerManager
from ci_persistence.sqlite_repository import SQLiteJobRepository

from .auth import create_get_current_user_dependency

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


# Create authentication dependency
get_current_user = create_get_current_user_dependency(get_repository)


async def create_job_from_upload(
    file: UploadFile, user: User, repo: JobRepository
) -> tuple[str, Job]:
    """
    Create a job from an uploaded zip file.

    This helper function handles the common workflow of:
    1. Generating a unique job ID
    2. Reading the uploaded zip file
    3. Stashing the zip to a temporary location
    4. Creating a job entry in the database

    Args:
        file: Uploaded zip file containing the project
        user: Authenticated user who owns the job
        repo: Job repository for database operations

    Returns:
        Tuple of (job_id, job) where job_id is the UUID string and job is the Job object

    Raises:
        Exception: If file I/O or database operations fail
    """
    import tempfile

    job_id = str(uuid.uuid4())
    zip_data = await file.read()

    # Stash the zip file to a temporary location
    fd, zip_file_path = tempfile.mkstemp(suffix=".zip", prefix=f"ci_job_{job_id}_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(zip_data)
    except Exception:
        os.close(fd)
        raise

    # Create job entry in the database with zip file path and user ownership
    job = Job(
        id=job_id,
        status="queued",
        zip_file_path=zip_file_path,
        user_id=user.id,
    )
    await repo.create_job(job)

    return job_id, job


async def stream_job_events(
    job_id: str,
    repo: JobRepository,
    cm: ContainerManager,
    request: Request | None = None,
    from_beginning: bool = True,
) -> AsyncGenerator[str, None]:
    """
    Helper function to stream job events as SSE.

    Streams logs directly from Docker container in real-time.

    Args:
        job_id: UUID of the job to stream
        repo: JobRepository instance for database access
        cm: ContainerManager instance for Docker operations
        request: Optional FastAPI request to check for client disconnection
        from_beginning: If True, stream all logs. If False, only stream new logs.

    Yields:
        SSE-formatted event strings
    """

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
    # The reconciliation loop runs every 2 seconds, so we need to wait at least 2-3 cycles
    # to handle various edge cases:
    # - Controller might be in the middle of a cycle when container exits
    # - System might be under load (especially in CI environments)
    # - Docker operations might have slight delays
    max_wait = 15  # 15 seconds max wait for finalization (allows ~7 reconciliation cycles)
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
async def submit_job(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    repo: JobRepository = Depends(get_repository),
    cm: ContainerManager = Depends(get_container_manager),
):
    """
    Run tests in Docker, stream results in real-time via SSE.

    Requires authentication via API key (Bearer token in Authorization header).

    Uses controller pattern: stashes the zip file, creates a queued job,
    and the controller's reconciliation loop handles container creation
    and execution.
    """
    job_id, _ = await create_job_from_upload(file, user, repo)

    # Controller will pick up the queued job and start it
    # Stream the results as they become available
    return StreamingResponse(
        stream_job_events(job_id, repo, cm, request, from_beginning=True),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/submit-stream")
async def submit_job_stream(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    repo: JobRepository = Depends(get_repository),
    cm: ContainerManager = Depends(get_container_manager),
):
    """
    Run tests in Docker, stream results in real-time via SSE.

    Requires authentication via API key (Bearer token in Authorization header).

    Creates a job ID and tracks the job so users can reconnect with 'ci wait'.
    First sends the job ID, then streams all events. Uses controller pattern.
    """
    job_id, _ = await create_job_from_upload(file, user, repo)

    async def event_generator():
        # First, send the job ID so client can print it
        yield f"data: {json.dumps({'type': 'job_id', 'job_id': job_id})}\n\n"

        # Then stream all job events
        async for event in stream_job_events(
            job_id, repo, cm, request, from_beginning=True
        ):
            yield event

    # Controller will pick up the queued job and start it
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/submit-async")
async def submit_job_async(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    repo: JobRepository = Depends(get_repository),
) -> dict[str, str]:
    """
    Submit a job and return job ID immediately. Job runs in background.

    Requires authentication via API key (Bearer token in Authorization header).

    Args:
        file: Uploaded zip file containing the project
        user: Authenticated user (injected by dependency)
        repo: Job repository (injected by dependency)

    Returns:
        Dictionary with job_id that can be used to query job status

    Uses controller pattern: stashes zip file, creates queued job, and
    returns immediately. Controller handles execution in background.
    """
    job_id, _ = await create_job_from_upload(file, user, repo)

    # Controller will pick up the queued job and start it
    return {"job_id": job_id}


@app.get("/jobs/{job_id}/stream")
async def stream_job_logs(
    job_id: str,
    from_beginning: bool = False,
    user: User = Depends(get_current_user),
    repo: JobRepository = Depends(get_repository),
    cm: ContainerManager = Depends(get_container_manager),
) -> StreamingResponse:
    """
    Stream logs for a job via Server-Sent Events (SSE).

    Requires authentication. Users can only stream logs for their own jobs.

    Args:
        job_id: UUID of the job to stream logs for
        from_beginning: If True, streams all past events first. If False (default),
                       only streams new events from current position forward.
        user: Authenticated user (injected by dependency)
        repo: Job repository (injected by dependency)
        cm: Container manager (injected by dependency)

    Returns:
        StreamingResponse with SSE format events

    Raises:
        HTTPException: 404 if job_id not found
        HTTPException: 403 if user doesn't own this job

    By default (from_beginning=False), only streams new events. This is useful
    for monitoring a running job from another terminal without seeing all history.
    With from_beginning=True, replays all events from the start.
    """
    job = await repo.get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Authorization: Users can only access their own jobs
    if job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    return StreamingResponse(
        stream_job_events(
            job_id, repo, cm, request=None, from_beginning=from_beginning
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/health")
async def health_check() -> dict[str, str]:
    """
    Health check endpoint (no authentication required).

    Returns:
        Dictionary with status="ok" if server is running
    """
    return {"status": "ok"}


@app.get("/jobs")
async def list_jobs(
    user: User = Depends(get_current_user),
    repo: JobRepository = Depends(get_repository),
) -> list[dict[str, Any]]:
    """
    List all jobs for the authenticated user.

    Requires authentication. Users can only see their own jobs.

    Args:
        user: Authenticated user (injected by dependency)
        repo: Job repository (injected by dependency)

    Returns:
        List of job dictionaries with job_id, status, success, start_time, and end_time
    """
    jobs = await repo.list_user_jobs(user.id)
    return [job.to_summary_dict() for job in jobs]


@app.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    user: User = Depends(get_current_user),
    repo: JobRepository = Depends(get_repository),
) -> dict[str, Any]:
    """
    Get job status and metadata (non-streaming).

    Requires authentication. Users can only access their own jobs.

    Args:
        job_id: UUID of the job to query
        user: Authenticated user (injected by dependency)
        repo: Job repository (injected by dependency)

    Returns:
        Dictionary with job_id, status (queued/running/completed), and success (bool or None)

    Raises:
        HTTPException: 404 if job_id not found
        HTTPException: 403 if user doesn't own this job
    """
    job = await repo.get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Authorization: Users can only access their own jobs
    if job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    return job.to_summary_dict()
