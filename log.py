#!/usr/bin/env python3
"""Leveled logger for Orb.  Writes to a file, not the terminal.

Environment variables:
    ORB_LOG       – verbosity threshold: error | warn | debug | trace
                    Unset → only ERROR entries are recorded.
    ORB_LOG_FILE  – path to the log file (created or appended to).
                    Default: /tmp/orb.log

Usage:
    from log import log, ERROR, WARN, DEBUG, TRACE

    log(DEBUG, "session initialized")
    log(WARN, "exa MCP unavailable, falling back")
    log(ERROR, "connection refused")
    log(TRACE, f"raw SSE line: {line}")

Level hierarchy (lower value = higher priority):
    ERROR (0) – hard failures;  always written to file + echoed to stderr
    WARN  (1) – notable events; ORB_LOG in ('warn', 'debug', 'trace')
    DEBUG (2) – every step;     ORB_LOG in ('debug', 'trace')
    TRACE (3) – raw wire data;  ORB_LOG == 'trace' only
"""

import os
import sys
from datetime import datetime
from enum import Enum


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class Level(Enum):
    """Verbosity levels, ordered from most to least critical."""
    ERROR = 0
    WARN  = 1
    DEBUG = 2
    TRACE = 3


# Convenience aliases — import these to avoid writing Level.DEBUG at call sites
ERROR = Level.ERROR
WARN  = Level.WARN
DEBUG = Level.DEBUG
TRACE = Level.TRACE


# ---------------------------------------------------------------------------
# Internal state (resolved once at import time)
# ---------------------------------------------------------------------------

_ENV_MAP: dict[str, Level] = {
    "error": Level.ERROR,
    "warn":  Level.WARN,
    "debug": Level.DEBUG,
    "trace": Level.TRACE,
}

_LABELS: dict[Level, str] = {
    Level.ERROR: "ERROR",
    Level.WARN:  " WARN",
    Level.DEBUG: "DEBUG",
    Level.TRACE: "TRACE",
}

# Threshold: messages at or above (lower value) this level are written.
# Unset ORB_LOG → ERROR only.
_ACTIVE: Level = _ENV_MAP.get(os.environ.get("ORB_LOG", "").lower(), Level.ERROR)

# Log file path, overridable via ORB_LOG_FILE.
_LOG_PATH: str = os.environ.get("ORB_LOG_FILE", "/tmp/orb.log")

# Open once at import time in append mode, line-buffered so entries are
# flushed after every write without an explicit flush() call.
try:
    _fh = open(_LOG_PATH, "a", buffering=1, encoding="utf-8")
except OSError as _open_err:
    _fh = None
    print(f"[orb/log] Cannot open log file {_LOG_PATH!r}: {_open_err}",
          file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# log()
# ---------------------------------------------------------------------------

def log(level: Level, msg: str) -> None:
    """Write *msg* to the log file if *level* is within the active threshold.

    ERROR entries are additionally echoed to stderr so hard failures are always
    visible even when the caller is not watching the log file.
    """
    if _ACTIVE.value < level.value:
        return

    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]   # HH:MM:SS.mmm
    entry = f"{ts} [{_LABELS[level]}] {msg}\n"

    if _fh is not None:
        _fh.write(entry)

    # Hard failures are always echoed to stderr regardless of log level config
    if level is Level.ERROR:
        print(f"[ERROR] {msg}", file=sys.stderr, flush=True)
