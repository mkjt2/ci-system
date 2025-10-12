import sys
import argparse
from pathlib import Path
from .client import submit_tests_streaming


def main():
    """Main entry point for the CI CLI."""
    parser = argparse.ArgumentParser(description="CI System CLI")
    subparsers = parser.add_subparsers(dest="command")
    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("job_type", choices=["test"])

    args = parser.parse_args()

    if args.command == "submit" and args.job_type == "test":
        success = False
        for event in submit_tests_streaming(Path.cwd()):
            if event["type"] == "log":
                print(event["data"], end="", flush=True)
            elif event["type"] == "complete":
                success = event["success"]
        sys.exit(0 if success else 1)

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()