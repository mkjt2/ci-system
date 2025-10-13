"""
Unit tests for JobController.

These tests mock the container manager and repository to test
the reconciliation logic in isolation.
"""

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ci_server.container_manager import ContainerInfo
from ci_server.job_controller import JobController
from ci_server.models import Job


class TestJobController:
    """Test suite for JobController class."""

    @pytest.fixture
    def mock_repository(self):
        """Create a mock repository."""
        repo = AsyncMock()
        repo.list_jobs = AsyncMock(return_value=[])
        repo.get_job = AsyncMock(return_value=None)
        repo.create_job = AsyncMock()
        repo.update_job_status = AsyncMock()
        repo.complete_job = AsyncMock()
        repo.add_event = AsyncMock()
        repo.get_events = AsyncMock(return_value=[])
        return repo

    @pytest.fixture
    def mock_container_manager(self):
        """Create a mock container manager."""
        mgr = AsyncMock()
        mgr.list_ci_containers = AsyncMock(return_value=[])
        mgr.get_container_info = AsyncMock(return_value=None)
        mgr.create_container = AsyncMock()
        mgr.start_container = AsyncMock()
        mgr.stop_container = AsyncMock()
        mgr.remove_container = AsyncMock()
        mgr.cleanup_container = AsyncMock()
        mgr.stream_logs = AsyncMock()
        return mgr

    @pytest.fixture
    def controller(self, mock_repository, mock_container_manager):
        """Create a JobController instance with mocked dependencies."""
        return JobController(
            repository=mock_repository,
            container_manager=mock_container_manager,
            reconcile_interval=0.1,  # Short interval for testing
        )

    @pytest.mark.asyncio
    async def test_controller_start_stop(self, controller):
        """Test starting and stopping the controller."""
        await controller.start()
        assert controller._running

        # Let it run briefly
        await asyncio.sleep(0.2)

        await controller.stop()
        assert not controller._running

    @pytest.mark.asyncio
    async def test_reconcile_empty_state(
        self, controller, mock_repository, mock_container_manager
    ):
        """Test reconciliation with no jobs or containers."""
        await controller.reconcile_once()

        # Should list jobs and containers
        mock_repository.list_jobs.assert_called_once()
        mock_container_manager.list_ci_containers.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconcile_queued_job_without_container(
        self, controller, mock_repository, mock_container_manager
    ):
        """Test that queued job without container stays queued (waiting for zip data)."""
        job = Job(id="test-job-id", status="queued")
        mock_repository.list_jobs.return_value = [job]
        mock_container_manager.list_ci_containers.return_value = []

        await controller.reconcile_once()

        # Should not start the job yet (no zip data registered)
        mock_container_manager.start_container.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_queued_job_with_registered_data(
        self, controller, mock_repository, mock_container_manager
    ):
        """Test that queued job with zip file path gets started."""
        # Create temp zip file
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            zip_file_path = f.name

        try:
            job = Job(id="test-job-id", status="queued", zip_file_path=zip_file_path)
            mock_repository.list_jobs.return_value = [job]
            mock_repository.get_job.return_value = job
            mock_container_manager.list_ci_containers.return_value = []
            mock_container_manager.create_container.return_value = (
                "container-123",
                Path("/tmp/test"),
            )

            await controller.reconcile_once()

            # Should start the container
            mock_container_manager.create_container.assert_called_once()
            mock_container_manager.start_container.assert_called_once_with(
                "container-123"
            )
            mock_repository.update_job_status.assert_called_once()
        finally:
            # Clean up temp file
            import os

            if os.path.exists(zip_file_path):
                os.unlink(zip_file_path)

    @pytest.mark.asyncio
    async def test_reconcile_running_job_with_exited_container(
        self, controller, mock_repository, mock_container_manager
    ):
        """Test that running job with exited container gets finalized."""
        job = Job(id="test-job-id", status="running", container_id="container-123")
        mock_repository.list_jobs.return_value = [job]

        container = ContainerInfo(
            container_id="container-123",
            name="test-job-id",
            status="exited",
            exit_code=0,
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        mock_container_manager.list_ci_containers.return_value = [container]

        await controller.reconcile_once()

        # Should finalize the job (logs NOT stored in DB - streamed on-demand)
        mock_repository.complete_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconcile_running_job_without_container(
        self, controller, mock_repository, mock_container_manager
    ):
        """Test that running job without container is marked as failed."""
        job = Job(id="test-job-id", status="running")
        mock_repository.list_jobs.return_value = [job]
        mock_container_manager.list_ci_containers.return_value = []

        await controller.reconcile_once()

        # Should mark job as failed
        mock_repository.update_job_status.assert_called()
        assert any(
            call[0][1] == "failed"
            for call in mock_repository.update_job_status.call_args_list
        )

    @pytest.mark.asyncio
    async def test_reconcile_completed_job_with_container(
        self, controller, mock_repository, mock_container_manager
    ):
        """Test that completed job with lingering container gets cleaned up."""
        job = Job(id="test-job-id", status="completed")
        mock_repository.list_jobs.return_value = [job]

        container = ContainerInfo(
            container_id="container-123",
            name="test-job-id",
            status="exited",
            exit_code=0,
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        mock_container_manager.list_ci_containers.return_value = [container]

        await controller.reconcile_once()

        # Should NOT cleanup the container (kept for log viewing)
        mock_container_manager.cleanup_container.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_orphaned_containers(
        self, controller, mock_repository, mock_container_manager
    ):
        """Test that containers without matching jobs are cleaned up."""
        # No jobs in database
        mock_repository.list_jobs.return_value = []

        # But there's an orphaned container
        orphaned_container = ContainerInfo(
            container_id="orphan-123",
            name="orphan-job-id",
            status="exited",
            exit_code=0,
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        mock_container_manager.list_ci_containers.return_value = [orphaned_container]

        await controller.reconcile_once()

        # Should cleanup the orphaned container
        mock_container_manager.cleanup_container.assert_called_once_with(
            "orphan-job-id"
        )

    @pytest.mark.asyncio
    async def test_register_job(self, controller):
        """Test registering a job with the controller."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            await controller.register_job("test-job-id", temp_path)

            assert "test-job-id" in controller.active_jobs
            assert controller.active_jobs["test-job-id"] == temp_path
