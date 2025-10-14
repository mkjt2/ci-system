# CI Admin CLI

Admin CLI for managing users and API keys in the CI system.

## Overview

The `ci-admin` command provides administrative functions for:
- Creating and managing user accounts
- Generating and revoking API keys
- Viewing user and key information
- Activating/deactivating users

## Installation

```bash
pip install -e .
```

The `ci-admin` command will be available globally.

## User Management

### Create a User

```bash
ci-admin user create --name "Alice Smith" --email "alice@example.com"
```

Output:
```
✓ User created successfully
  ID:    a1b2c3d4-e5f6-7890-abcd-ef1234567890
  Name:  Alice Smith
  Email: alice@example.com
```

### List Users

```bash
# Table format (default)
ci-admin user list

# JSON format
ci-admin user list --json
```

### Get User Details

```bash
# By email
ci-admin user get --email "alice@example.com"

# By user ID
ci-admin user get a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

### Deactivate/Activate Users

```bash
# Deactivate a user (prevents their API keys from working)
ci-admin user deactivate <user-id>

# Reactivate a user
ci-admin user activate <user-id>
```

## API Key Management

### Create an API Key

```bash
# By email
ci-admin key create --email "alice@example.com" --name "Development Key"

# By user ID
ci-admin key create --user-id <user-id> --name "Production Key"
```

Output:
```
✓ API key created successfully

  API Key: ci_abc123def456ghi789jkl012mno345pqr678stu901
  Name:    Development Key
  User:    alice@example.com

  ⚠️  IMPORTANT: This is the only time you'll see this key!
     Save it securely now.
```

**Note:** API keys are shown only once during creation. Store them securely (e.g., in environment variables or a password manager).

### List API Keys

```bash
# List keys for a specific user (by email)
ci-admin key list --email "alice@example.com"

# List keys for a specific user (by ID)
ci-admin key list --user-id <user-id>

# List all API keys
ci-admin key list

# JSON format
ci-admin key list --json
```

### Revoke an API Key

```bash
ci-admin key revoke <key-id>
```

Revoked keys immediately stop working for authentication.

## Configuration

### Database Location

By default, `ci-admin` uses the database at `~/.ci/jobs.db`. You can override this:

```bash
export CI_DB_PATH=/path/to/custom/database.db
ci-admin user create --name "Bob" --email "bob@example.com"
```

## Security Features

- **API Key Format**: Keys use the prefix `ci_` followed by 40+ characters (240-bit entropy)
- **Key Storage**: Keys are hashed using SHA-256 before storage
- **One-time Display**: Keys are shown only once during creation
- **Revocation**: Keys can be instantly revoked
- **User Activation**: Inactive users' keys don't work
- **Email Validation**: Email addresses are validated before user creation

## Example Workflow

```bash
# 1. Create a user
ci-admin user create --name "Alice" --email "alice@example.com"

# 2. Create an API key for the user
ci-admin key create --email "alice@example.com" --name "Dev Key"
# Save the output key: ci_abc123...

# 3. User configures their client
export CI_API_KEY="ci_abc123..."
ci submit test

# 4. Later, list the user's keys
ci-admin key list --email "alice@example.com"

# 5. Revoke a key if needed
ci-admin key revoke <key-id>
```

## Error Handling

The CLI provides clear error messages for common issues:

- Duplicate email addresses
- Invalid email format
- Non-existent users or keys
- Database connection problems

All errors exit with code 1 and display helpful messages on stderr.
