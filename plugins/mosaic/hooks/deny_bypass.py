#!/usr/bin/env python3
"""PreToolUse hook: keep the model on the gated MCP tools.

In plugin mode the model still has the host's shell and editors. Two things
would let it walk around the gates, so both are denied:
  1. running the harness CLI (`mosaic ...`, `python -m harness ...`),
     which reaches the same flows with no AgentState in front of them, and
     includes `mosaic approve`;
  2. touching the approval tokens or the harness user config directory.

Exit 2 blocks the call and hands stderr to the model. This is a guard for a
cooperative model, not a sandbox: see harness/approvals.py for what a shell
running as the user can still do, and `mosaic agent --driver claude` for
the session that has no shell at all.
"""

import json
import os
import re
import sys

CONFIG_DIR = os.environ.get(
    "MOSAIC_CONFIG_DIR",
    os.path.join(os.path.expanduser("~"), ".config", "mosaic"))

HARNESS_CLI = re.compile(
    r"(?:^|[\s;&|(`$])(?:\S*/)?mosaic(?=\s|$)"      # mosaic / ./mosaic
    r"|\bpython[0-9.]*\b[^;&|\n]*?\s-m\s*harness\b"     # python -m harness
    r"|\bharness/__main__\.py\b"
    r"|\brunpy\b[^;&|\n]*\bharness\b"
)
PROTECTED = re.compile(r"mosaic/approvals|\.config/mosaic")


def verdict(payload: dict):
    tool = payload.get("tool_name", "")
    data = payload.get("tool_input") or {}
    if tool == "Bash":
        command = str(data.get("command", ""))
        if HARNESS_CLI.search(command):
            return ("the mosaic CLI bypasses the session gates; use the "
                    "mosaic MCP tools (session_new, request_scope, ...)")
        if PROTECTED.search(command) or CONFIG_DIR in command:
            return "mosaic approvals and config are for the user to change"
        return None
    path = str(data.get("file_path") or data.get("notebook_path") or "")
    if path:
        real = os.path.realpath(os.path.expanduser(path))
        if real.startswith(os.path.realpath(CONFIG_DIR) + os.sep) or \
                PROTECTED.search(real):
            return "mosaic approvals and config are for the user to change"
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    reason = verdict(payload)
    if reason is None:
        return 0
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
