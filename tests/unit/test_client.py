"""
Unit tests for ci_client.client module.

Tests the project zip creation and HTTP client functions.
"""

import io
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from ci_client.client import (
    create_project_zip,
    list_jobs,
    submit_tests_async,
    submit_tests_streaming,
    wait_for_job,
)


class TestCreateProjectZip:
    """Test suite for create_project_zip function."""

    @pytest.fixture
    def temp_project(self):
        """Create a temporary project directory with various files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Create normal files that should be included
            (project_dir / "README.md").write_text("# Test Project")
            (project_dir / "requirements.txt").write_text("pytest>=7.0.0\n")

            # Create source directory
            src_dir = project_dir / "src"
            src_dir.mkdir()
            (src_dir / "main.py").write_text("def main(): pass")
            (src_dir / "__init__.py").write_text("")

            # Create tests directory
            tests_dir = project_dir / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_main.py").write_text("def test_main(): pass")

            yield project_dir

    def test_creates_valid_zip_file(self, temp_project):
        """Test that create_project_zip returns valid zip bytes."""
        zip_bytes = create_project_zip(temp_project)

        # Should return bytes
        assert isinstance(zip_bytes, bytes)
        assert len(zip_bytes) > 0

        # Should be a valid zip file
        zip_buffer = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(zip_buffer, "r") as zf:
            # Should not raise exception
            zf.testzip()

    def test_includes_expected_files(self, temp_project):
        """Test that normal files are included in the zip."""
        zip_bytes = create_project_zip(temp_project)

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = set(zf.namelist())

            # Check that expected files are present
            assert "README.md" in names
            assert "requirements.txt" in names
            assert "src/main.py" in names
            assert "src/__init__.py" in names
            assert "tests/test_main.py" in names

    def test_excludes_hidden_directories(self, temp_project):
        """Test that files in hidden directories (starting with .) are excluded."""
        # Create hidden directory with files
        hidden_dir = temp_project / ".git"
        hidden_dir.mkdir()
        (hidden_dir / "config").write_text("git config")

        nested_hidden = temp_project / "src" / ".hidden"
        nested_hidden.mkdir()
        (nested_hidden / "secret.txt").write_text("secret")

        zip_bytes = create_project_zip(temp_project)

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = set(zf.namelist())

            # Hidden directory files should be excluded
            assert ".git/config" not in names
            assert "src/.hidden/secret.txt" not in names

            # Normal files should still be present
            assert "src/main.py" in names

    def test_excludes_pycache_directories(self, temp_project):
        """Test that __pycache__ directories are excluded."""
        # Create __pycache__ directories
        pycache_dir = temp_project / "__pycache__"
        pycache_dir.mkdir()
        (pycache_dir / "main.cpython-312.pyc").write_bytes(b"compiled")

        nested_pycache = temp_project / "src" / "__pycache__"
        nested_pycache.mkdir()
        (nested_pycache / "module.cpython-312.pyc").write_bytes(b"compiled")

        zip_bytes = create_project_zip(temp_project)

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = set(zf.namelist())

            # __pycache__ files should be excluded
            assert "__pycache__/main.cpython-312.pyc" not in names
            assert "src/__pycache__/module.cpython-312.pyc" not in names

            # Normal files should still be present
            assert "src/main.py" in names

    def test_excludes_dotfiles_in_root(self, temp_project):
        """Test that dotfiles are excluded."""
        (temp_project / ".gitignore").write_text("*.pyc\n")
        (temp_project / ".env").write_text("SECRET=123")

        zip_bytes = create_project_zip(temp_project)

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = set(zf.namelist())

            # Dotfiles should be excluded
            assert ".gitignore" not in names
            assert ".env" not in names

    def test_includes_nested_directories(self, temp_project):
        """Test that deeply nested files are included."""
        deep_dir = temp_project / "src" / "utils" / "helpers"
        deep_dir.mkdir(parents=True)
        (deep_dir / "helper.py").write_text("def help(): pass")

        zip_bytes = create_project_zip(temp_project)

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = set(zf.namelist())
            assert "src/utils/helpers/helper.py" in names

    def test_preserves_directory_structure(self, temp_project):
        """Test that relative paths are preserved in the zip."""
        zip_bytes = create_project_zip(temp_project)

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            # Files should be stored with paths relative to project_dir
            for name in zf.namelist():
                # Should not have absolute paths
                assert not Path(name).is_absolute()
                # Should not start with parent directory references
                assert not name.startswith("..")

    def test_only_includes_files_not_directories(self, temp_project):
        """Test that only files are included, not empty directory entries."""
        # Create an empty directory
        empty_dir = temp_project / "empty"
        empty_dir.mkdir()

        zip_bytes = create_project_zip(temp_project)

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            # All entries should be files (not end with /)
            for name in zf.namelist():
                assert not name.endswith("/")

    def test_handles_empty_project(self):
        """Test that empty project directory produces empty zip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            zip_bytes = create_project_zip(project_dir)

            # Should still be a valid zip
            with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
                assert len(zf.namelist()) == 0


