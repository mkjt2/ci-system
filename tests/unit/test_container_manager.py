"""
Unit tests for ContainerManager.

These tests use pytest with asyncio support to test container management operations.
"""

import pytest

from ci_controller.container_manager import ContainerManager


class TestContainerManager:
    """Test suite for ContainerManager class."""

    @pytest.fixture
    def container_manager(self):
        """Create a ContainerManager instance for testing."""
        return ContainerManager()

    def test_extract_job_id_valid_uuid(self, container_manager):
        """Test that valid UUIDs are extracted as job IDs."""
        valid_uuid = "550e8400-e29b-41d4-a716-446655440000"
        # Without prefix, should extract the UUID directly
        assert container_manager._extract_job_id(valid_uuid) == valid_uuid

    def test_extract_job_id_with_prefix(self, container_manager):
        """Test that job IDs are extracted from prefixed container names."""
        # Create a container manager with a prefix
        mgr_with_prefix = ContainerManager(container_name_prefix="test_")
        valid_uuid = "550e8400-e29b-41d4-a716-446655440000"

        # Should extract job ID from prefixed name
        assert mgr_with_prefix._extract_job_id(f"test_{valid_uuid}") == valid_uuid

        # Should reject name without matching prefix
        assert mgr_with_prefix._extract_job_id(valid_uuid) is None
        assert mgr_with_prefix._extract_job_id(f"other_{valid_uuid}") is None

    def test_extract_job_id_invalid(self, container_manager):
        """Test that non-UUID strings are rejected."""
        invalid_names = [
            "not-a-uuid",
            "my_container",
            "test",
            "550e8400",  # Incomplete UUID
            "",
            "550e8400-e29b-41d4-a716-44665544000g",  # Invalid character
        ]
        for name in invalid_names:
            assert container_manager._extract_job_id(name) is None, (
                f"Should reject: {name}"
            )

    @pytest.mark.asyncio
    async def test_get_container_info_nonexistent(self, container_manager):
        """Test getting info for a non-existent container."""
        job_id = "00000000-0000-0000-0000-000000000000"
        info = await container_manager.get_container_info(job_id)
        assert info is None

    @pytest.mark.asyncio
    async def test_list_ci_containers(self, container_manager):
        """Test listing CI containers."""
        # This should not raise an exception
        containers = await container_manager.list_ci_containers()
        assert isinstance(containers, list)
        # All returned containers should have valid job IDs as names
        for container in containers:
            # Verify the name can be extracted as a valid job ID
            assert container_manager._extract_job_id(container.name) is not None
