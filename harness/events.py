"""Observable agent events and an oh-my-pi-inspired terminal renderer.

The event stream is the stable contract. Human terminals get a compact live
transcript; automation can consume the same events as JSON Lines without
scraping ANSI output. Provider request/response envelopes and configured API
keys are never recorded, but prompts and tool output can contain user data.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import json
import os
import re
import sys
import time
from typing import Any, Callable, Deque, Dict, Optional, TextIO


@dataclass(frozen=True)
class AgentEvent:
    """One append-only observation from an agent session."""

    sequence: int
    kind: str
    message: str
    elapsed_s: float
    step: Optional[int] = None
    tool: Optional[str] = None
    status: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str, sort_keys=True)


class EventStream:
    """Sequence, record, and fan out session events."""

    def __init__(
        self,
        sink: Optional[Callable[[AgentEvent], None]] = None,
        *,
        max_in_memory: int = 2000,
    ):
        self._sink = sink
        self._started = time.monotonic()
        self._sequence = 0
        self.max_in_memory = max_in_memory
        self.events: Deque[AgentEvent] = deque(maxlen=max_in_memory)

    @property
    def event_count(self) -> int:
        return self._sequence

    def emit(
        self,
        kind: str,
        message: str,
        *,
        step: Optional[int] = None,
        tool: Optional[str] = None,
        status: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> AgentEvent:
        self._sequence += 1
        event = AgentEvent(
            sequence=self._sequence,
            kind=kind,
            message=message,
            elapsed_s=round(time.monotonic() - self._started, 3),
            step=step,
            tool=tool,
            status=status,
            details=details or {},
        )
        self.events.append(event)
        if self._sink is not None:
            self._sink(event)
        return event


class JsonlRenderer:
    """Machine-readable renderer for streaming integrations."""

    def __init__(self, stream: TextIO = sys.stdout):
        self.stream = stream

    def __call__(self, event: AgentEvent) -> None:
        try:
            self.stream.write(event.to_json() + "\n")
            self.stream.flush()
        except BrokenPipeError:
            if self.stream is sys.stdout:
                sys.stdout = open(os.devnull, "w")


class CompositeSink:
    def __init__(self, *sinks: Callable[[AgentEvent], None]):
        self.sinks = sinks

    def __call__(self, event: AgentEvent) -> None:
        for sink in self.sinks:
            sink(event)


class JsonlJournal:
    """Durable append-only session journal."""

    def __init__(self, path):
        from pathlib import Path

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        for existing in self.path.parent.glob("*.jsonl"):
            try:
                existing.chmod(0o600)
            except OSError:
                pass
        descriptor = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        os.chmod(self.path, 0o600)
        self._stream = os.fdopen(descriptor, "a", encoding="utf-8")

    def __call__(self, event: AgentEvent) -> None:
        self._stream.write(event.to_json() + "\n")
        self._stream.flush()

    def close(self) -> None:
        self._stream.close()


class TerminalRenderer:
    """Render decisions, tool calls, live output, and gates as they happen."""

    COLORS = {
        "cyan": "\033[36m",
        "blue": "\033[34m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "red": "\033[31m",
        "dim": "\033[2m",
        "bold": "\033[1m",
        "reset": "\033[0m",
    }

    def __init__(
        self,
        stream: TextIO = sys.stdout,
        *,
        color: Optional[bool] = None,
        show_output: bool = True,
    ):
        self.stream = stream
        is_tty = bool(getattr(stream, "isatty", lambda: False)())
        self.color = (
            is_tty and "NO_COLOR" not in os.environ if color is None else color
        )
        self.show_output = show_output
        self._assistant_open = False

    def _paint(self, text: str, color: str) -> str:
        if not self.color:
            return text
        return f"{self.COLORS[color]}{text}{self.COLORS['reset']}"

    def _write(self, text: str) -> None:
        self._close_assistant()
        try:
            self.stream.write(text + "\n")
            self.stream.flush()
        except BrokenPipeError:
            if self.stream is sys.stdout:
                sys.stdout = open(os.devnull, "w")

    def _close_assistant(self) -> None:
        if self._assistant_open:
            self.stream.write("\n")
            self.stream.flush()
            self._assistant_open = False

    def _clip(self, text: str, width: int = 240) -> str:
        if not self.color:
            text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        text = text.replace("\t", "    ")
        return text if len(text) <= width else text[: width - 1] + "…"

    def __call__(self, event: AgentEvent) -> None:
        if event.kind == "session_start":
            self._write(self._paint("╭─ oh-my-soc agent", "bold"))
            self._write(f"│  {event.message}")
            return
        if event.kind == "plan":
            self._write(self._paint("├─ plan", "blue"))
            for index, item in enumerate(event.details.get("steps", []), 1):
                self._write(f"│  {index}. {item}")
            return
        if event.kind in {"thinking", "decision"}:
            marker = "●" if event.kind == "decision" else "◇"
            self._write(
                f"{self._paint(marker, 'cyan')} "
                f"{self._clip(event.message)}"
            )
            return
        if event.kind == "tool_start":
            args = event.details.get("arguments")
            suffix = f" {json.dumps(args, default=str, sort_keys=True)}" if args else ""
            self._write(
                f"{self._paint('→', 'blue')} "
                f"{self._paint(event.tool or 'tool', 'bold')}{self._clip(suffix)}"
            )
            return
        if event.kind == "tool_output":
            if self.show_output and event.message:
                self._write(f"  {self._paint('│', 'dim')} {self._clip(event.message)}")
            return
        if event.kind in {"tool_end", "gate"}:
            ok = event.status == "ok"
            marker = self._paint("✓" if ok else "✗", "green" if ok else "red")
            label = f"{event.tool}: " if event.tool else ""
            self._write(f"{marker} {label}{self._clip(event.message)}")
            return
        if event.kind == "recovery":
            self._write(
                f"{self._paint('↻', 'yellow')} {self._clip(event.message)}"
            )
            return
        if event.kind == "assistant":
            for line in event.message.splitlines() or [""]:
                self._write(f"  {self._paint('│', 'cyan')} {self._clip(line)}")
            return
        if event.kind == "assistant_delta":
            try:
                if not self._assistant_open:
                    self.stream.write(f"  {self._paint('│', 'cyan')} ")
                    self._assistant_open = True
                self.stream.write(event.message)
                self.stream.flush()
            except BrokenPipeError:
                if self.stream is sys.stdout:
                    sys.stdout = open(os.devnull, "w")
            return
        if event.kind == "session_end":
            ok = event.status in {"ok", "verified"}
            color = "green" if ok else "red"
            marker = (
                "verified"
                if event.status == "verified"
                else "complete" if ok else "stopped"
            )
            self._write(self._paint(f"╰─ {marker} · {event.message}", color))
            return
        if event.kind == "error":
            self._write(self._paint(f"✗ {self._clip(event.message)}", "red"))
            return
        self._write(self._clip(event.message))
