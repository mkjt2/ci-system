"""
Admin CLI for managing CI system users and API keys.

Provides commands for CRUD operations on users and API keys.
"""

import asyncio
import json
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import click

from ci_common.models import APIKey, User
from ci_persistence.sqlite_repository import SQLiteJobRepository
from ci_server.auth import generate_api_key, hash_api_key


def get_db_path() -> str:
    """Get the database path from environment variable or default."""
    return os.environ.get("CI_DB_PATH", str(Path.home() / ".ci" / "jobs.db"))


def get_repository() -> SQLiteJobRepository:
    """Get the repository instance."""
    return SQLiteJobRepository(get_db_path())


def validate_email(email: str) -> bool:
    """Validate email format."""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email) is not None


def run_async(coro):
    """Helper to run async functions in CLI commands."""
    return asyncio.run(coro)


@click.group()
def cli():
    """CI Admin - Manage users and API keys for the CI system."""
    pass


@cli.group()
def user():
    """Manage users."""
    pass


@cli.group()
def key():
    """Manage API keys."""
    pass


# ============================================================================
# User Commands
# ============================================================================


@user.command("create")
@click.option("--name", required=True, help="User's display name")
@click.option("--email", required=True, help="User's email address")
def user_create(name: str, email: str):
    """Create a new user."""
    # Validate email format
    if not validate_email(email):
        click.echo(f"Error: Invalid email format: {email}", err=True)
        sys.exit(1)

    async def create():
        repo = get_repository()
        await repo.initialize()

        try:
            # Check if email already exists
            existing_user = await repo.get_user_by_email(email)
            if existing_user:
                click.echo(f"Error: User with email {email} already exists", err=True)
                sys.exit(1)

            # Create user
            user_obj = User(
                id=str(uuid.uuid4()),
                name=name,
                email=email,
                created_at=datetime.now(UTC),
                is_active=True,
            )

            await repo.create_user(user_obj)

            click.echo("✓ User created successfully")
            click.echo(f"  ID:    {user_obj.id}")
            click.echo(f"  Name:  {user_obj.name}")
            click.echo(f"  Email: {user_obj.email}")

        finally:
            await repo.close()

    run_async(create())


@user.command("list")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def user_list(json_output: bool):
    """List all users."""

    async def list_users():
        repo = get_repository()
        await repo.initialize()

        try:
            users = await repo.list_users()

            if json_output:
                # JSON output
                users_data = [
                    {
                        "id": u.id,
                        "name": u.name,
                        "email": u.email,
                        "created_at": u.created_at.isoformat(),
                        "is_active": u.is_active,
                    }
                    for u in users
                ]
                click.echo(json.dumps(users_data, indent=2))
            else:
                # Table output
                if not users:
                    click.echo("No users found.")
                    return

                click.echo(f"\n{'ID':<38} {'Name':<20} {'Email':<30} {'Status':<10}")
                click.echo("-" * 100)
                for u in users:
                    status = "Active" if u.is_active else "Inactive"
                    click.echo(f"{u.id:<38} {u.name:<20} {u.email:<30} {status:<10}")
                click.echo()

        finally:
            await repo.close()

    run_async(list_users())


@user.command("get")
@click.argument("user_id", required=False)
@click.option("--email", help="Get user by email instead of ID")
def user_get(user_id: str | None, email: str | None):
    """Get user details by ID or email."""
    if not user_id and not email:
        click.echo("Error: Must provide either USER_ID or --email", err=True)
        sys.exit(1)

    if user_id and email:
        click.echo("Error: Provide either USER_ID or --email, not both", err=True)
        sys.exit(1)

    async def get_user():
        repo = get_repository()
        await repo.initialize()

        try:
            # Get user by ID or email
            if email:
                user_obj = await repo.get_user_by_email(email)
            else:
                assert user_id is not None  # Already validated above
                user_obj = await repo.get_user(user_id)

            if not user_obj:
                identifier = email if email else user_id
                click.echo(f"Error: User not found: {identifier}", err=True)
                sys.exit(1)

            # Display user details
            click.echo("\nUser Details:")
            click.echo(f"  ID:         {user_obj.id}")
            click.echo(f"  Name:       {user_obj.name}")
            click.echo(f"  Email:      {user_obj.email}")
            click.echo(f"  Created:    {user_obj.created_at.isoformat()}")
            click.echo(
                f"  Status:     {'Active' if user_obj.is_active else 'Inactive'}"
            )
            click.echo()

        finally:
            await repo.close()

    run_async(get_user())


@user.command("deactivate")
@click.argument("user_id")
def user_deactivate(user_id: str):
    """Deactivate a user."""

    async def deactivate():
        repo = get_repository()
        await repo.initialize()

        try:
            # Check if user exists
            user_obj = await repo.get_user(user_id)
            if not user_obj:
                click.echo(f"Error: User not found: {user_id}", err=True)
                sys.exit(1)

            # Deactivate user
            await repo.update_user_active_status(user_id, False)

            click.echo(f"✓ User deactivated: {user_obj.email}")

        finally:
            await repo.close()

    run_async(deactivate())


@user.command("activate")
@click.argument("user_id")
def user_activate(user_id: str):
    """Activate a user."""

    async def activate():
        repo = get_repository()
        await repo.initialize()

        try:
            # Check if user exists
            user_obj = await repo.get_user(user_id)
            if not user_obj:
                click.echo(f"Error: User not found: {user_id}", err=True)
                sys.exit(1)

            # Activate user
            await repo.update_user_active_status(user_id, True)

            click.echo(f"✓ User activated: {user_obj.email}")

        finally:
            await repo.close()

    run_async(activate())


