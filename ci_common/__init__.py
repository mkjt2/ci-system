"""
CI Common module.

This module contains shared domain models and interfaces used across
the CI system components (server, controller, persistence).

The common module has no dependencies on other ci_* modules, making it
a pure domain layer that can be imported by any component.
"""

from .models import Job, JobEvent
from .repository import JobRepository

__all__ = ["Job", "JobEvent", "JobRepository"]
