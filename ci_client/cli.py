import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .client import list_jobs, submit_tests_async, submit_tests_streaming, wait_for_job


def get_server_url() -> str:
    """
    Get the CI server URL from environment variable or use default.

    Returns:
        Server URL string

    Environment variables:
    - CI_SERVER_URL: Custom server URL (useful for testing with different ports)
    """
    return os.environ.get("CI_SERVER_URL", "http://localhost:8000")


def get_api_key(cli_arg: str | None = None) -> str | None:
    """
    Get API key from multiple sources in priority order.

    Priority (highest to lowest):
    1. Command line argument (--api-key)
    2. Environment variable (CI_API_KEY)
    3. Config file (~/.ci/config)

    Args:
        cli_arg: API key from command line argument (highest priority)

    Returns:
        API key string if found, None otherwise

    Config file format (~/.ci/config):
        api_key=ci_abc123...
    """
    # Priority 1: Command line argument
    if cli_arg:
        return cli_arg

    # Priority 2: Environment variable
    env_key = os.environ.get("CI_API_KEY")
    if env_key:
        return env_key

    # Priority 3: Config file
    config_path = Path.home() / ".ci" / "config"
    if config_path.exists():
        try:
            content = config_path.read_text()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("api_key="):
                    return line[8:].strip()
        except OSError:
            pass  # Silently ignore config file read errors

    return None


