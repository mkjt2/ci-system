import sys
import argparse
from pathlib import Path
from .client import submit_tests_streaming, submit_tests_async, wait_for_job


def main():
    """Main entry point for the CI CLI."""
    parser = argparse.ArgumentParser(description="CI System CLI")
    subparsers = parser.add_subparsers(dest="command")

    # ci submit test [--async]
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

    # ci wait <job_id>
    wait_parser = subparsers.add_parser(
        "wait", help="Wait for a job to complete and stream logs"
    )
    wait_parser.add_argument("job_id", help="Job ID to wait for")

    args = parser.parse_args()

    if args.command == "submit" and args.job_type == "test":
        if args.async_mode:
            # Async mode: submit and return job ID immediately
            try:
                job_id = submit_tests_async(Path.cwd())
                print(f"Job submitted: {job_id}")
                sys.exit(0)
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            # Sync mode: submit and wait for completion (original behavior)
            try:
                success = False
                for event in submit_tests_streaming(Path.cwd()):
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
            except KeyboardInterrupt:
                print("\n\nJob cancelled by user.", file=sys.stderr)
                sys.exit(130)  # Standard exit code for SIGINT

    elif args.command == "wait":
        # Wait for a job and stream logs
        try:
            success = False
            for event in wait_for_job(args.job_id):
                if event["type"] == "log":
                    print(event["data"], end="", flush=True)
                elif event["type"] == "complete":
                    success = event["success"]
            sys.exit(0 if success else 1)
        except KeyboardInterrupt:
            print(f"\n\nStopped waiting for job {args.job_id}.", file=sys.stderr)
            print(
                "The job continues to run on the server. Use 'ci wait' to reconnect.",
                file=sys.stderr,
            )
            sys.exit(130)  # Standard exit code for SIGINT

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
