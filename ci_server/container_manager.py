"""
Backward compatibility wrapper for ContainerManager.

This module provides a thin wrapper to maintain backward compatibility
during the migration to ci_controller package. New code should import
directly from ci_controller.container_manager instead.

TODO: Remove this wrapper after all code is migrated to use ci_controller directly.
"""

from ci_controller.container_manager import ContainerInfo, ContainerManager

__all__ = ["ContainerManager", "ContainerInfo"]
