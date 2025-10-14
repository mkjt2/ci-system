"""
Unit tests for ci_server.auth.

Tests API key generation, hashing, and authentication logic.
"""

import hashlib
import re

from ci_server.auth import generate_api_key, hash_api_key


class TestAPIKeyGeneration:
    """Test suite for API key generation."""

    def test_generate_api_key_format(self):
        """Test that generated API keys have the correct format."""
        api_key = generate_api_key()

        # Should start with "ci_"
        assert api_key.startswith("ci_")

        # Should be 43 characters total (ci_ + 40 chars)
        assert len(api_key) == 43

        # Should only contain URL-safe base64 characters
        # (alphanumeric, -, _)
        pattern = r"^ci_[A-Za-z0-9_-]{40}$"
        assert re.match(pattern, api_key)

    def test_generate_api_key_uniqueness(self):
        """Test that generated API keys are unique."""
        keys = [generate_api_key() for _ in range(100)]

        # All keys should be unique
        assert len(keys) == len(set(keys))

    def test_generate_api_key_randomness(self):
        """Test that generated keys are sufficiently random."""
        key1 = generate_api_key()
        key2 = generate_api_key()

        # Keys should be different
        assert key1 != key2

        # Keys should not share the same suffix
        assert key1[3:] != key2[3:]


class TestAPIKeyHashing:
    """Test suite for API key hashing."""

    def test_hash_api_key_deterministic(self):
        """Test that hashing the same key produces the same hash."""
        api_key = "ci_test123456789012345678901234567890"

        hash1 = hash_api_key(api_key)
        hash2 = hash_api_key(api_key)

        assert hash1 == hash2

    def test_hash_api_key_format(self):
        """Test that hash is a valid SHA-256 hex string."""
        api_key = generate_api_key()
        key_hash = hash_api_key(api_key)

        # SHA-256 produces 64 hex characters
        assert len(key_hash) == 64

        # Should only contain hex characters
        assert re.match(r"^[0-9a-f]{64}$", key_hash)

    def test_hash_api_key_different_keys(self):
        """Test that different keys produce different hashes."""
        key1 = "ci_key1234567890123456789012345678901"
        key2 = "ci_key0987654321098765432109876543210"

        hash1 = hash_api_key(key1)
        hash2 = hash_api_key(key2)

        assert hash1 != hash2

    def test_hash_api_key_matches_sha256(self):
        """Test that hash matches standard SHA-256 implementation."""
        api_key = "ci_testkey1234567890123456789012345678"

        our_hash = hash_api_key(api_key)
        expected_hash = hashlib.sha256(api_key.encode()).hexdigest()

        assert our_hash == expected_hash

    def test_hash_api_key_empty_string(self):
        """Test that empty string can be hashed."""
        key_hash = hash_api_key("")

        # Should produce valid SHA-256 hash
        assert len(key_hash) == 64
        assert re.match(r"^[0-9a-f]{64}$", key_hash)

    def test_hash_api_key_special_characters(self):
        """Test hashing keys with special characters."""
        api_key = "ci_test!@#$%^&*()_+-=[]{}|;:',.<>?/"

        key_hash = hash_api_key(api_key)

        # Should produce valid SHA-256 hash
        assert len(key_hash) == 64
        assert re.match(r"^[0-9a-f]{64}$", key_hash)


class TestAPIKeyGenerationAndHashing:
    """Integration tests for key generation and hashing."""

    def test_generated_keys_hash_correctly(self):
        """Test that generated keys can be hashed."""
        api_key = generate_api_key()
        key_hash = hash_api_key(api_key)

        assert len(key_hash) == 64
        assert re.match(r"^[0-9a-f]{64}$", key_hash)

    def test_generated_keys_produce_unique_hashes(self):
        """Test that multiple generated keys produce unique hashes."""
        keys = [generate_api_key() for _ in range(10)]
        hashes = [hash_api_key(key) for key in keys]

        # All hashes should be unique
        assert len(hashes) == len(set(hashes))

    def test_hash_length_constant(self):
        """Test that hash length is constant regardless of input."""
        short_key = "ci_short"
        long_key = "ci_" + "x" * 1000

        hash1 = hash_api_key(short_key)
        hash2 = hash_api_key(long_key)

        # Both should be 64 characters (SHA-256 property)
        assert len(hash1) == 64
        assert len(hash2) == 64


class TestAPIKeySecurity:
    """Security-focused tests for API key generation."""

    def test_key_entropy(self):
        """Test that keys have sufficient entropy."""
        # Generate 1000 keys and check character distribution
        keys = [generate_api_key() for _ in range(1000)]

        # Extract just the random parts (after "ci_")
        random_parts = [key[3:] for key in keys]

        # Check that we use a good variety of characters
        all_chars = "".join(random_parts)
        unique_chars = set(all_chars)

        # URL-safe base64 has 64 possible characters
        # We should see at least 50 different characters in 1000 keys
        assert len(unique_chars) >= 50

    def test_no_sequential_keys(self):
        """Test that keys are not sequential or predictable."""
        keys = [generate_api_key() for _ in range(100)]

        # No two keys should differ by only one character
        for i, key1 in enumerate(keys):
            for key2 in keys[i + 1 :]:
                # Count differences
                diffs = sum(c1 != c2 for c1, c2 in zip(key1, key2, strict=False))
                # Keys should differ in many positions (not just 1 or 2)
                assert diffs > 10

    def test_hash_collision_resistance(self):
        """Test that small changes in key produce very different hashes."""
        base_key = "ci_test1234567890123456789012345678901"

        # Change one character
        modified_key = "ci_test1234567890123456789012345678902"

        hash1 = hash_api_key(base_key)
        hash2 = hash_api_key(modified_key)

        # Hashes should be completely different (avalanche effect)
        # Count different characters
        diffs = sum(c1 != c2 for c1, c2 in zip(hash1, hash2, strict=False))

        # SHA-256 should have avalanche effect - at least 50% different
        assert diffs > 32  # More than half the characters differ
