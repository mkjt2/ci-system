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
    CI_PYTHON_BASE_IMAGE: Docker base image for Python (default: python:3.12-slim)
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from typing import Any

from ci_controller.container_manager import ContainerManager
from ci_controller.controller import JobController
from ci_persistence.sqlite_repository import SQLiteJobRepository

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    Returns:
        Parsed arguments
    """
    parser = argparse.ArgumentParser(
        description="CI Controller - Kubernetes-style reconciliation loop for CI jobs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  CI_DB_PATH              Database path (default: ci_jobs.db)
  CI_CONTAINER_PREFIX     Container name prefix for namespace isolation
  CI_RECONCILE_INTERVAL   Seconds between reconciliation loops (default: 2.0)
  CI_PYTHON_BASE_IMAGE    Docker base image for Python (default: python:3.12-slim)

Note: Command-line arguments override environment variables.

Examples:
  # Run with default settings
  ci-controller

  # Use custom database and reconcile interval
  ci-controller --db-path /tmp/ci_jobs.db --interval 5.0

  # Use container prefix for isolation
  ci-controller --container-prefix ci_test_

  # Use custom Python base image
  ci-controller --python-base-image python:3.11-slim

  # Enable debug logging
  ci-controller --log-level DEBUG
        """,
    )

    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to SQLite database file (default: CI_DB_PATH env or ci_jobs.db)",
    )

    parser.add_argument(
        "--container-prefix",
        type=str,
        default=None,
        help="Container name prefix for namespace isolation (default: CI_CONTAINER_PREFIX env or '')",
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Seconds between reconciliation loops (default: CI_RECONCILE_INTERVAL env or 2.0)",
    )

    parser.add_argument(
        "--python-base-image",
        type=str,
        default=None,
        help="Docker base image for Python (default: CI_PYTHON_BASE_IMAGE env or python:3.12-slim)",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: INFO)",
    )

    return parser.parse_args()


def get_database_path(args: argparse.Namespace) -> str:
    """
    Get the database path from CLI args or environment or use default.

    Args:
        args: Parsed command-line arguments

    Returns:
        Path to the SQLite database file
    """
    if args.db_path:
        return args.db_path
    return os.environ.get("CI_DB_PATH", "ci_jobs.db")


def get_container_prefix(args: argparse.Namespace) -> str:
    """
    Get the container name prefix from CLI args or environment.

    Args:
        args: Parsed command-line arguments

    Returns:
        Container name prefix for Docker isolation
    """
    if args.container_prefix is not None:
        return args.container_prefix
    return os.environ.get("CI_CONTAINER_PREFIX", "")


def get_reconcile_interval(args: argparse.Namespace) -> float:
    """
    Get the reconciliation interval from CLI args or environment.

    Args:
        args: Parsed command-line arguments

    Returns:
        Seconds between reconciliation loops
    """
    # Try CLI arg first
    if args.interval is not None:
        if args.interval <= 0:
            logger.warning(f"Invalid interval={args.interval}, using default 2.0")
            return 2.0
        return args.interval

    # Fall back to environment variable
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


def get_python_base_image(args: argparse.Namespace) -> str:
    """
    Get the Python base image from CLI args or environment.

    Args:
        args: Parsed command-line arguments

    Returns:
        Docker base image for Python
    """
    if args.python_base_image is not None:
        return args.python_base_image
    return os.environ.get("CI_PYTHON_BASE_IMAGE", "python:3.12-slim")


async def run_controller(args: argparse.Namespace) -> None:
    """
    Initialize and run the job controller.

    Args:
        args: Parsed command-line arguments

    This function sets up the controller with configured parameters and
    runs it until interrupted by SIGINT or SIGTERM.
    """
    # Get configuration
    db_path = get_database_path(args)
    container_prefix = get_container_prefix(args)
    reconcile_interval = get_reconcile_interval(args)
    python_base_image = get_python_base_image(args)

    logger.info("Starting CI Controller")
    logger.info(f"  Database: {db_path}")
    logger.info(f"  Container prefix: {container_prefix or '(none)'}")
    logger.info(f"  Reconcile interval: {reconcile_interval}s")
    logger.info(f"  Python base image: {python_base_image}")

    # Initialize repository
    repository = SQLiteJobRepository(db_path)
    await repository.initialize()
    logger.info("Database initialized")

    # Initialize container manager
    container_manager = ContainerManager(
        container_name_prefix=container_prefix, python_base_image=python_base_image
    )

    # Initialize and start controller
    controller = JobController(
        repository=repository,
        container_manager=container_manager,
        reconcile_interval=reconcile_interval,
    )

    # Set up signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler(sig: Any, _frame: Any) -> None:
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
    # Parse command-line arguments
    args = parse_args()

    # Configure logging based on args
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        asyncio.run(run_controller(args))
        return 0
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
