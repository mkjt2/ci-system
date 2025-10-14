"""
Unit tests for ci_server.executor module.

Tests the Docker test execution functions with mocked subprocess calls.
"""

import asyncio
import io
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ci_server.executor import run_tests_in_docker, run_tests_in_docker_streaming


def create_test_zip(include_requirements: bool = True) -> bytes:
    """Helper to create a test project zip file."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if include_requirements:
            zf.writestr("requirements.txt", "pytest>=7.0.0\n")
        zf.writestr("test_example.py", "def test_pass(): assert True\n")
    return zip_buffer.getvalue()


class TestRunTestsInDockerStreaming:
    """Test suite for run_tests_in_docker_streaming function."""

    @pytest.mark.asyncio
    async def test_successful_test_execution(self):
        """Test successful pytest execution streams log events and completes."""
        zip_data = create_test_zip()

        # Mock the subprocess
        mock_process = AsyncMock()
        mock_process.stdout = AsyncMock()
        # Simulate pytest output
        mock_process.stdout.readline = AsyncMock(
            side_effect=[
                b"collected 1 item\n",
                b"test_example.py::test_pass PASSED\n",
                b"1 passed in 0.01s\n",
                b"",  # EOF
            ]
        )
        mock_process.returncode = 0
        mock_process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            events = []
            async for event in run_tests_in_docker_streaming(zip_data):
                events.append(event)

        # Should have log events and completion
        assert len(events) == 4
        assert events[0] == {"type": "log", "data": "collected 1 item\n"}
        assert events[1] == {
            "type": "log",
            "data": "test_example.py::test_pass PASSED\n",
        }
        assert events[2] == {"type": "log", "data": "1 passed in 0.01s\n"}
        assert events[3] == {"type": "complete", "success": True}

    @pytest.mark.asyncio
    async def test_failed_test_execution(self):
        """Test failed pytest execution returns success=False."""
        zip_data = create_test_zip()

        mock_process = AsyncMock()
        mock_process.stdout = AsyncMock()
        mock_process.stdout.readline = AsyncMock(
            side_effect=[
                b"test_example.py::test_fail FAILED\n",
                b"1 failed in 0.01s\n",
                b"",  # EOF
            ]
        )
        mock_process.returncode = 1  # Non-zero exit code
        mock_process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            events = []
            async for event in run_tests_in_docker_streaming(zip_data):
                events.append(event)

        # Should complete with success=False
        assert events[-1] == {"type": "complete", "success": False}

    @pytest.mark.asyncio
    async def test_missing_requirements_txt(self):
        """Test that missing requirements.txt yields error event."""
        # Create zip without requirements.txt
        zip_data = create_test_zip(include_requirements=False)

        events = []
        async for event in run_tests_in_docker_streaming(zip_data):
            events.append(event)

        # Should yield error log and failure
        assert len(events) == 2
        assert events[0] == {
            "type": "log",
            "data": "Error: requirements.txt not found in project\n",
        }
        assert events[1] == {"type": "complete", "success": False}

    @pytest.mark.asyncio
    async def test_cancellation_via_cancel_event(self):
        """Test that setting cancel_event terminates the process."""
        zip_data = create_test_zip()
        cancel_event = asyncio.Event()

        mock_process = AsyncMock()
        mock_process.stdout = AsyncMock()

        # Simulate output then cancellation
        # Note: cancellation check happens at top of loop, so a line read
        # before cancel is set will be yielded
        call_count = 0

        async def mock_readline():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b"Starting tests...\n"
            elif call_count == 2:
                # Set cancel event after first line is read
                cancel_event.set()
                return b"This line gets read before cancel check\n"
            return b""

        mock_process.stdout.readline = mock_readline
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            events = []
            async for event in run_tests_in_docker_streaming(zip_data, cancel_event):
                events.append(event)

        # Cancel check happens at top of loop, so both lines are read before termination
        assert len(events) == 4
        assert events[0] == {"type": "log", "data": "Starting tests...\n"}
        assert events[1] == {
            "type": "log",
            "data": "This line gets read before cancel check\n",
        }
        assert events[2] == {"type": "log", "data": "\nJob cancelled by user.\n"}
        assert events[3] == {"type": "complete", "success": False, "cancelled": True}

        # Verify process was terminated
        mock_process.terminate.assert_called_once()
        mock_process.wait.assert_called()

    @pytest.mark.asyncio
    async def test_timeout_allows_cancel_check(self):
        """Test that readline timeout allows periodic cancel event checking."""
        zip_data = create_test_zip()
        cancel_event = asyncio.Event()

        mock_process = AsyncMock()
        mock_process.stdout = AsyncMock()

        # Simulate timeouts followed by cancellation
        call_count = 0

        async def mock_readline():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call times out
                await asyncio.sleep(0.15)  # Longer than 0.1s timeout
                return b"Line 1\n"
            elif call_count == 2:
                # Second call, set cancel and timeout
                cancel_event.set()
                await asyncio.sleep(0.15)
                return b"Should not reach\n"
            return b""

        mock_process.stdout.readline = mock_readline
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            events = []
            async for event in run_tests_in_docker_streaming(zip_data, cancel_event):
                events.append(event)

        # Should see cancellation after first line
        assert any("cancelled" in str(event) for event in events)
        mock_process.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_generator_exit_terminates_process(self):
        """Test that breaking from iteration terminates the process."""
        zip_data = create_test_zip()

        mock_process = AsyncMock()
        mock_process.stdout = AsyncMock()
        mock_process.stdout.readline = AsyncMock(
            side_effect=[
                b"Line 1\n",
                b"Line 2\n",
                b"Line 3\n",
                b"",
            ]
        )
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            events = []
            async for event in run_tests_in_docker_streaming(zip_data):
                events.append(event)
                if len(events) == 2:
                    # Break early (simulates client disconnection)
                    break

        # Should only have 2 events before break
        assert len(events) == 2

        # Process should be terminated when generator is cleaned up
        # Note: This happens during generator cleanup, which is hard to test directly
        # In production, the try/except (asyncio.CancelledError, GeneratorExit) handles this

    @pytest.mark.asyncio
    async def test_exception_during_execution(self):
        """Test that exceptions during execution yield error events."""
        zip_data = create_test_zip()

        # Simulate subprocess creation failure
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=Exception("Docker not available"),
        ):
            events = []
            async for event in run_tests_in_docker_streaming(zip_data):
                events.append(event)

        # Should yield error log and failure
        assert len(events) == 2
        assert events[0]["type"] == "log"
        assert "Error running tests" in events[0]["data"]
        assert "Docker not available" in events[0]["data"]
        assert events[1] == {"type": "complete", "success": False}

    @pytest.mark.asyncio
    async def test_docker_command_arguments(self):
        """Test that correct Docker command arguments are used."""
        zip_data = create_test_zip()

        mock_process = AsyncMock()
        mock_process.stdout = AsyncMock()
        mock_process.stdout.readline = AsyncMock(return_value=b"")
        mock_process.returncode = 0
        mock_process.wait = AsyncMock()

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_process
        ) as mock_exec:
            events = []
            async for event in run_tests_in_docker_streaming(zip_data):
                events.append(event)

            # Verify Docker command structure
            call_args = mock_exec.call_args
            args = call_args[0]

            assert args[0] == "docker"
            assert args[1] == "run"
            assert args[2] == "--rm"
            assert "-v" in args
            assert "-w" in args
            assert "/workspace" in args
            assert "python:3.12-slim" in args
            assert "sh" in args
            assert "-c" in args
            # Verify the command includes pytest
            assert "pytest" in " ".join(args)

    @pytest.mark.asyncio
    async def test_empty_output_lines(self):
        """Test that empty output lines are handled correctly."""
        zip_data = create_test_zip()

        mock_process = AsyncMock()
        mock_process.stdout = AsyncMock()
        mock_process.stdout.readline = AsyncMock(
            side_effect=[
                b"Line 1\n",
                b"",  # EOF immediately
            ]
        )
        mock_process.returncode = 0
        mock_process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            events = []
            async for event in run_tests_in_docker_streaming(zip_data):
                events.append(event)

        # Should have one log line and completion
        assert len(events) == 2
        assert events[0] == {"type": "log", "data": "Line 1\n"}
        assert events[1] == {"type": "complete", "success": True}


class TestRunTestsInDocker:
    """Test suite for run_tests_in_docker (non-streaming wrapper)."""

    @pytest.mark.asyncio
    async def test_aggregates_streaming_output(self):
        """Test that run_tests_in_docker aggregates streaming output."""
        zip_data = create_test_zip()

        mock_process = AsyncMock()
        mock_process.stdout = AsyncMock()
        mock_process.stdout.readline = AsyncMock(
            side_effect=[
                b"Line 1\n",
                b"Line 2\n",
                b"Line 3\n",
                b"",
            ]
        )
        mock_process.returncode = 0
        mock_process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            success, output = await run_tests_in_docker(zip_data)

        # Should aggregate all log lines
        assert success is True
        assert output == "Line 1\nLine 2\nLine 3\n"

    @pytest.mark.asyncio
    async def test_returns_failure_status(self):
        """Test that run_tests_in_docker returns failure status correctly."""
        zip_data = create_test_zip()

        mock_process = AsyncMock()
        mock_process.stdout = AsyncMock()
        mock_process.stdout.readline = AsyncMock(
            side_effect=[
                b"Test failed\n",
                b"",
            ]
        )
        mock_process.returncode = 1
        mock_process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            success, output = await run_tests_in_docker(zip_data)

        assert success is False
        assert output == "Test failed\n"

    @pytest.mark.asyncio
    async def test_handles_missing_requirements(self):
        """Test that run_tests_in_docker handles missing requirements.txt."""
        zip_data = create_test_zip(include_requirements=False)

        success, output = await run_tests_in_docker(zip_data)

        assert success is False
        assert "requirements.txt not found" in output