# ============================================================================
# API Key Commands
# ============================================================================


@key.command("create")
@click.option("--user-id", help="User ID (UUID)")
@click.option("--email", help="User email (alternative to --user-id)")
@click.option("--name", required=True, help="Descriptive name for this API key")
def key_create(user_id: str | None, email: str | None, name: str):
    """Create a new API key for a user."""
    if not user_id and not email:
        click.echo("Error: Must provide either --user-id or --email", err=True)
        sys.exit(1)

    if user_id and email:
        click.echo("Error: Provide either --user-id or --email, not both", err=True)
        sys.exit(1)

    async def create():
        repo = get_repository()
        await repo.initialize()

        try:
            # Get user by ID or email
            if email:
                user_obj = await repo.get_user_by_email(email)
                if not user_obj:
                    click.echo(f"Error: User not found with email: {email}", err=True)
                    sys.exit(1)
                actual_user_id = user_obj.id
            else:
                assert user_id is not None  # Already validated above
                user_obj = await repo.get_user(user_id)
                if not user_obj:
                    click.echo(f"Error: User not found: {user_id}", err=True)
                    sys.exit(1)
                actual_user_id = user_id

            # Generate API key
            api_key_plaintext = generate_api_key()
            key_hash = hash_api_key(api_key_plaintext)

            # Create API key record
            api_key_obj = APIKey(
                id=str(uuid.uuid4()),
                user_id=actual_user_id,
                key_hash=key_hash,
                name=name,
                created_at=datetime.now(UTC),
                is_active=True,
            )

            await repo.create_api_key(api_key_obj)

            click.echo("\n✓ API key created successfully")
            click.echo(f"\n  API Key: {api_key_plaintext}")
            click.echo(f"  Name:    {name}")
            click.echo(f"  User:    {user_obj.email}")
            click.echo("\n  ⚠️  IMPORTANT: This is the only time you'll see this key!")
            click.echo("     Save it securely now.\n")

        finally:
            await repo.close()

    run_async(create())


@key.command("list")
@click.option("--user-id", help="Filter by user ID")
@click.option("--email", help="Filter by user email")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def key_list(user_id: str | None, email: str | None, json_output: bool):
    """List API keys (optionally filtered by user)."""

    async def list_keys():
        repo = get_repository()
        await repo.initialize()

        try:
            # Determine which keys to list
            if email:
                # Get user by email first
                user_obj = await repo.get_user_by_email(email)
                if not user_obj:
                    click.echo(f"Error: User not found with email: {email}", err=True)
                    sys.exit(1)
                keys = await repo.list_user_api_keys(user_obj.id)
                filter_user_id = user_obj.id
            elif user_id:
                keys = await repo.list_user_api_keys(user_id)
                filter_user_id = user_id
            else:
                # List all keys (need to get all users and their keys)
                users = await repo.list_users()
                keys = []
                for u in users:
                    user_keys = await repo.list_user_api_keys(u.id)
                    keys.extend(user_keys)
                filter_user_id = None

            if json_output:
                # JSON output
                keys_data = [
                    {
                        "id": k.id,
                        "user_id": k.user_id,
                        "name": k.name,
                        "created_at": k.created_at.isoformat(),
                        "last_used_at": (
                            k.last_used_at.isoformat() if k.last_used_at else None
                        ),
                        "is_active": k.is_active,
                    }
                    for k in keys
                ]
                click.echo(json.dumps(keys_data, indent=2))
            else:
                # Table output
                if not keys:
                    click.echo("No API keys found.")
                    return

                # Get user info for display if listing all keys
                if filter_user_id is None:
                    users = await repo.list_users()
                    user_map = {u.id: u for u in users}
                else:
                    user_obj = await repo.get_user(filter_user_id)
                    user_map = {user_obj.id: user_obj} if user_obj else {}

                click.echo(f"\n{'ID':<38} {'Name':<25} {'User':<25} {'Status':<10}")
                click.echo("-" * 100)
                for k in keys:
                    status = "Active" if k.is_active else "Revoked"
                    user_info = user_map.get(k.user_id)
                    user_display = user_info.email if user_info else k.user_id[:8]
                    key_name = k.name or "(unnamed)"
                    click.echo(
                        f"{k.id:<38} {key_name:<25} {user_display:<25} {status:<10}"
                    )
                click.echo()

        finally:
            await repo.close()

    run_async(list_keys())


@key.command("revoke")
@click.argument("key_id")
def key_revoke(key_id: str):
    """Revoke an API key."""

    async def revoke():
        repo = get_repository()
        await repo.initialize()

        try:
            # Check if key exists (by trying to list all keys and finding it)
            users = await repo.list_users()
            found_key = None
            for u in users:
                keys = await repo.list_user_api_keys(u.id)
                for k in keys:
                    if k.id == key_id:
                        found_key = k
                        break
                if found_key:
                    break

            if not found_key:
                click.echo(f"Error: API key not found: {key_id}", err=True)
                sys.exit(1)

            # Revoke key
            await repo.revoke_api_key(key_id)

            key_name = found_key.name or "(unnamed)"
            click.echo(f"✓ API key revoked: {key_name}")

        finally:
            await repo.close()

    run_async(revoke())


if __name__ == "__main__":
    cli()
