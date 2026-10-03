#!/usr/bin/env bash
# Run a command (or a shell) in the IIC-OSIC-TOOLS container, repo root mounted.
#
# A convenience runtime, NOT the reference: evidence is produced under the nix
# toolchain (`nix develop .#sim`, docs/reproducing.md). The container's signoff
# tools are other versions (2026.09: Magic 8.3.684, KLayout 0.30.12, Netgen
# 1.5.323, Yosys 0.69, Verilator 5.052 -- nix-eda 6.11 has 8.3.623, 0.30.7,
# 1.5.316, 0.62, and the sims pin 5.050). So the container exports
# MOSAIC_TOOLCHAIN=iic:<tag>: `mosaic doctor` reports it and the evidence
# store keys any run recorded there apart from nix-made runs.
#
#   tools/iic-osic.sh                       interactive shell (needs a TTY)
#   tools/iic-osic.sh klayout run.gds       one command
#
#   IIC_TAG     image tag (default 2026.09; pinned -- never `latest`)
#   EXTRA_VOLS  extra `docker run` volume arguments
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${IIC_TAG:-2026.09}"
IMAGE="hpretl/iic-osic-tools:$TAG"

command -v docker >/dev/null || { echo "docker not found in PATH" >&2; exit 1; }

# --user: files the container writes into the repo stay owned by you.
ARGS=(--rm --user "$(id -u):$(id -g)" -v "$REPO:/workspace" -w /workspace
      -e "MOSAIC_TOOLCHAIN=iic:$TAG" -e IIC_OSIC_TOOLS_QUIET=1)
# -it only with a terminal: agents and CI have none, and `-t` without one fails.
if [ -t 0 ] && [ -t 1 ]; then ARGS+=(-it); fi
if [ -n "${DISPLAY:-}" ] && [ -S /tmp/.X11-unix ]; then
  ARGS+=(-e "DISPLAY=$DISPLAY" -v /tmp/.X11-unix:/tmp/.X11-unix:rw)
fi
# shellcheck disable=SC2206  # EXTRA_VOLS is deliberately word-split
[ -n "${EXTRA_VOLS:-}" ] && ARGS+=($EXTRA_VOLS)

if [ $# -eq 0 ]; then
  exec docker run "${ARGS[@]}" "$IMAGE" --skip bash
fi
exec docker run "${ARGS[@]}" "$IMAGE" --skip "$@"
