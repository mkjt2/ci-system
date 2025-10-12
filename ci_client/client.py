import io
import json
import zipfile
from pathlib import Path
from typing import Generator
import requests


def create_project_zip(project_dir: Path) -> bytes:
    """Create a zip file of the project directory."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path in project_dir.rglob('*'):
            if path.is_file() and not any(p.startswith('.') or p == '__pycache__' for p in path.parts):
                zf.write(path, path.relative_to(project_dir))
    return zip_buffer.getvalue()


def submit_tests(project_dir: Path, server_url: str = "http://localhost:8000") -> tuple[bool, str]:
    """Submit tests to the CI server (non-streaming, for backward compatibility)."""
    try:
        response = requests.post(
            f"{server_url}/submit",
            files={"file": ("project.zip", create_project_zip(project_dir), "application/zip")},
            timeout=300,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("success", False), result.get("output", "")
    except requests.exceptions.RequestException as e:
        return False, f"Error submitting to CI server: {e}\n"


def submit_tests_streaming(project_dir: Path, server_url: str = "http://localhost:8000") -> Generator[dict, None, None]:
    """Submit tests to the CI server with streaming output via SSE."""
    try:
        response = requests.post(
            f"{server_url}/submit-stream",
            files={"file": ("project.zip", create_project_zip(project_dir), "application/zip")},
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