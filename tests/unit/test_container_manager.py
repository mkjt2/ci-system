"""
Unit tests for ContainerManager.

These tests use pytest with asyncio support to test container management operations.
"""

import pytest

from ci_server.container_manager import ContainerManager


class TestContainerManager:
    """Test suite for ContainerManager class."""

    @pytest.fixture
    def container_manager(self):
        """Create a ContainerManager instance for testing."""
        return ContainerManager()

    def test_is_job_id_valid_uuid(self, container_manager):
        """Test that valid UUIDs are recognized as job IDs."""
        valid_uuid = "550e8400-e29b-41d4-a716-446655440000"
        assert container_manager._is_job_id(valid_uuid)

    def test_is_job_id_invalid(self, container_manager):
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
            assert not container_manager._is_job_id(name), f"Should reject: {name}"

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
        # All returned containers should have UUID names
        for container in containers:
            assert container_manager._is_job_id(container.name)
