"""
End-to-end tests for the CI admin CLI.

Tests the admin CLI commands for managing users and API keys.
These tests are written TDD-style before implementing the admin CLI.
"""

import json
import os
import re
import subprocess
import tempfile

import pytest


@pytest.fixture
def test_db_path():
    """Create a temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="ci_admin_test_")
    os.close(fd)

    # Initialize the database
    import asyncio

    from ci_persistence.sqlite_repository import SQLiteJobRepository

    async def init_db():
        repo = SQLiteJobRepository(path)
        await repo.initialize()
        await repo.close()

    asyncio.run(init_db())

    yield path

    # Clean up test database after test
    if os.path.exists(path):
        os.unlink(path)


def run_admin_command(*args, env=None):
    """Helper to run ci-admin commands."""
    cmd_env = os.environ.copy()
    if env:
        cmd_env.update(env)

    return subprocess.run(
        ["ci-admin", *args],
        capture_output=True,
        text=True,
        env=cmd_env,
    )


class TestUserManagement:
    """Test suite for user CRUD operations via admin CLI."""

    def test_create_user(self, test_db_path):
        """Test creating a new user."""
        result = run_admin_command(
            "user", "create",
            "--name", "Alice Smith",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 0
        output = result.stdout

        # Should show success message with user ID
        assert "created successfully" in output.lower()
        assert "alice@example.com" in output

        # Should show user ID (UUID format)
        assert re.search(r"[a-f0-9\-]{36}", output) is not None

    def test_create_user_duplicate_email(self, test_db_path):
        """Test that creating a user with duplicate email fails."""
        # Create first user
        result1 = run_admin_command(
            "user", "create",
            "--name", "Alice",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )
        assert result1.returncode == 0

        # Try to create second user with same email
        result2 = run_admin_command(
            "user", "create",
            "--name", "Alice Clone",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )

        assert result2.returncode == 1
        assert "already exists" in result2.stderr.lower() or "duplicate" in result2.stderr.lower()

    def test_list_users(self, test_db_path):
        """Test listing all users."""
        # Create some users
        run_admin_command(
            "user", "create",
            "--name", "Alice",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )
        run_admin_command(
            "user", "create",
            "--name", "Bob",
            "--email", "bob@example.com",
            env={"CI_DB_PATH": test_db_path}
        )

        # List users
        result = run_admin_command("user", "list", env={"CI_DB_PATH": test_db_path})

        assert result.returncode == 0
        output = result.stdout

        # Should show both users
        assert "alice@example.com" in output
        assert "bob@example.com" in output
        assert "Alice" in output
        assert "Bob" in output

    def test_list_users_json(self, test_db_path):
        """Test listing users in JSON format."""
        # Create a user
        run_admin_command(
            "user", "create",
            "--name", "Alice",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )

        # List users in JSON format
        result = run_admin_command(
            "user", "list", "--json",
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 0

        # Parse JSON output
        users = json.loads(result.stdout)
        assert isinstance(users, list)
        assert len(users) == 1

        user = users[0]
        assert user["email"] == "alice@example.com"
        assert user["name"] == "Alice"
        assert user["is_active"] is True
        assert "id" in user
        assert "created_at" in user

    def test_get_user_by_id(self, test_db_path):
        """Test getting a user by ID."""
        # Create a user
        create_result = run_admin_command(
            "user", "create",
            "--name", "Alice",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )

        # Extract user ID from output
        match = re.search(r"([a-f0-9\-]{36})", create_result.stdout)
        assert match is not None
        user_id = match.group(1)

        # Get user by ID
        result = run_admin_command(
            "user", "get", user_id,
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 0
        output = result.stdout

        assert "alice@example.com" in output
        assert "Alice" in output
        assert user_id in output

    def test_get_user_by_email(self, test_db_path):
        """Test getting a user by email."""
        # Create a user
        run_admin_command(
            "user", "create",
            "--name", "Alice",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )

        # Get user by email
        result = run_admin_command(
            "user", "get", "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 0
        output = result.stdout

        assert "alice@example.com" in output
        assert "Alice" in output

    def test_deactivate_user(self, test_db_path):
        """Test deactivating a user."""
        # Create a user
        create_result = run_admin_command(
            "user", "create",
            "--name", "Alice",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )

        match = re.search(r"([a-f0-9\-]{36})", create_result.stdout)
        assert match is not None
        user_id = match.group(1)

        # Deactivate user
        result = run_admin_command(
            "user", "deactivate", user_id,
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 0
        assert "deactivated" in result.stdout.lower()

        # Verify user is inactive
        get_result = run_admin_command(
            "user", "get", user_id,
            env={"CI_DB_PATH": test_db_path}
        )
        assert "inactive" in get_result.stdout.lower() or "false" in get_result.stdout.lower()

    def test_activate_user(self, test_db_path):
        """Test activating a deactivated user."""
        # Create and deactivate a user
        create_result = run_admin_command(
            "user", "create",
            "--name", "Alice",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )

        match = re.search(r"([a-f0-9\-]{36})", create_result.stdout)
        assert match is not None
        user_id = match.group(1)

        run_admin_command(
            "user", "deactivate", user_id,
            env={"CI_DB_PATH": test_db_path}
        )

        # Activate user
        result = run_admin_command(
            "user", "activate", user_id,
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 0
        assert "activated" in result.stdout.lower()

        # Verify user is active
        get_result = run_admin_command(
            "user", "get", user_id,
            env={"CI_DB_PATH": test_db_path}
        )
        assert "active" in get_result.stdout.lower() or "true" in get_result.stdout.lower()


class TestAPIKeyManagement:
    """Test suite for API key CRUD operations via admin CLI."""

    def test_create_api_key(self, test_db_path):
        """Test creating an API key for a user."""
        # Create a user first
        user_result = run_admin_command(
            "user", "create",
            "--name", "Alice",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )
        match = re.search(r"([a-f0-9\-]{36})", user_result.stdout)
        assert match is not None
        user_id = match.group(1)

        # Create API key
        result = run_admin_command(
            "key", "create",
            "--user-id", user_id,
            "--name", "Alice's laptop key",
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 0
        output = result.stdout

        # Should show the API key (starts with ci_)
        assert "ci_" in output

        # Should warn that this is the only time the key is shown
        assert "only time" in output.lower() or "save" in output.lower()

        # Extract the API key
        match = re.search(r"(ci_[A-Za-z0-9_-]{40,})", output)
        assert match is not None, "API key not found in output"

    def test_create_api_key_by_email(self, test_db_path):
        """Test creating an API key using user email instead of ID."""
        # Create a user
        run_admin_command(
            "user", "create",
            "--name", "Alice",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )

        # Create API key using email
        result = run_admin_command(
            "key", "create",
            "--email", "alice@example.com",
            "--name", "Alice's key",
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 0
        assert "ci_" in result.stdout

    def test_list_api_keys_for_user(self, test_db_path):
        """Test listing all API keys for a user."""
        # Create a user
        user_result = run_admin_command(
            "user", "create",
            "--name", "Alice",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )
        match = re.search(r"([a-f0-9\-]{36})", user_result.stdout)
        assert match is not None
        user_id = match.group(1)

        # Create multiple API keys
        run_admin_command(
            "key", "create",
            "--user-id", user_id,
            "--name", "Laptop key",
            env={"CI_DB_PATH": test_db_path}
        )
        run_admin_command(
            "key", "create",
            "--user-id", user_id,
            "--name", "Server key",
            env={"CI_DB_PATH": test_db_path}
        )

        # List keys
        result = run_admin_command(
            "key", "list",
            "--user-id", user_id,
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 0
        output = result.stdout

        # Should show both key names
        assert "Laptop key" in output
        assert "Server key" in output

        # Should NOT show the actual API keys (security)
        assert "ci_" not in output

    def test_list_api_keys_json(self, test_db_path):
        """Test listing API keys in JSON format."""
        # Create a user and API key
        user_result = run_admin_command(
            "user", "create",
            "--name", "Alice",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )
        match = re.search(r"([a-f0-9\-]{36})", user_result.stdout)
        assert match is not None
        user_id = match.group(1)

        run_admin_command(
            "key", "create",
            "--user-id", user_id,
            "--name", "Test key",
            env={"CI_DB_PATH": test_db_path}
        )

        # List keys in JSON format
        result = run_admin_command(
            "key", "list",
            "--user-id", user_id,
            "--json",
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 0

        # Parse JSON output
        keys = json.loads(result.stdout)
        assert isinstance(keys, list)
        assert len(keys) == 1

        key = keys[0]
        assert key["name"] == "Test key"
        assert key["is_active"] is True
        assert "id" in key
        assert "created_at" in key
        # Should NOT include the actual key hash (security)
        assert "key_hash" not in key

    def test_revoke_api_key(self, test_db_path):
        """Test revoking an API key."""
        # Create a user and API key
        user_result = run_admin_command(
            "user", "create",
            "--name", "Alice",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )
        match = re.search(r"([a-f0-9\-]{36})", user_result.stdout)
        assert match is not None
        user_id = match.group(1)

        run_admin_command(
            "key", "create",
            "--user-id", user_id,
            "--name", "Test key",
            env={"CI_DB_PATH": test_db_path}
        )

        # List keys to get the key ID
        list_result = run_admin_command(
            "key", "list",
            "--user-id", user_id,
            "--json",
            env={"CI_DB_PATH": test_db_path}
        )
        keys = json.loads(list_result.stdout)
        key_id = keys[0]["id"]

        # Revoke the key
        result = run_admin_command(
            "key", "revoke", key_id,
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 0
        assert "revoked" in result.stdout.lower()

        # Verify key is inactive
        list_result = run_admin_command(
            "key", "list",
            "--user-id", user_id,
            "--json",
            env={"CI_DB_PATH": test_db_path}
        )
        keys = json.loads(list_result.stdout)
        assert keys[0]["is_active"] is False

    def test_list_all_api_keys(self, test_db_path):
        """Test listing all API keys across all users."""
        # Create two users with keys
        user1_result = run_admin_command(
            "user", "create",
            "--name", "Alice",
            "--email", "alice@example.com",
            env={"CI_DB_PATH": test_db_path}
        )
        match1 = re.search(r"([a-f0-9\-]{36})", user1_result.stdout)
        assert match1 is not None
        user1_id = match1.group(1)

        user2_result = run_admin_command(
            "user", "create",
            "--name", "Bob",
            "--email", "bob@example.com",
            env={"CI_DB_PATH": test_db_path}
        )
        match2 = re.search(r"([a-f0-9\-]{36})", user2_result.stdout)
        assert match2 is not None
        user2_id = match2.group(1)

        run_admin_command(
            "key", "create",
            "--user-id", user1_id,
            "--name", "Alice key",
            env={"CI_DB_PATH": test_db_path}
        )
        run_admin_command(
            "key", "create",
            "--user-id", user2_id,
            "--name", "Bob key",
            env={"CI_DB_PATH": test_db_path}
        )

        # List all keys (no user filter)
        result = run_admin_command(
            "key", "list",
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 0
        output = result.stdout

        # Should show both keys
        assert "Alice key" in output
        assert "Bob key" in output
        # Should also show user emails/names
        assert "alice@example.com" in output or "Alice" in output
        assert "bob@example.com" in output or "Bob" in output


class TestErrorHandling:
    """Test suite for error handling in admin CLI."""

    def test_create_key_for_nonexistent_user(self, test_db_path):
        """Test creating an API key for a user that doesn't exist."""
        fake_user_id = "00000000-0000-0000-0000-000000000000"

        result = run_admin_command(
            "key", "create",
            "--user-id", fake_user_id,
            "--name", "Test key",
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 1
        assert "not found" in result.stderr.lower() or "does not exist" in result.stderr.lower()

    def test_get_nonexistent_user(self, test_db_path):
        """Test getting a user that doesn't exist."""
        fake_user_id = "00000000-0000-0000-0000-000000000000"

        result = run_admin_command(
            "user", "get", fake_user_id,
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 1
        assert "not found" in result.stderr.lower()

    def test_revoke_nonexistent_key(self, test_db_path):
        """Test revoking an API key that doesn't exist."""
        fake_key_id = "00000000-0000-0000-0000-000000000000"

        result = run_admin_command(
            "key", "revoke", fake_key_id,
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 1
        assert "not found" in result.stderr.lower()

    def test_invalid_email_format(self, test_db_path):
        """Test creating a user with invalid email format."""
        result = run_admin_command(
            "user", "create",
            "--name", "Alice",
            "--email", "not-an-email",
            env={"CI_DB_PATH": test_db_path}
        )

        assert result.returncode == 1
        assert "email" in result.stderr.lower() and "invalid" in result.stderr.lower()
