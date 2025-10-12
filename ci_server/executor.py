import asyncio
import io
import tempfile
import zipfile
from collections.abc import AsyncGenerator
from pathlib import Path


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


async def run_tests_in_docker_streaming(
    zip_data: bytes, cancel_event: asyncio.Event | None = None
) -> AsyncGenerator[dict, None]:
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

            try:
                while True:
                    # Check if cancellation was requested
                    if cancel_event and cancel_event.is_set():
                        process.terminate()
                        await process.wait()
                        yield {"type": "log", "data": "\nJob cancelled by user.\n"}
                        yield {"type": "complete", "success": False, "cancelled": True}
                        return

                    # Read with timeout to allow checking cancel_event periodically
                    try:
                        line = await asyncio.wait_for(
                            process.stdout.readline(), timeout=0.1
                        )
                        if not line:
                            break
                        yield {"type": "log", "data": line.decode()}
                    except asyncio.TimeoutError:
                        # No data available, continue loop to check cancel_event
                        continue

                await process.wait()
                yield {"type": "complete", "success": process.returncode == 0}
            except (asyncio.CancelledError, GeneratorExit):
                # Task/generator was cancelled (e.g., client disconnected)
                process.terminate()
                await process.wait()
                # Don't yield here as generator is closing
                raise

        except Exception as e:
            yield {"type": "log", "data": f"Error running tests: {e}\n"}
            yield {"type": "complete", "success": False}