def main():
    """Main entry point for the CI CLI."""
    parser = argparse.ArgumentParser(description="CI System CLI")
    subparsers = parser.add_subparsers(dest="command")

    # ci submit test [--async] [--api-key KEY]
    submit_parser = subparsers.add_parser(
        "submit", help="Submit a job to the CI system"
    )
    submit_parser.add_argument(
        "job_type", choices=["test"], help="Type of job to submit"
    )
    submit_parser.add_argument(
        "--async",
        dest="async_mode",
        action="store_true",
        help="Submit job asynchronously and return job ID immediately",
    )
    submit_parser.add_argument(
        "--api-key",
        dest="api_key",
        help="API key for authentication (can also use CI_API_KEY env var or ~/.ci/config)",
    )

    # ci wait <job_id> [--all] [--api-key KEY]
    wait_parser = subparsers.add_parser(
        "wait", help="Wait for a job to complete and stream logs"
    )
    wait_parser.add_argument("job_id", help="Job ID to wait for")
    wait_parser.add_argument(
        "--all",
        dest="from_beginning",
        action="store_true",
        help="Show all logs from beginning (default: only show new logs)",
    )
    wait_parser.add_argument(
        "--api-key",
        dest="api_key",
        help="API key for authentication (can also use CI_API_KEY env var or ~/.ci/config)",
    )

    # ci list [--json] [--api-key KEY]
    list_parser = subparsers.add_parser("list", help="List all jobs")
    list_parser.add_argument(
        "--json",
        dest="json_mode",
        action="store_true",
        help="Output in JSON format",
    )
    list_parser.add_argument(
        "--api-key",
        dest="api_key",
        help="API key for authentication (can also use CI_API_KEY env var or ~/.ci/config)",
    )

    args = parser.parse_args()

    # Get server URL from environment
    server_url = get_server_url()

    # Get API key from command line, environment, or config file
    api_key = get_api_key(getattr(args, "api_key", None))

    if args.command == "submit" and args.job_type == "test":
        if args.async_mode:
            # Async mode: submit and return job ID immediately
            try:
                job_id = submit_tests_async(Path.cwd(), server_url=server_url, api_key=api_key)
                print(f"Job submitted: {job_id}")
                sys.exit(0)
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "401" in error_msg or "403" in error_msg or "unauthorized" in error_msg or "forbidden" in error_msg:
                    print(f"Error: {e}", file=sys.stderr)
                    print("\nAuthentication required. Please provide an API key using one of:", file=sys.stderr)
                    print("  1. Command line flag: --api-key <key>", file=sys.stderr)
                    print("  2. Environment variable: CI_API_KEY=<key>", file=sys.stderr)
                    print("  3. Config file: ~/.ci/config (format: api_key=<key>)", file=sys.stderr)
                    sys.exit(1)
                else:
                    print(f"Error: {e}", file=sys.stderr)
                    sys.exit(1)
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            # Sync mode: submit and wait for completion (original behavior)
            try:
                success = False
                for event in submit_tests_streaming(Path.cwd(), server_url=server_url, api_key=api_key):
                    if event["type"] == "job_id":
                        # Print job ID so user can reconnect from another terminal
                        print(f"Job ID: {event['job_id']}", file=sys.stderr)
                        print(
                            "You can reconnect from another terminal with: ci wait "
                            f"{event['job_id']}",
                            file=sys.stderr,
                        )
                        print("", file=sys.stderr)  # Blank line
                    elif event["type"] == "log":
                        print(event["data"], end="", flush=True)
                    elif event["type"] == "complete":
                        success = event["success"]
                sys.exit(0 if success else 1)
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "401" in error_msg or "403" in error_msg or "unauthorized" in error_msg or "forbidden" in error_msg:
                    print(f"\nError: {e}", file=sys.stderr)
                    print("\nAuthentication required. Please provide an API key using one of:", file=sys.stderr)
                    print("  1. Command line flag: --api-key <key>", file=sys.stderr)
                    print("  2. Environment variable: CI_API_KEY=<key>", file=sys.stderr)
                    print("  3. Config file: ~/.ci/config (format: api_key=<key>)", file=sys.stderr)
                    sys.exit(1)
                else:
                    print(f"\nError: {e}", file=sys.stderr)
                    sys.exit(1)
            except KeyboardInterrupt:
                print("\n\nJob cancelled by user.", file=sys.stderr)
                sys.exit(130)  # Standard exit code for SIGINT

    elif args.command == "wait":
        # Wait for a job and stream logs
        try:
            success = False
            for event in wait_for_job(
                args.job_id, from_beginning=args.from_beginning, server_url=server_url, api_key=api_key
            ):
                if event["type"] == "log":
                    print(event["data"], end="", flush=True)
                elif event["type"] == "complete":
                    success = event["success"]
            sys.exit(0 if success else 1)
        except RuntimeError as e:
            error_msg = str(e).lower()
            if "401" in error_msg or "403" in error_msg or "unauthorized" in error_msg or "forbidden" in error_msg:
                print(f"Error: {e}", file=sys.stderr)
                print("\nAuthentication required. Please provide an API key using one of:", file=sys.stderr)
                print("  1. Command line flag: --api-key <key>", file=sys.stderr)
                print("  2. Environment variable: CI_API_KEY=<key>", file=sys.stderr)
                print("  3. Config file: ~/.ci/config (format: api_key=<key>)", file=sys.stderr)
                sys.exit(1)
            else:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
        except KeyboardInterrupt:
            print(f"\n\nStopped waiting for job {args.job_id}.", file=sys.stderr)
            print(
                "The job continues to run on the server. Use 'ci wait' to reconnect.",
                file=sys.stderr,
            )
            sys.exit(130)  # Standard exit code for SIGINT

    elif args.command == "list":
        # List all jobs
        try:
            jobs = list_jobs(server_url=server_url, api_key=api_key)

            if args.json_mode:
                # JSON output mode
                print(json.dumps(jobs, indent=2))
                sys.exit(0)

            # Human-readable table mode
            if not jobs:
                print("No jobs found.")
                sys.exit(0)

            # Print header
            print(
                f"{'JOB ID':<38} {'STATUS':<12} {'START TIME':<22} {'END TIME':<22} {'SUCCESS':<8}"
            )
            print("-" * 110)

            # Print each job
            for job in jobs:
                job_id = job["job_id"][:36]  # Truncate if needed
                status = job["status"]
                start_time = (
                    format_time(job.get("start_time"))
                    if job.get("start_time")
                    else "N/A"
                )
                end_time = (
                    format_time(job.get("end_time")) if job.get("end_time") else "N/A"
                )
                success = format_success(job.get("success"))

                print(
                    f"{job_id:<38} {status:<12} {start_time:<22} {end_time:<22} {success:<8}"
                )

            sys.exit(0)
        except RuntimeError as e:
            error_msg = str(e).lower()
            if "401" in error_msg or "403" in error_msg or "unauthorized" in error_msg or "forbidden" in error_msg:
                print(f"Error: {e}", file=sys.stderr)
                print("\nAuthentication required. Please provide an API key using one of:", file=sys.stderr)
                print("  1. Command line flag: --api-key <key>", file=sys.stderr)
                print("  2. Environment variable: CI_API_KEY=<key>", file=sys.stderr)
                print("  3. Config file: ~/.ci/config (format: api_key=<key>)", file=sys.stderr)
                sys.exit(1)
            else:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)

    parser.print_help()
    sys.exit(1)


def format_time(time_str: str | None) -> str:
    """Format ISO timestamp to human-readable format."""
    if not time_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return time_str


def format_success(success: bool | None) -> str:
    """Format success value to human-readable string."""
    if success is None:
        return "-"
    return "✓" if success else "✗"


if __name__ == "__main__":
    main()