class TestSubmitTestsAsync:
    """Test suite for submit_tests_async function."""

    @patch("ci_client.client.requests.post")
    def test_successful_submission(self, mock_post):
        """Test successful async job submission."""
        # Mock successful response
        mock_response = Mock()
        mock_response.json.return_value = {"job_id": "test-job-123"}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "requirements.txt").write_text("pytest\n")

            job_id = submit_tests_async(project_dir, "http://test-server:8000")

            assert job_id == "test-job-123"
            mock_post.assert_called_once()
            # Verify correct endpoint
            args, _ = mock_post.call_args
            assert args[0] == "http://test-server:8000/submit-async"

    @patch("ci_client.client.requests.post")
    def test_network_error_raises_exception(self, mock_post):
        """Test that network errors are converted to RuntimeError."""
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection failed")

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            with pytest.raises(RuntimeError, match="Error submitting to CI server"):
                submit_tests_async(project_dir)

    @patch("ci_client.client.requests.post")
    def test_http_error_raises_exception(self, mock_post):
        """Test that HTTP errors are converted to RuntimeError."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "500 Server Error"
        )
        mock_post.return_value = mock_response

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            with pytest.raises(RuntimeError, match="Error submitting to CI server"):
                submit_tests_async(project_dir)


class TestListJobs:
    """Test suite for list_jobs function."""

    @patch("ci_client.client.requests.get")
    def test_successful_list(self, mock_get):
        """Test successful job listing."""
        mock_response = Mock()
        mock_response.json.return_value = [
            {
                "job_id": "job-1",
                "status": "completed",
                "success": True,
                "start_time": "2024-01-15T10:00:00Z",
                "end_time": "2024-01-15T10:01:00Z",
            },
            {
                "job_id": "job-2",
                "status": "running",
                "success": None,
                "start_time": "2024-01-15T10:02:00Z",
                "end_time": None,
            },
        ]
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        jobs = list_jobs("http://test-server:8000")

        assert len(jobs) == 2
        assert jobs[0]["job_id"] == "job-1"
        assert jobs[1]["job_id"] == "job-2"
        mock_get.assert_called_once_with("http://test-server:8000/jobs", headers={}, timeout=10)

    @patch("ci_client.client.requests.get")
    def test_network_error_raises_exception(self, mock_get):
        """Test that network errors are converted to RuntimeError."""
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection failed")

        with pytest.raises(RuntimeError, match="Error fetching jobs from CI server"):
            list_jobs()


class TestWaitForJob:
    """Test suite for wait_for_job function."""

    @patch("ci_client.client.requests.get")
    def test_streams_job_events(self, mock_get):
        """Test that wait_for_job streams events correctly."""
        # Mock SSE response
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.iter_lines.return_value = [
            'data: {"type": "log", "data": "Starting tests\\n"}',
            'data: {"type": "log", "data": "test_example.py PASSED\\n"}',
            'data: {"type": "complete", "success": true}',
        ]
        mock_get.return_value = mock_response

        events = list(wait_for_job("test-job-123", "http://test-server:8000"))

        assert len(events) == 3
        # Note: JSON parsing converts \\n escape sequence to actual newline
        assert events[0] == {"type": "log", "data": "Starting tests\n"}
        assert events[1] == {"type": "log", "data": "test_example.py PASSED\n"}
        assert events[2] == {"type": "complete", "success": True}

        # Verify correct endpoint and params
        args, kwargs = mock_get.call_args
        assert args[0] == "http://test-server:8000/jobs/test-job-123/stream"
        assert kwargs["stream"] is True
        assert "from_beginning" not in kwargs.get("params", {})

    @patch("ci_client.client.requests.get")
    def test_from_beginning_parameter(self, mock_get):
        """Test that from_beginning=True is passed as parameter."""
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.iter_lines.return_value = [
            'data: {"type": "complete", "success": true}',
        ]
        mock_get.return_value = mock_response

        list(wait_for_job("test-job-123", from_beginning=True))

        # Verify from_beginning param is passed
        _, kwargs = mock_get.call_args
        assert kwargs["params"] == {"from_beginning": True}

    @patch("ci_client.client.requests.get")
    def test_skips_empty_lines(self, mock_get):
        """Test that empty lines and non-data lines are skipped."""
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.iter_lines.return_value = [
            "",  # Empty line
            'data: {"type": "log", "data": "Test output\\n"}',
            "",  # Another empty line
            ": comment line",  # SSE comment
            'data: {"type": "complete", "success": true}',
        ]
        mock_get.return_value = mock_response

        events = list(wait_for_job("test-job-123"))

        # Should only have 2 events (empty lines and comments skipped)
        assert len(events) == 2
        assert events[0]["type"] == "log"
        assert (
            events[0]["data"] == "Test output\n"
        )  # Verify newline is parsed correctly
        assert events[1]["type"] == "complete"

    @patch("ci_client.client.requests.get")
    def test_network_error_yields_error_event(self, mock_get):
        """Test that network errors yield error events instead of raising."""
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection failed")

        events = list(wait_for_job("test-job-123"))

        # Should yield error log and failure complete
        assert len(events) == 2
        assert events[0]["type"] == "log"
        assert "Error waiting for job" in events[0]["data"]
        assert events[1] == {"type": "complete", "success": False}


class TestSubmitTestsStreaming:
    """Test suite for submit_tests_streaming function."""

    @patch("ci_client.client.requests.post")
    def test_streams_events(self, mock_post):
        """Test that submit_tests_streaming yields SSE events."""
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.iter_lines.return_value = [
            'data: {"type": "job_id", "job_id": "test-job-456"}',
            'data: {"type": "log", "data": "Running tests\\n"}',
            'data: {"type": "complete", "success": true}',
        ]
        mock_post.return_value = mock_response

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "requirements.txt").write_text("pytest\n")

            events = list(submit_tests_streaming(project_dir))

            assert len(events) == 3
            assert events[0]["type"] == "job_id"
            assert events[1]["type"] == "log"
            assert events[2]["type"] == "complete"

            # Verify endpoint
            args, kwargs = mock_post.call_args
            assert args[0].endswith("/submit-stream")
            assert kwargs["stream"] is True

    @patch("ci_client.client.requests.post")
    def test_network_error_yields_error_events(self, mock_post):
        """Test that network errors yield error events instead of raising."""
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection failed")

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            events = list(submit_tests_streaming(project_dir))

            # Should yield error log and failure complete
            assert len(events) == 2
            assert events[0]["type"] == "log"
            assert "Error submitting to CI server" in events[0]["data"]
            assert events[1] == {"type": "complete", "success": False}
