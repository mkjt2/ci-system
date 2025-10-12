import json
import uuid
import asyncio
from typing import Dict, List, Any, Optional, AsyncGenerator
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse
from .executor import run_tests_in_docker, run_tests_in_docker_streaming

app = FastAPI()

# In-memory job store (does not survive restarts)
# Each job has: id (str), status (str), events (List[dict]), success (Optional[bool])
jobs: Dict[str, Dict[str, Any]] = {}


async def process_job_async(job_id: str, zip_data: bytes) -> None:
    """
    Process a job asynchronously and store output in job store.

    Args:
        job_id: UUID of the job to process
        zip_data: Zipped project data to test

    This function runs in the background and updates the job store
    with events as they occur during test execution.
    """
    job = jobs[job_id]
    job["status"] = "running"

    try:
        # Stream events from Docker execution and store them
        async for event in run_tests_in_docker_streaming(zip_data):
            job["events"].append(event)
            if event["type"] == "complete":
                job["status"] = "completed"
                job["success"] = event["success"]
    except Exception as e:
        # Handle any unexpected errors during job processing
        job["events"].append({"type": "log", "data": f"Error: {e}\n"})
        job["events"].append({"type": "complete", "success": False})
        job["status"] = "completed"
        job["success"] = False


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

    # Initialize job in store
    jobs[job_id] = {
        "id": job_id,
        "status": "running",
        "events": [],
        "success": None,
    }

    async def event_generator():
        # First, send the job ID so client can print it
        yield f"data: {json.dumps({'type': 'job_id', 'job_id': job_id})}\n\n"

        # Create async generator task
        gen = run_tests_in_docker_streaming(zip_data)

        try:
            async for event in gen:
                # Store event in job history
                jobs[job_id]["events"].append(event)
                if event["type"] == "complete":
                    jobs[job_id]["status"] = "completed"
                    jobs[job_id]["success"] = event.get("success", False)

                # Check if client has disconnected before yielding
                if await request.is_disconnected():
                    # Close the generator to trigger cleanup/cancellation
                    await gen.aclose()
                    return
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            # Ensure generator is closed on any exit
            await gen.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/submit-async")
async def submit_job_async(file: UploadFile = File(...)) -> Dict[str, str]:
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

    # Initialize job entry in the store
    jobs[job_id] = {
        "id": job_id,
        "status": "queued",  # Will become "running" then "completed"
        "events": [],  # Accumulates log and complete events
        "success": None,  # Set to True/False when job completes
    }

    # Start job processing in background (fire-and-forget)
    asyncio.create_task(process_job_async(job_id, zip_data))

    return {"job_id": job_id}


@app.get("/jobs/{job_id}/stream")
async def stream_job_logs(job_id: str) -> StreamingResponse:
    """
    Stream logs for a job via Server-Sent Events (SSE).

    Args:
        job_id: UUID of the job to stream logs for

    Returns:
        StreamingResponse with SSE format events

    Raises:
        HTTPException: 404 if job_id not found

    This endpoint supports reconnection - it always streams all events
    from the beginning. If the job is still running when we catch up,
    it continues to wait for and stream new events as they arrive.
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]

    async def event_generator() -> AsyncGenerator[str, None]:
        # First, stream all existing events (allows reconnection/replay)
        for event in job["events"]:
            yield f"data: {json.dumps(event)}\n\n"
            await asyncio.sleep(0.01)  # Small delay to avoid overwhelming client

        # If job is still running, continue polling for new events
        if job["status"] == "running":
            last_index = len(job["events"])
            while job["status"] == "running":
                await asyncio.sleep(0.1)  # Poll interval
                # Stream any new events that have arrived
                while last_index < len(job["events"]):
                    yield f"data: {json.dumps(job['events'][last_index])}\n\n"
                    last_index += 1

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> Dict[str, Any]:
    """
    Get job status and metadata (non-streaming).

    Args:
        job_id: UUID of the job to query

    Returns:
        Dictionary with job_id, status (queued/running/completed), and success (bool or None)

    Raises:
        HTTPException: 404 if job_id not found
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    return {
        "job_id": job["id"],
        "status": job["status"],  # "queued", "running", or "completed"
        "success": job["success"],  # None until completed, then True/False
    }
