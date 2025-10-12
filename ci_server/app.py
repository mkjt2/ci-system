import asyncio
import json
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from .executor import run_tests_in_docker, run_tests_in_docker_streaming
from .models import Job, JobEvent
from .repository import JobRepository
from .sqlite_repository import SQLiteJobRepository

# Global repository instance (initialized at startup)
repository: JobRepository | None = None


def get_database_path() -> str:
    """
    Get the database path from environment or use default.

    Returns:
        Path to the SQLite database file

    Environment variables:
    - CI_DB_PATH: Custom database path (useful for testing)
    """
    return os.environ.get("CI_DB_PATH", "ci_jobs.db")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI app.

    Handles startup and shutdown events:
    - Startup: Initialize database and create tables
    - Shutdown: Close database connections
    """
    global repository

    # Startup: Initialize the repository with configured database path
    db_path = get_database_path()
    repository = SQLiteJobRepository(db_path)
    await repository.initialize()

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


@app.post("/submit")
async def submit_job(file: UploadFile = File(...)):
    """Run tests in Docker, return results when complete (non-streaming)."""
    success, output = await run_tests_in_docker(await file.read())
    return {"success": success, "output": output}


@app.post("/submit-stream")
async def submit_job_stream(request: Request, file: UploadFile = File(...)):
    """
    Run tests in Docker, stream results in real-time via SSE.

    Creates a job ID and tracks the job so users can reconnect with 'ci wait'.
    Cancels the job if client disconnects (Ctrl-C).
    """
    zip_data = await file.read()
    job_id = str(uuid.uuid4())
    repo = get_repository()

    # Create job in database
    job = Job(
        id=job_id,
        status="running",
        start_time=datetime.utcnow(),
    )
    await repo.create_job(job)

    async def event_generator():
        # First, send the job ID so client can print it
        yield f"data: {json.dumps({'type': 'job_id', 'job_id': job_id})}\n\n"

        # Create async generator task
        gen = run_tests_in_docker_streaming(zip_data)

        try:
            async for event_dict in gen:
                # Store event in database
                event = JobEvent.from_dict(event_dict, timestamp=datetime.utcnow())
                await repo.add_event(job_id, event)

                # Update job status if complete
                if event.type == "complete":
                    await repo.complete_job(
                        job_id,
                        success=event.success or False,
                        end_time=datetime.utcnow(),
                    )

                # Check if client has disconnected before yielding
                if await request.is_disconnected():
                    # Close the generator to trigger cleanup/cancellation
                    await gen.aclose()
                    return
                yield f"data: {json.dumps(event_dict)}\n\n"
        finally:
            # Ensure generator is closed on any exit
            await gen.aclose()

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

    This endpoint is non-blocking - it creates a job entry and starts
    processing in the background, then immediately returns the job ID.
    """
    job_id = str(uuid.uuid4())
    zip_data = await file.read()
    repo = get_repository()

    # Create job entry in the database
    job = Job(
        id=job_id,
        status="queued",
    )
    await repo.create_job(job)

    # Start job processing in background (fire-and-forget)
    asyncio.create_task(process_job_async(job_id, zip_data))

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

    async def event_generator() -> AsyncGenerator[str, None]:
        # Check if job is already completed when starting
        if job.status == "completed" and not from_beginning:
            # Job already completed and we're not showing history
            # Just send a status message and complete event
            yield f"data: {json.dumps({'type': 'log', 'data': 'Job already completed.\\n'})}\n\n"
            # Still send the complete event with final status
            if job.events and job.events[-1].type == "complete":
                yield f"data: {json.dumps(job.events[-1].to_dict())}\n\n"
            return

        # Determine starting position
        if from_beginning:
            # Stream all existing events (replay from beginning)
            for event in job.events:
                yield f"data: {json.dumps(event.to_dict())}\n\n"
                await asyncio.sleep(0.01)  # Small delay to avoid overwhelming client
            last_index = len(job.events)
        else:
            # Start from current position (only new events)
            last_index = len(job.events)

        # If job is still running, continue polling for new events
        if job.status == "running":
            while True:
                # Re-fetch job to get latest status
                current_job = await repo.get_job(job_id)
                if current_job is None:
                    break

                # Get new events since last check
                new_events = await repo.get_events(job_id, from_index=last_index)
                for event in new_events:
                    yield f"data: {json.dumps(event.to_dict())}\n\n"
                    last_index += 1

                # Check if job completed
                if current_job.status == "completed":
                    break

                await asyncio.sleep(0.1)  # Poll interval

    return StreamingResponse(
        event_generator(),
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
