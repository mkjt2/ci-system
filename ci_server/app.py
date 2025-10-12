import json
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import StreamingResponse
from .executor import run_tests_in_docker, run_tests_in_docker_streaming

app = FastAPI()


@app.post("/submit")
async def submit_job(file: UploadFile = File(...)):
    """Run tests in Docker, return results when complete (non-streaming)."""
    success, output = await run_tests_in_docker(await file.read())
    return {"success": success, "output": output}


@app.post("/submit-stream")
async def submit_job_stream(file: UploadFile = File(...)):
    """Run tests in Docker, stream results in real-time via SSE."""
    zip_data = await file.read()

    async def event_generator():
        async for event in run_tests_in_docker_streaming(zip_data):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )