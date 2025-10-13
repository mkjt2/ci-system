"""
Backward compatibility wrapper for SQLiteJobRepository.

This module provides a thin wrapper to maintain backward compatibility
during the migration to ci_persistence package. New code should import
directly from ci_persistence.sqlite_repository instead.

TODO: Remove this wrapper after all code is migrated to use ci_persistence directly.
"""

from ci_persistence.sqlite_repository import SQLiteJobRepository

__all__ = ["SQLiteJobRepository"]
