"""
Backward compatibility wrapper for repository interface.

This module provides a thin wrapper to maintain backward compatibility
during the migration to ci_common package. New code should import
directly from ci_common.repository instead.

TODO: Remove this wrapper after all code is migrated to use ci_common directly.
"""

from ci_common.repository import JobRepository

__all__ = ["JobRepository"]
