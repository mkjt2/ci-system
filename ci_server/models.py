"""
Backward compatibility wrapper for models.

This module provides a thin wrapper to maintain backward compatibility
during the migration to ci_common package. New code should import
directly from ci_common.models instead.

TODO: Remove this wrapper after all code is migrated to use ci_common directly.
"""

from ci_common.models import Job, JobEvent

__all__ = ["Job", "JobEvent"]
