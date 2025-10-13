"""
Job controller with reconciliation loop for managing Docker containers.

This module implements a Kubernetes-style controller pattern that continuously
reconciles the desired state (jobs in DB) with actual state (Docker containers),
taking corrective actions when they diverge.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from ci_server.models import Job
from ci_server.repository import JobRepository

from .container_manager import ContainerInfo, ContainerManager

# Configure logging
logger = logging.getLogger(__name__)


class JobController:
    """
    Controller that reconciles job state with container state.

    This controller runs a continuous loop that:
    1. Fetches desired state from the database (jobs)
    2. Fetches actual state from Docker (containers)
    3. Reconciles differences and takes corrective actions
    4. Handles crash recovery and orphaned resources
    """

    def __init__(
        self,
        repository: JobRepository,
        container_manager: ContainerManager | None = None,
        reconcile_interval: float = 2.0,
    ):
        """
        Initialize the job controller.

        Args:
            repository: Job repository for persisting state
            container_manager: Container manager for Docker operations
            reconcile_interval: Seconds between reconciliation loops
        """
        self.repository = repository
        self.container_manager = container_manager or ContainerManager()
        self.reconcile_interval = reconcile_interval

        # Track active jobs and their resources
        self.active_jobs: dict[str, Path] = {}  # job_id -> temp_dir_path
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the controller reconciliation loop."""
        if self._running:
            logger.warning("Controller already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Job controller started")

        # Perform initial reconciliation on startup (crash recovery)
        await self.reconcile_once()

    async def stop(self) -> None:
        """Stop the controller and clean up resources."""
        if not self._running:
            return

        logger.info("Stopping job controller...")
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Clean up temporary directories
        import shutil

        for job_id, temp_dir in self.active_jobs.items():
            logger.info(f"Cleaning up temp directory for job {job_id}")
            shutil.rmtree(temp_dir, ignore_errors=True)

        self.active_jobs.clear()
        logger.info("Job controller stopped")

    async def _run_loop(self) -> None:
        """Main reconciliation loop."""
        while self._running:
            try:
                await self.reconcile_once()
                await asyncio.sleep(self.reconcile_interval)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in reconciliation loop: {e}", exc_info=True)
                await asyncio.sleep(self.reconcile_interval)

    async def reconcile_once(self) -> None:
        """
        Perform one reconciliation cycle.

        This is the core reconciliation logic that compares desired state (DB)
        with actual state (Docker) and takes corrective actions.
        """
        try:
            # 1. Get desired state from database
            jobs = await self.repository.list_jobs()
            logger.debug(f"Reconciliation: Found {len(jobs)} jobs in database")

            # 2. Get actual state from Docker
            containers = await self.container_manager.list_ci_containers()
            container_map = {c.name: c for c in containers}
            logger.debug(
                f"Reconciliation: Found {len(containers)} containers in Docker"
            )

            # 3. Reconcile each job
            for job in jobs:
                try:
                    logger.debug(
                        f"Reconciling job {job.id} (status={job.status}, container_id={job.container_id}, zip_file_path={job.zip_file_path})"
                    )
                    await self._reconcile_job(job, container_map.get(job.id))
                except Exception as e:
                    logger.error(f"Error reconciling job {job.id}: {e}", exc_info=True)

            # 4. Clean up orphaned containers (containers without jobs)
            await self._cleanup_orphaned_containers(containers, jobs)

        except Exception as e:
            logger.error(f"Error in reconciliation cycle: {e}", exc_info=True)

    async def _reconcile_job(self, job: Job, container: ContainerInfo | None) -> None:
        """
        Reconcile a single job's state with its container state.

        Args:
            job: Job from database (desired state)
            container: Container from Docker (actual state), or None
        """
        job_id = job.id

        # Handle jobs in "queued" state
        if job.status == "queued":
            if container is None:
                # No container exists yet - start the job if we have zip file path
                if job.zip_file_path:
                    await self._start_job(job_id)
            else:
                # Container exists but shouldn't - clean it up
                logger.warning(
                    f"Job {job_id} is queued but container exists, cleaning up"
                )
                await self.container_manager.cleanup_container(job_id)

        # Handle jobs in "running" state
        elif job.status == "running":
            if container is None:
                # Container disappeared! Mark job as failed
                logger.error(f"Container for running job {job_id} disappeared")
                await self._mark_job_failed(job_id, "Container lost during execution")
            elif container.status == "exited":
                # Container finished, collect results
                logger.info(f"Job {job_id} container exited, collecting results")
                await self._finalize_job(job_id, container)
            elif container.status == "running":
                # Normal case - stream logs to database
                await self._stream_logs_to_db(job_id, container)
            elif container.status in ["dead", "removing"]:
                # Container in bad state
                logger.error(f"Job {job_id} container in bad state: {container.status}")
                await self._mark_job_failed(
                    job_id, f"Container entered bad state: {container.status}"
                )

        # Handle jobs in "completed", "failed", or "cancelled" state
        elif (
            job.status in ["completed", "failed", "cancelled"]
            and job_id in self.active_jobs
        ):
            # Keep container around for log viewing - don't clean up immediately
            # Containers will be cleaned up by explicit user action or periodic cleanup

            # Clean up temp directory if we still have it
            temp_dir = self.active_jobs.pop(job_id)
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)

    async def _start_job(self, job_id: str) -> None:
        """
        Start a queued job by creating and starting its container.

        Args:
            job_id: Job identifier
        """
        try:
            logger.info(f"_start_job called for job {job_id}")

            # Get the full job to access zip_file_path
            job = await self.repository.get_job(job_id)
            if job is None:
                logger.error(f"Job {job_id} not found in database")
                return

            # Validate zip_file_path is set and exists
            if not job.zip_file_path:
                logger.error(f"Job {job_id} has no zip file path")
                await self._mark_job_failed(job_id, "No zip file path available")
                return

            zip_path = Path(job.zip_file_path)
            if not zip_path.exists():
                logger.error(
                    f"Job {job_id} zip file does not exist: {job.zip_file_path}"
                )
                await self._mark_job_failed(
                    job_id, f"Zip file not found: {job.zip_file_path}"
                )
                return

            if not zip_path.is_file():
                logger.error(
                    f"Job {job_id} zip path is not a file: {job.zip_file_path}"
                )
                await self._mark_job_failed(
                    job_id, f"Zip path is not a file: {job.zip_file_path}"
                )
                return

            logger.info(f"Job {job_id} has valid zip_file_path: {job.zip_file_path}")

            # If container already exists (from a previous attempt), use it
            if job.container_id:
                container_info = await self.container_manager.get_container_info(job_id)
                if container_info:
                    logger.info(f"Reusing existing container for job {job_id}")
                    await self.container_manager.start_container(job.container_id)
                    await self.repository.update_job_status(
                        job_id, "running", start_time=datetime.utcnow()
                    )
                    return

            # Create container from zip file
            logger.info(f"Creating container for job {job_id} from {job.zip_file_path}")
            container_id, temp_dir = await self.container_manager.create_container(
                job_id, job.zip_file_path
            )

            # Register the temp directory for lifecycle management
            self.active_jobs[job_id] = temp_dir
            logger.info(f"Registered temp directory for job {job_id}: {temp_dir}")

            # Start the container
            logger.info(f"Starting container {container_id} for job {job_id}")
            await self.container_manager.start_container(container_id)

            # Update job status to running
            await self.repository.update_job_status(
                job_id,
                "running",
                start_time=datetime.utcnow(),
                container_id=container_id,
            )

            logger.info(
                f"Job {job_id} started successfully with container {container_id}"
            )

        except Exception as e:
            logger.error(f"Failed to start job {job_id}: {e}", exc_info=True)
            await self._mark_job_failed(job_id, f"Failed to start container: {e}")

    async def _finalize_job(self, job_id: str, container: ContainerInfo) -> None:
        """
        Finalize a job after its container has exited.

        Args:
            job_id: Job identifier
            container: Container information

        Note: Logs are NOT stored in the database. They are streamed directly
        from Docker on-demand by SSE clients.
        """
        try:
            # Determine success based on exit code
            success = container.exit_code == 0

            # Mark job as completed (no log events stored)
            await self.repository.complete_job(
                job_id, success=success, end_time=datetime.utcnow()
            )

            logger.info(f"Job {job_id} finalized with success={success}")

        except Exception as e:
            logger.error(f"Error finalizing job {job_id}: {e}", exc_info=True)
            await self._mark_job_failed(job_id, f"Error during finalization: {e}")

    async def _stream_logs_to_db(self, job_id: str, container: ContainerInfo) -> None:
        """
        Stream new logs from a running container to the database.

        Args:
            job_id: Job identifier
            container: Container information

        Note: This is a lightweight check. Full streaming is handled by
        the existing stream_job_events function for SSE clients.
        """
        # In the current implementation, logs are streamed directly to clients
        # via SSE. The database captures them through the old process_job_async.
        # This is a placeholder for future enhancements where we might want
        # to periodically checkpoint logs even without active clients.
        pass

    async def _mark_job_failed(self, job_id: str, reason: str) -> None:
        """
        Mark a job as failed with a reason.

        Args:
            job_id: Job identifier
            reason: Failure reason

        Note: Error message is logged but NOT stored in database.
        Clients will see the error in Docker logs if available.
        """
        try:
            # Log the error for debugging
            logger.error(f"Job {job_id} failed: {reason}")

            # Mark job as failed in database
            await self.repository.update_job_status(job_id, "failed")
            await self.repository.complete_job(
                job_id, success=False, end_time=datetime.utcnow()
            )

            logger.info(f"Job {job_id} marked as failed: {reason}")

        except Exception as e:
            logger.error(f"Error marking job {job_id} as failed: {e}", exc_info=True)

    async def _cleanup_orphaned_containers(
        self, containers: list[ContainerInfo], jobs: list[Job]
    ) -> None:
        """
        Clean up containers that don't have a corresponding job in the database.

        Args:
            containers: All CI containers from Docker
            jobs: All jobs from database
        """
        job_ids = {job.id for job in jobs}

        for container in containers:
            if container.name not in job_ids:
                logger.warning(
                    f"Found orphaned container {container.container_id} "
                    f"(name: {container.name}), cleaning up"
                )
                await self.container_manager.cleanup_container(container.name)

    async def register_job(self, job_id: str, temp_dir: Path) -> None:
        """
        Register a new job with the controller.

        This is called by the submit endpoint after creating the container.

        Args:
            job_id: Job identifier
            temp_dir: Temporary directory with project files
        """
        self.active_jobs[job_id] = temp_dir
        logger.info(f"Registered job {job_id} with controller")
