"""
Authentication and authorization utilities for the CI server.

This module provides API key generation, hashing, and validation,
as well as FastAPI dependency for authentication.
"""

import hashlib
import secrets
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ci_common.models import User
from ci_common.repository import JobRepository

# HTTP Bearer token authentication scheme
security = HTTPBearer()


def generate_api_key() -> str:
    """
    Generate a new API key with format: ci_<40 random chars>.

    The key uses URL-safe base64 encoding with 240 bits of entropy,
    making it cryptographically secure for authentication.

    Returns:
        API key string in format "ci_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

    Example:
        >>> key = generate_api_key()
        >>> key.startswith("ci_")
        True
        >>> len(key)
        43
    """
    # Generate 30 random bytes (240 bits of entropy)
    # Base64 encoding produces 40 characters from 30 bytes
    random_part = secrets.token_urlsafe(30)[:40]
    return f"ci_{random_part}"


def hash_api_key(api_key: str) -> str:
    """
    Hash an API key using SHA-256 for secure storage.

    Only hashed keys are stored in the database. The plaintext key
    is shown once during creation and must be saved by the user.

    Args:
        api_key: The plaintext API key to hash

    Returns:
        Hex-encoded SHA-256 hash of the API key

    Example:
        >>> hash_api_key("ci_test123")
        'f8d3b5e7...'  # 64-character hex string
    """
    return hashlib.sha256(api_key.encode()).hexdigest()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> User:
    """
    FastAPI dependency that validates API key and returns the current user.

    This function is used as a dependency in FastAPI endpoints to enforce
    authentication. It extracts the API key from the Authorization header,
    validates it against the database, and returns the associated user.

    Args:
        credentials: HTTP Bearer token credentials from request header

    Returns:
        User object if authentication succeeds

    Raises:
        HTTPException: 401 if API key is invalid, revoked, or user is inactive

    Usage:
        @app.get("/protected")
        async def protected_endpoint(user: User = Depends(get_current_user)):
            return {"user_id": user.id, "name": user.name}

    Note:
        This function requires the repository to be injected via dependency.
        See get_current_user_with_repo() for the actual implementation.
    """
    # This is a stub - the actual implementation uses get_repository()
    # which is defined in app.py. This function signature is kept for
    # type hints and documentation.
    raise NotImplementedError(
        "Use get_current_user_with_repo() with repository dependency"
    )


def create_get_current_user_dependency(
    get_repository_func,  # type: ignore
):
    """
    Create a get_current_user dependency with repository injection.

    This factory function creates the actual authentication dependency
    that can access the repository. It's needed because the repository
    getter is defined in app.py, creating a circular dependency if we
    try to import it here.

    Args:
        get_repository_func: Function that returns the JobRepository instance

    Returns:
        Async function that can be used as FastAPI dependency

    Example:
        # In app.py:
        get_current_user = create_get_current_user_dependency(get_repository)

        @app.get("/jobs")
        async def list_jobs(user: User = Depends(get_current_user)):
            ...
    """

    async def get_current_user_with_repo(
        credentials: HTTPAuthorizationCredentials = Security(security),
        repository: JobRepository = Depends(get_repository_func),
    ) -> User:
        """
        Validate API key and return current user.

        Args:
            credentials: HTTP Bearer token from Authorization header
            repository: JobRepository instance for database access

        Returns:
            User object if authentication succeeds

        Raises:
            HTTPException: 401 if authentication fails
        """
        # Extract API key from Bearer token
        api_key = credentials.credentials

        # Hash the key to compare with database
        key_hash = hash_api_key(api_key)

        # Look up API key in database
        api_key_obj = await repository.get_api_key_by_hash(key_hash)

        # Validate API key exists and is active
        if not api_key_obj or not api_key_obj.is_active:
            raise HTTPException(
                status_code=401,
                detail="Invalid or revoked API key",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Get the user associated with this API key
        user = await repository.get_user(api_key_obj.user_id)

        # Validate user exists and is active
        if not user or not user.is_active:
            raise HTTPException(
                status_code=401,
                detail="User not found or inactive",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Update last_used_at timestamp
        # Note: We update synchronously to avoid issues with background tasks
        # in test environments. This adds minimal latency (~1-2ms for SQLite)
        await repository.update_api_key_last_used(api_key_obj.id, datetime.now(UTC))

        return user

    return get_current_user_with_repo
