import asyncio
import tempfile
import zipfile
import io
from pathlib import Path
from typing import AsyncGenerator


async def run_tests_in_docker(zip_data: bytes) -> tuple[bool, str]:
    """Run tests in Docker container, return all output at once (non-streaming)."""
    output_lines = []
    success = False
    async for event in run_tests_in_docker_streaming(zip_data):
        if event["type"] == "log":
            output_lines.append(event["data"])
        elif event["type"] == "complete":
            success = event["success"]
    return success, "".join(output_lines)


async def run_tests_in_docker_streaming(zip_data: bytes) -> AsyncGenerator[dict, None]:
    """Run tests in Docker container, streaming output line-by-line."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            zf.extractall(temp_path)

        if not (temp_path / "requirements.txt").exists():
            yield {
                "type": "log",
                "data": "Error: requirements.txt not found in project\n",
            }
            yield {"type": "complete", "success": False}
            return

        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "run",
                "--rm",
                "-v",
                f"{temp_path}:/workspace:ro",
                "-w",
                "/workspace",
                "python:3.12-slim",
                "sh",
                "-c",
                "pip install -q -r requirements.txt && python -m pytest -v",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            # Assert stdout is available (we specified PIPE)
            assert process.stdout is not None, (
                "stdout should be available when PIPE is specified"
            )

            while line := await process.stdout.readline():
                yield {"type": "log", "data": line.decode()}

            await process.wait()
            yield {"type": "complete", "success": process.returncode == 0}

        except Exception as e:
            yield {"type": "log", "data": f"Error running tests: {e}\n"}
            yield {"type": "complete", "success": False}
