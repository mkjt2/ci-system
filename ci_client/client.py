import io
import json
import zipfile
from pathlib import Path
from typing import Generator
import requests


def create_project_zip(project_dir: Path) -> bytes:
    """Create a zip file of the project directory."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in project_dir.rglob("*"):
            if path.is_file() and not any(
                p.startswith(".") or p == "__pycache__" for p in path.parts
            ):
                zf.write(path, path.relative_to(project_dir))
    return zip_buffer.getvalue()


def submit_tests(
    project_dir: Path, server_url: str = "http://localhost:8000"
) -> tuple[bool, str]:
    """Submit tests to the CI server (non-streaming, for backward compatibility)."""
    try:
        response = requests.post(
            f"{server_url}/submit",
            files={
                "file": (
                    "project.zip",
                    create_project_zip(project_dir),
                    "application/zip",
                )
            },
            timeout=300,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("success", False), result.get("output", "")
    except requests.exceptions.RequestException as e:
        return False, f"Error submitting to CI server: {e}\n"


def submit_tests_streaming(
    project_dir: Path, server_url: str = "http://localhost:8000"
) -> Generator[dict, None, None]:
    """Submit tests to the CI server with streaming output via SSE."""
    try:
        response = requests.post(
            f"{server_url}/submit-stream",
            files={
                "file": (
                    "project.zip",
                    create_project_zip(project_dir),
                    "application/zip",
                )
            },
            stream=True,
            timeout=300,
        )
        response.raise_for_status()

        for line in response.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                yield json.loads(line[6:])
    except requests.exceptions.RequestException as e:
        yield {"type": "log", "data": f"Error submitting to CI server: {e}\n"}
        yield {"type": "complete", "success": False}


def submit_tests_async(
    project_dir: Path, server_url: str = "http://localhost:8000"
) -> str:
    """
    Submit tests to the CI server asynchronously and return job ID immediately.

    Args:
        project_dir: Path to the project directory to test
        server_url: Base URL of the CI server

    Returns:
        str: UUID job ID that can be used to query job status or wait for completion

    Raises:
        RuntimeError: If submission fails due to network or server error

    This function is non-blocking - it submits the project and returns immediately
    with a job ID. The job runs in the background on the server.
    """
    try:
        response = requests.post(
            f"{server_url}/submit-async",
            files={
                "file": (
                    "project.zip",
                    create_project_zip(project_dir),
                    "application/zip",
                )
            },
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        return result["job_id"]
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Error submitting to CI server: {e}")


def wait_for_job(
    job_id: str, server_url: str = "http://localhost:8000", from_beginning: bool = False
) -> Generator[dict, None, None]:
    """
    Wait for a job to complete and stream its output via Server-Sent Events.

    Args:
        job_id: UUID of the job to wait for
        server_url: Base URL of the CI server
        from_beginning: If True, streams all logs from the beginning.
                       If False (default), only streams new logs from current position.

    Yields:
        dict: Event dictionaries with 'type' and other fields:
            - {"type": "log", "data": str} - Log output from test execution
            - {"type": "complete", "success": bool} - Final completion status

    By default, only streams new logs (forward-looking). This is useful for
    monitoring a running job from another terminal without seeing all history.
    Use from_beginning=True to replay all logs from the start.
    """
    try:
        # Only add param if True (FastAPI will use default False if not present)
        params = {"from_beginning": from_beginning} if from_beginning else {}
        response = requests.get(
            f"{server_url}/jobs/{job_id}/stream",
            params=params,
            stream=True,
            timeout=300,
        )
        response.raise_for_status()

        # Parse SSE format: "data: {...}\n\n"
        for line in response.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                yield json.loads(line[6:])
    except requests.exceptions.RequestException as e:
        yield {"type": "log", "data": f"Error waiting for job: {e}\n"}
        yield {"type": "complete", "success": False}
