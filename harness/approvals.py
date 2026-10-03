"""Human approval tokens: what a model can never grant itself.

A plugin host starts the MCP server before anyone has typed a request, so the
old rule "the ceiling is derived from the request on the command line" has no
request to derive from. The ceiling becomes a standing allowlist the user
writes into the host config (MOSAIC_SCOPES), and anything expensive or
tree-changing needs a second, time-limited yes from a person:

    mosaic approve physical --minutes 60

`approve` refuses without a real terminal on both stdin and stdout and makes
the user type a phrase back. An agent's shell tool has no TTY, so a model that
calls it is refused. The token is a small JSON file (mode 0600) with an expiry.

What needs a token (enforced in harness/gates.py and the registry):
  - selecting the `physical` or `integration` scope in an MCP session;
  - any flow whose FlowSpec declares approval=True, and a tb-matrix run above
    the validate tier -- the token for the session's CURRENT scope;
  - wrapper-smith apply -- the `integration` token.

In-process drivers (`agent --driver api|deterministic`) keep their TTY flags:
`--allow-physical` / `--allow-integration` are static grants, because the user
typed them. Tokens are consulted there too.

WHAT THIS DOES NOT STOP. A model that has a general shell as the same Unix user
can write this file directly. The Claude Code plugin's PreToolUse hook denies
shell and file writes that touch the approvals directory or the harness CLI,
and `mosaic agent --driver claude|omp` removes the shell entirely; those
are the enforced paths. A host that hands the model an unrestricted shell is
trusting the model with the user's account, and no file-based token changes
that.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import FrozenSet, Optional

#: Scopes whose selection needs a token in an MCP session.
APPROVAL_SCOPES = frozenset({"physical", "integration"})
MAX_MINUTES = 24 * 60


def approvals_dir() -> Path:
    from .skills.setup_wizard import CONFIG_DIR

    return CONFIG_DIR / "approvals"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def confirmation_phrase(scope: str, minutes: int) -> str:
    return f"approve {scope} for {minutes} minutes"


def write_token(scope: str, minutes: int,
                directory: Optional[Path] = None) -> Path:
    if not 1 <= minutes <= MAX_MINUTES:
        raise ValueError(f"--minutes must be 1..{MAX_MINUTES}")
    directory = directory or approvals_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    created = _now()
    payload = {
        "scope": scope,
        "created_at": created.isoformat(),
        "expires_at": (created + timedelta(minutes=minutes)).isoformat(),
        "granted_by": "tty",
    }
    path = directory / f"{scope}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(payload, stream, indent=2)
    os.chmod(path, 0o600)
    return path


def revoke(scope: str, directory: Optional[Path] = None) -> bool:
    path = (directory or approvals_dir()) / f"{scope}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def token_status(scope: str, directory: Optional[Path]) -> Optional[str]:
    """None when a valid token exists, otherwise why it does not count."""
    if directory is None:
        return "no approval directory configured"
    path = directory / f"{scope}.json"
    try:
        info = path.stat()
    except FileNotFoundError:
        return f"no approval token for {scope!r}"
    # Another account, or a world-writable token, is not this user's yes.
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        return f"approval token {path} has unsafe owner or permissions"
    try:
        payload = json.loads(path.read_text())
        created = datetime.fromisoformat(payload["created_at"])
        expires = datetime.fromisoformat(payload["expires_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return f"approval token {path} is unreadable"
    now = _now()
    if payload.get("scope") != scope:
        return f"approval token {path} names another scope"
    if created > now or expires - created > timedelta(minutes=MAX_MINUTES):
        return f"approval token {path} has an impossible lifetime"
    if expires <= now:
        return f"approval for {scope!r} expired at {expires.isoformat()}"
    return None


@dataclass(frozen=True)
class Approvals:
    """Static grants (TTY flags) plus, optionally, token files."""

    static: FrozenSet[str] = field(default_factory=frozenset)
    token_dir: Optional[Path] = None

    def refusal(self, scope: str) -> Optional[str]:
        if scope in self.static:
            return None
        return token_status(scope, self.token_dir)

    def granted(self, scope: str) -> bool:
        return self.refusal(scope) is None


def how_to_approve(scope: str) -> str:
    return (f"a person must run `mosaic approve {scope}` in a terminal; "
            "the model cannot grant this")


def cli_approve(scope: str, minutes: int, *, revoke_only: bool = False) -> int:
    """The `mosaic approve` command. Returns the exit status."""
    if revoke_only:
        removed = revoke(scope)
        print(f"{'revoked' if removed else 'no token for'} {scope}")
        return 0
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("approve needs an interactive terminal on stdin and stdout; "
              "an agent's shell tool cannot grant approval", file=sys.stderr)
        return 2
    phrase = confirmation_phrase(scope, minutes)
    print(f"This lets an agent session use the '{scope}' scope and its "
          f"approval-gated flows for {minutes} minutes.")
    print(f"Type exactly:  {phrase}")
    try:
        typed = input("> ").strip()
    except EOFError:
        typed = ""
    if typed != phrase:
        print("not approved (the phrase did not match)", file=sys.stderr)
        return 1
    path = write_token(scope, minutes)
    print(f"approved: {path}")
    return 0
