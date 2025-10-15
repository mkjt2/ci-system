#!/usr/bin/env python3
"""
Test script to verify that temporary files and Docker resources are cleaned up.

This script submits a job, waits for completion, and then verifies that:
1. Temporary zip files are deleted
2. Temporary directories are deleted
3. Docker images are removed
4. Docker containers are removed
"""

import subprocess
import sys
import time


def run_command(cmd):
    """Run a shell command and return output."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip()

def count_temp_files():
    """Count ci_job_* temp files and directories in /tmp."""
    result = run_command("find /tmp -name 'ci_job_*' 2>/dev/null | wc -l")
    return int(result)

def count_docker_images():
    """Count ci-job- Docker images."""
    result = run_command("docker images | grep 'ci-job-' | wc -l")
    return int(result)

def count_docker_containers():
    """Count ci-job- Docker containers."""
    result = run_command("docker ps -a | grep 'ci-job-' | wc -l")
    return int(result)

def main():
    print("=== Cleanup Verification Test ===\n")

    # Take baseline measurements
    print("📊 Baseline measurements:")
    baseline_temp = count_temp_files()
    baseline_images = count_docker_images()
    baseline_containers = count_docker_containers()
    print(f"  Temp files: {baseline_temp}")
    print(f"  Docker images: {baseline_images}")
    print(f"  Docker containers: {baseline_containers}")
    print()

    # Submit an async job
    print("🚀 Submitting test job...")
    job_output = run_command("cd tests/fixtures/dummy_project && ci submit test --async")

    if "Job submitted:" not in job_output:
        print(f"❌ Failed to submit job: {job_output}")
        sys.exit(1)

    job_id = job_output.split("Job submitted:")[-1].strip()
    print(f"  Job ID: {job_id}")
    print()

    # Wait for job to complete
    print("⏳ Waiting for job to complete...")
    time.sleep(8)  # Give it time to complete
    print()

    # Take post-job measurements (before reconciliation completes)
    print("📊 Post-job measurements (immediately after):")
    post_temp = count_temp_files()
    post_images = count_docker_images()
    post_containers = count_docker_containers()
    print(f"  Temp files: {post_temp} (change: {post_temp - baseline_temp:+d})")
    print(f"  Docker images: {post_images} (change: {post_images - baseline_images:+d})")
    print(f"  Docker containers: {post_containers} (change: {post_containers - baseline_containers:+d})")
    print()

    # Wait for reconciliation loop to clean up
    print("⏳ Waiting for reconciliation loop to clean up resources...")
    time.sleep(5)  # Reconciliation loop runs every 2 seconds
    print()

    # Take final measurements
    print("📊 Final measurements (after reconciliation):")
    final_temp = count_temp_files()
    final_images = count_docker_images()
    final_containers = count_docker_containers()
    print(f"  Temp files: {final_temp} (change: {final_temp - baseline_temp:+d})")
    print(f"  Docker images: {final_images} (change: {final_images - baseline_images:+d})")
    print(f"  Docker containers: {final_containers} (change: {final_containers - baseline_containers:+d})")
    print()

    # Verify cleanup
    print("✅ Verification:")
    success = True

    if final_temp == baseline_temp:
        print("  ✓ Temp files cleaned up (no net increase)")
    else:
        print(f"  ✗ Temp files NOT cleaned up (leaked {final_temp - baseline_temp} files)")
        success = False

    if final_images == baseline_images:
        print("  ✓ Docker images cleaned up (no net increase)")
    else:
        print(f"  ✗ Docker images NOT cleaned up (leaked {final_images - baseline_images} images)")
        success = False

    if final_containers == baseline_containers:
        print("  ✓ Docker containers cleaned up (no net increase)")
    else:
        print(f"  ✗ Docker containers NOT cleaned up (leaked {final_containers - baseline_containers} containers)")
        success = False

    print()
    if success:
        print("🎉 All cleanup tests passed!")
        sys.exit(0)
    else:
        print("❌ Some cleanup tests failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
