"""
CI Persistence module.

This module contains database implementation for job storage.
Currently supports SQLite, but can be extended to PostgreSQL, MySQL, etc.

The persistence layer depends on ci_common for domain models and interfaces,
and can be used by both ci_server and ci_controller.
"""

from .sqlite_repository import SQLiteJobRepository

__all__ = ["SQLiteJobRepository"]
