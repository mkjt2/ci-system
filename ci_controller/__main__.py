"""
Standalone entrypoint for running the CI controller independently.

This allows the controller to run as a separate process from the server,
enabling distributed architectures where job execution is separated from
the API server.

Usage:
    python -m ci_controller [OPTIONS]
    ci-controller [OPTIONS]  (after pip install)

Environment Variables:
    CI_DB_PATH: Database path (default: ci_jobs.db)
    CI_CONTAINER_PREFIX: Container name prefix for namespace isolation (default: "")
    CI_RECONCILE_INTERVAL: Seconds between reconciliation loops (default: 2.0)
"""

import asyncio
import logging
import os
import signal
import sys
from typing import Any

from ci_controller.container_manager import ContainerManager
from ci_controller.controller import JobController
from ci_persistence.sqlite_repository import SQLiteJobRepository

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_database_path() -> str:
    """
    Get the database path from environment or use default.

    Returns:
        Path to the SQLite database file
    """
    return os.environ.get("CI_DB_PATH", "ci_jobs.db")


def get_container_prefix() -> str:
    """
    Get the container name prefix from environment.

    Returns:
        Container name prefix for Docker isolation
    """
    return os.environ.get("CI_CONTAINER_PREFIX", "")


def get_reconcile_interval() -> float:
    """
    Get the reconciliation interval from environment.

    Returns:
        Seconds between reconciliation loops
    """
    try:
        interval = float(os.environ.get("CI_RECONCILE_INTERVAL", "2.0"))
        if interval <= 0:
            logger.warning(
                f"Invalid CI_RECONCILE_INTERVAL={interval}, using default 2.0"
            )
            return 2.0
        return interval
    except ValueError:
        logger.warning(
            f"Invalid CI_RECONCILE_INTERVAL={os.environ.get('CI_RECONCILE_INTERVAL')}, "
            "using default 2.0"
        )
        return 2.0


async def run_controller() -> None:
    """
    Initialize and run the job controller.

    This function sets up the controller with configured parameters and
    runs it until interrupted by SIGINT or SIGTERM.
    """
    # Get configuration
    db_path = get_database_path()
    container_prefix = get_container_prefix()
    reconcile_interval = get_reconcile_interval()

    logger.info("Starting CI Controller")
    logger.info(f"  Database: {db_path}")
    logger.info(f"  Container prefix: {container_prefix or '(none)'}")
    logger.info(f"  Reconcile interval: {reconcile_interval}s")

    # Initialize repository
    repository = SQLiteJobRepository(db_path)
    await repository.initialize()
    logger.info("Database initialized")

    # Initialize container manager
    container_manager = ContainerManager(container_name_prefix=container_prefix)

    # Initialize and start controller
    controller = JobController(
        repository=repository,
        container_manager=container_manager,
        reconcile_interval=reconcile_interval,
    )

    # Set up signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler(sig: Any, frame: Any) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {sig}, initiating graceful shutdown...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Start the controller
        await controller.start()
        logger.info("Controller started successfully")

        # Wait for shutdown signal
        await shutdown_event.wait()

    except Exception as e:
        logger.error(f"Controller error: {e}", exc_info=True)
        raise
    finally:
        # Graceful shutdown
        logger.info("Stopping controller...")
        await controller.stop()
        logger.info("Closing database connections...")
        await repository.close()
        logger.info("Controller stopped cleanly")


def main() -> int:
    """
    Main entrypoint for the controller.

    Returns:
        Exit code (0 for success, 1 for error)
    """
    try:
        asyncio.run(run_controller())
        return 0
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
