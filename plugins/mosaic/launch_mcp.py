#!/usr/bin/env python3
"""Start the mosaic MCP server from the user's MOSAIC checkout.

A plugin host copies this directory into its own cache, so the plugin must
not carry the harness: it would run stale code against the wrong tree. This
launcher finds the checkout (MOSAIC_REPO, else the first directory at or
above the working directory that looks like one) and execs that checkout's
own interpreter and harness. Extra arguments pass through to `mcp-server`.
"""

import os
import sys
from pathlib import Path


def is_checkout(path: Path) -> bool:
    return ((path / "util" / "mosaic_gen" / "core_registry.py").is_file()
            and (path / "configs").is_dir())


def find_checkout() -> Path:
    pinned = os.environ.get("MOSAIC_REPO")
    if pinned:
        return Path(pinned).expanduser().resolve()
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if is_checkout(candidate):
            return candidate
    raise SystemExit(
        "mosaic: no MOSAIC checkout at or above "
        f"{cwd}; open the host inside one or set MOSAIC_REPO")


def main() -> None:
    repo = find_checkout()
    if not is_checkout(repo):
        raise SystemExit(f"mosaic: {repo} is not a MOSAIC checkout")
    venv = repo / ".venv" / "bin" / "python"
    python = str(venv) if venv.is_file() else sys.executable
    env = dict(os.environ, MOSAIC_REPO=str(repo), PYTHONPATH=str(repo))
    os.chdir(repo)
    os.execve(python, [python, "-m", "harness", "mcp-server", *sys.argv[1:]],
              env)


if __name__ == "__main__":
    main()
