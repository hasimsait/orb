import os
import sys
import tempfile
import pytest

# Ensure parent directory is in sys.path so orb modules can be imported directly
PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)


@pytest.fixture
def temp_log_file(tmp_path):
    """Provides a temporary log file path and cleans up ORB_LOG_FILE env var after test."""
    log_file = tmp_path / "test_orb.log"
    old_log_file = os.environ.get("ORB_LOG_FILE")
    old_orb_log = os.environ.get("ORB_LOG")

    os.environ["ORB_LOG_FILE"] = str(log_file)
    yield log_file

    # Restore environment
    if old_log_file is not None:
        os.environ["ORB_LOG_FILE"] = old_log_file
    else:
        os.environ.pop("ORB_LOG_FILE", None)

    if old_orb_log is not None:
        os.environ["ORB_LOG"] = old_orb_log
    else:
        os.environ.pop("ORB_LOG", None)


@pytest.fixture
def sample_files(tmp_path):
    """Creates temporary sample files for testing @filepath expansion."""
    small_file = tmp_path / "small.txt"
    small_file.write_text("Hello from small file!", encoding="utf-8")

    large_file = tmp_path / "large.txt"
    # Create content larger than 102,400 bytes (100KB)
    large_content = "X" * 150000
    large_file.write_text(large_content, encoding="utf-8")

    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()
    nested_file = nested_dir / "nested.py"
    nested_file.write_text("print('nested')", encoding="utf-8")

    return {
        "small": small_file,
        "large": large_file,
        "nested": nested_file,
        "dir": tmp_path,
    }
