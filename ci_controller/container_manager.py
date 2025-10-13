"""
Container manager for Docker-based job execution.

This module provides an abstraction over Docker operations for managing
test execution containers. It tracks container lifecycle and provides
methods for creating, monitoring, and cleaning up containers.
"""

import asyncio
import io
import json
import tempfile
import zipfile
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


@dataclass
class ContainerInfo:
    """
    Information about a Docker container.

    Represents the current state of a container from Docker's perspective.
    """

    container_id: str
    name: str  # Job ID used as container name
    status: Literal[
        "created", "running", "exited", "paused", "restarting", "removing", "dead"
    ]
    exit_code: int | None
    started_at: datetime | None
    finished_at: datetime | None


class ContainerManager:
    """
    Manages Docker containers for CI job execution.

    This class provides high-level operations for creating, monitoring,
    and cleaning up Docker containers that run pytest tests.
    """

    def __init__(self, container_name_prefix: str = ""):
        """
        Initialize the container manager.

        Args:
            container_name_prefix: Optional prefix for container names.
                                  Containers are named as "{prefix}{job_id}".
                                  This enables parallel test execution without interference.
        """
        self.image = "python:3.12-slim"
        self.container_name_prefix = container_name_prefix

    def _get_container_name(self, job_id: str) -> str:
        """
        Get the full container name with prefix.

        Args:
            job_id: Job identifier

        Returns:
            Full container name: "{prefix}{job_id}"
        """
        return f"{self.container_name_prefix}{job_id}"

    async def create_container(
        self, job_id: str, zip_file_path: str
    ) -> tuple[str, Path]:
        """
        Create a Docker container for running tests.

        Args:
            job_id: Unique job identifier (used as container name)
            zip_file_path: Path to zipped project file

        Returns:
            Tuple of (container_id, temp_dir_path)
            The temp_dir_path must be kept alive for container execution

        Raises:
            RuntimeError: If container creation fails
        """
        # Create temporary directory for project files
        temp_dir = tempfile.mkdtemp(prefix=f"ci_job_{job_id}_")
        temp_path = Path(temp_dir)

        try:
            # Read and extract project files from the stashed zip
            with open(zip_file_path, "rb") as f:
                zip_data = f.read()

            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                zf.extractall(temp_path)

            # Verify requirements.txt exists
            if not (temp_path / "requirements.txt").exists():
                raise RuntimeError("requirements.txt not found in project")

            # Create container (but don't start yet)
            # Use prefixed job_id as container name for namespace isolation
            container_name = self._get_container_name(job_id)
            process = await asyncio.create_subprocess_exec(
                "docker",
                "create",
                "--name",
                container_name,
                "-v",
                f"{temp_path}:/workspace:ro",
                "-w",
                "/workspace",
                self.image,
                "sh",
                "-c",
                "pip install -q -r requirements.txt && python -m pytest -v",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                raise RuntimeError(f"Failed to create container: {stderr.decode()}")

            container_id = stdout.decode().strip()
            return container_id, temp_path

        except Exception as e:
            # Clean up temp directory on failure
            import shutil

            shutil.rmtree(temp_path, ignore_errors=True)
            raise RuntimeError(f"Failed to create container: {e}") from e

    async def start_container(self, container_id: str) -> None:
        """
        Start a created container.

        Args:
            container_id: Docker container ID or name

        Raises:
            RuntimeError: If container start fails
        """
        process = await asyncio.create_subprocess_exec(
            "docker",
            "start",
            container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        _, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"Failed to start container: {stderr.decode()}")

    async def get_container_info(self, job_id: str) -> ContainerInfo | None:
        """
        Get information about a container by job ID.

        Args:
            job_id: Job identifier (used as container name)

        Returns:
            ContainerInfo if container exists, None otherwise
        """
        container_name = self._get_container_name(job_id)
        process = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, _ = await process.communicate()

        if process.returncode != 0:
            # Container doesn't exist
            return None

        # Parse JSON output
        try:
            data = json.loads(stdout.decode())
            if not data:
                return None

            container = data[0]
            state = container["State"]

            # Parse timestamps
            started_at = None
            if state.get("StartedAt"):
                try:
                    started_at = datetime.fromisoformat(
                        state["StartedAt"].replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    pass

            finished_at = None
            if state.get("FinishedAt"):
                try:
                    finished_at = datetime.fromisoformat(
                        state["FinishedAt"].replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    pass

            return ContainerInfo(
                container_id=container["Id"],
                name=job_id,
                status=state["Status"].lower(),
                exit_code=state.get("ExitCode"),
                started_at=started_at,
                finished_at=finished_at,
            )
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            raise RuntimeError(f"Failed to parse container info: {e}") from e

    async def stream_logs(
        self, container_id: str, follow: bool = True
    ) -> AsyncGenerator[str, None]:
        """
        Stream logs from a container.

        Args:
            container_id: Docker container ID or name
            follow: If True, stream logs continuously. If False, return existing logs.

        Yields:
            Log lines as strings
        """
        args = ["docker", "logs"]
        if follow:
            args.append("--follow")
        args.append(container_id)

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        assert process.stdout is not None

        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                yield line.decode()
        finally:
            # Clean up process if still running
            if process.returncode is None:
                process.terminate()
                await process.wait()

    async def stop_container(self, container_id: str, timeout: int = 10) -> None:
        """
        Stop a running container.

        Args:
            container_id: Docker container ID or name
            timeout: Seconds to wait before killing container

        Raises:
            RuntimeError: If stop operation fails
        """
        process = await asyncio.create_subprocess_exec(
            "docker",
            "stop",
            "--time",
            str(timeout),
            container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        _, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"Failed to stop container: {stderr.decode()}")

    async def remove_container(self, container_id: str, force: bool = False) -> None:
        """
        Remove a container.

        Args:
            container_id: Docker container ID or name
            force: If True, force removal even if running

        Raises:
            RuntimeError: If removal fails
        """
        args = ["docker", "rm"]
        if force:
            args.append("--force")
        args.append(container_id)

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        _, stderr = await process.communicate()

        if process.returncode != 0:
            # Ignore "already removed" errors
            error = stderr.decode()
            if "No such container" not in error:
                raise RuntimeError(f"Failed to remove container: {error}")

    async def list_ci_containers(self) -> list[ContainerInfo]:
        """
        List all CI-related containers (both running and stopped).

        Returns:
            List of ContainerInfo objects for containers matching CI naming pattern
        """
        process = await asyncio.create_subprocess_exec(
            "docker",
            "ps",
            "-a",
            "--filter",
            "ancestor=python:3.12-slim",
            "--format",
            "{{.Names}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"Failed to list containers: {stderr.decode()}")

        # Parse container names and get full info for each
        names = stdout.decode().strip().split("\n")
        containers = []

        for name in names:
            if not name:
                continue
            # Only include containers that match our prefix and have valid job IDs
            # This filters out containers from other test instances and user containers
            job_id = self._extract_job_id(name)
            if job_id:
                info = await self.get_container_info(job_id)
                if info:
                    containers.append(info)

        return containers

    def _extract_job_id(self, container_name: str) -> str | None:
        """
        Extract job ID from container name by stripping prefix.

        Args:
            container_name: Full container name from Docker

        Returns:
            Job ID if container matches our prefix and name pattern, None otherwise
        """
        import re

        # Check if container has our prefix
        if not container_name.startswith(self.container_name_prefix):
            return None

        # Strip prefix to get potential job ID
        potential_job_id = container_name[len(self.container_name_prefix) :]

        # Check if remaining part looks like a UUID (job ID format)
        # UUID format: 8-4-4-4-12 hex characters
        uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        if re.match(uuid_pattern, potential_job_id):
            return potential_job_id

        return None

    async def cleanup_container(self, job_id: str) -> None:
        """
        Clean up a container and its associated resources.

        Args:
            job_id: Job identifier (used as container name)

        This is a best-effort operation that won't raise exceptions.
        """
        try:
            container_name = self._get_container_name(job_id)
            await self.remove_container(container_name, force=True)
        except Exception:
            # Best effort - don't fail if cleanup fails
            pass
