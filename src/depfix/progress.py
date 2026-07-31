"""Secret-safe progress reporting for interactive preparation work."""

from __future__ import annotations

import sys
from threading import Lock
from typing import TextIO

from .errors import redact

_LEVELS = {
    "TRACE": 5,
    "DEBUG": 10,
    "INFO": 20,
    "WARN": 30,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
    "OFF": 100,
}
_OUTPUT_LOCK = Lock()


class ProgressReporter:
    """Write stable progress lines without taking ownership of application logging."""

    def __init__(self, log_level: str, *, stream: TextIO | None = None) -> None:
        self.level = _LEVELS.get(log_level.strip().upper(), _LEVELS["WARNING"])
        self.stream = stream

    @property
    def enabled(self) -> bool:
        return self.level <= _LEVELS["INFO"]

    def emit(self, action: str, detail: str) -> None:
        if not self.enabled:
            return
        output = self.stream or sys.stderr
        with _OUTPUT_LOCK:
            print(f"depfix  {action:<8} {redact(detail)}", file=output, flush=True)

    def forward_uv(self, stdout: str, stderr: str) -> None:
        """Forward uv's completed summary to stderr without leaking credentials."""
        if not self.enabled:
            return
        for chunk in (stdout, stderr):
            for raw_line in chunk.splitlines():
                line = raw_line.strip()
                if line:
                    self.emit("uv", line)
