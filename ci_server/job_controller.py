"""
Backward compatibility wrapper for JobController.

This module provides a thin wrapper to maintain backward compatibility
during the migration to ci_controller package. New code should import
directly from ci_controller.controller instead.

TODO: Remove this wrapper after all code is migrated to use ci_controller directly.
"""

from ci_controller.controller import JobController

__all__ = ["JobController"]
