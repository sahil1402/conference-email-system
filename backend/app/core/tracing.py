"""Per-email pipeline tracing (Phase 5A observability).

Emits one JSON log line per pipeline stage (classify → retrieve → route →
draft) so an email's journey through the pipeline can be reconstructed
end-to-end. The trace record is deliberately narrow:

    {timestamp, email_id, stage, input_summary, output_summary, duration_ms}

Design constraints:
- Additive / side-effect only. Tracing never changes a pipeline module's
  inputs, outputs, or return types, and a tracing failure must never break the
  pipeline (every emit is best-effort).
- Stdlib only. Uses ``logging`` + a ``RotatingFileHandler`` writing JSONL to
  ``backend/logs/pipeline_trace.jsonl`` — no new dependency.
- Never logs the draft text itself (only its length), so the trace file is safe
  to retain and inspect.

The email's database id is only assigned at persistence time, *after* the
stages run. So a ``PipelineTracer`` buffers each stage's record during
processing and ``flush(email_id)`` writes them all out once the id is known,
preserving stage order.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator

# tracing.py lives at backend/app/core/ → parents[2] is the backend/ dir.
_DEFAULT_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "pipeline_trace.jsonl"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3

# The ordered pipeline stages a full run emits.
STAGES = ("classifier", "retriever", "router", "drafter")

# Dedicated logger — isolated from the app's root logging so trace lines never
# leak into normal application logs and vice versa.
_trace_logger = logging.getLogger("confmail.pipeline_trace")
_trace_logger.setLevel(logging.INFO)
_trace_logger.propagate = False

_current_log_path: Path = _DEFAULT_LOG_PATH

logger = logging.getLogger(__name__)


class _JsonTraceFormatter(logging.Formatter):
    """Render a trace LogRecord as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": getattr(record, "trace_timestamp", None)
            or datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "email_id": getattr(record, "email_id", None),
            "stage": getattr(record, "stage", None),
            "input_summary": getattr(record, "input_summary", None),
            "output_summary": getattr(record, "output_summary", None),
            "duration_ms": getattr(record, "duration_ms", None),
        }
        return json.dumps(payload, ensure_ascii=False)


def _install_handler(path: Path) -> None:
    """(Re)install the rotating JSONL handler pointed at ``path``."""
    for handler in list(_trace_logger.handlers):
        _trace_logger.removeHandler(handler)
        handler.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(_JsonTraceFormatter())
    _trace_logger.addHandler(handler)


_install_handler(_current_log_path)


def configure_tracing(log_path: str | Path) -> None:
    """Point tracing at a different log file (used by tests for isolation)."""
    global _current_log_path
    _current_log_path = Path(log_path)
    _install_handler(_current_log_path)


def log_trace(
    email_id: str,
    stage: str,
    input_summary: Any,
    output_summary: Any,
    duration_ms: float,
    timestamp: str | None = None,
) -> None:
    """Emit one trace line. Best-effort: never raises into the caller."""
    try:
        _trace_logger.info(
            "pipeline_stage",
            extra={
                "email_id": str(email_id),
                "stage": stage,
                "input_summary": input_summary,
                "output_summary": output_summary,
                "duration_ms": duration_ms,
                "trace_timestamp": timestamp,
            },
        )
    except Exception:  # noqa: BLE001 - tracing must never break the pipeline
        logger.warning("Trace emit failed (%s/%s).", email_id, stage, exc_info=True)


class _StageHolder:
    """Mutable slot a traced stage sets its output summary on."""

    __slots__ = ("output_summary",)

    def __init__(self) -> None:
        self.output_summary: Any = None


class PipelineTracer:
    """Buffers per-stage trace records, then flushes them under one email id.

    Stages run before the email is persisted (and thus before its id exists),
    so records are held in memory and written together by ``flush``.
    """

    def __init__(self) -> None:
        self._buffer: list[dict] = []

    @contextmanager
    def stage(self, stage: str, input_summary: Any) -> Iterator[_StageHolder]:
        """Time a pipeline stage and buffer its trace record.

        Set ``holder.output_summary`` inside the ``with`` block to capture the
        stage's output. The record is buffered on exit — even if the stage
        raises — so timing/inputs are always recorded; ``flush`` writes it.
        """
        start = time.perf_counter()
        holder = _StageHolder()
        try:
            yield holder
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000.0, 3)
            self._buffer.append(
                {
                    "stage": stage,
                    "input_summary": input_summary,
                    "output_summary": holder.output_summary,
                    "duration_ms": duration_ms,
                    "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                }
            )

    def flush(self, email_id: str) -> None:
        """Write all buffered records under ``email_id`` (in stage order)."""
        for entry in self._buffer:
            log_trace(email_id=email_id, **entry)
        self._buffer.clear()


def read_traces(email_id: str) -> list[dict]:
    """Return all trace records for ``email_id``, oldest first.

    Reads the current log file plus any rotated backups (chronological order),
    filtering to the requested email. Malformed lines are skipped.
    """
    target = str(email_id)
    entries: list[dict] = []

    paths: list[Path] = []
    # Rotated backups are named ``<file>.1`` (newest backup) .. ``.N`` (oldest);
    # read oldest → newest → current so records stay chronological.
    for i in range(_BACKUP_COUNT, 0, -1):
        backup = _current_log_path.with_name(f"{_current_log_path.name}.{i}")
        if backup.exists():
            paths.append(backup)
    if _current_log_path.exists():
        paths.append(_current_log_path)

    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if str(record.get("email_id")) == target:
                        entries.append(record)
        except OSError:
            continue

    return entries
