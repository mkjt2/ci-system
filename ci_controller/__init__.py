"""
CI Controller module.

This module contains the job controller and container manager that run
independently of the CI server. The controller reconciles desired state
(jobs in database) with actual state (Docker containers).

The controller can run as a separate process from the FastAPI server,
allowing for better separation of concerns and scalability.
"""

from .container_manager import ContainerInfo, ContainerManager
from .controller import JobController

__all__ = ["JobController", "ContainerManager", "ContainerInfo"]
