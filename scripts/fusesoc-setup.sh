#!/bin/bash
# Resolve and export the x-heep/MOSAIC FuseSoC graph.
#
# With --manifest, this consumes the isolated generated-file overlay and the
# per-config core flags emitted by mcu_gen.py.  Every invocation gets a unique
# run directory, avoiding both the old global /tmp core-root race and build
# products shared by concurrent configurations.  Without a manifest the
# historical in-place x-heep flow is retained.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="${REPO_ROOT}/.venv/bin/python"
FUSESOC="${REPO_ROOT}/.venv/bin/fusesoc"
MANIFEST="${MOSAIC_MANIFEST:-}"

usage() {
    echo "Usage: $0 [--manifest PATH]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --manifest)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            MANIFEST="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ ! -x "$FUSESOC" ]; then
    echo "ERROR: fusesoc not found at $FUSESOC" >&2
    echo "Install with: pip install git+https://github.com/x-heep/fusesoc.git@ot" >&2
    exit 1
fi

if [ ! -x "$VENV_PY" ]; then
    echo "ERROR: project Python not found at $VENV_PY" >&2
    exit 1
fi

# Export tool paths for FuseSoC generators.
export REGTOOL="${REGTOOL:-${REPO_ROOT}/hw/vendor/pulp_platform/register_interface/vendor/lowrisc_opentitan/util/regtool.py}"
export PERIPH_STRUCTS_GEN="${PERIPH_STRUCTS_GEN:-${REPO_ROOT}/util/periph_structs_gen/periph_structs_gen.py}"
export TEMPLATE_FILE="${TEMPLATE_FILE:-${REPO_ROOT}/util/periph_structs_gen/periph_structs.tpl}"

FUSESOC_ARGS=()
if [ -n "$MANIFEST" ]; then
    MANIFEST="$($VENV_PY -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "$MANIFEST")"
    if [ ! -f "$MANIFEST" ]; then
        echo "ERROR: MOSAIC manifest not found: $MANIFEST" >&2
        exit 1
    fi
    BUNDLE_DIR="$($VENV_PY -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundle_dir"])' "$MANIFEST")"
    mkdir -p "$BUNDLE_DIR/runs"
    RUN_ROOT="$(mktemp -d "$BUNDLE_DIR/runs/fusesoc.XXXXXX")"
    FUSESOC_ROOT="$RUN_ROOT/core-root"
    BUILD_ROOT="$RUN_ROOT/build"
    "$VENV_PY" "$REPO_ROOT/util/xheep_gen/build_manifest.py" stage \
        --manifest "$MANIFEST" --output "$FUSESOC_ROOT" >/dev/null
    while IFS= read -r flag; do
        [ -n "$flag" ] && FUSESOC_ARGS+=(--flag "$flag")
    done < <("$VENV_PY" "$REPO_ROOT/util/xheep_gen/build_manifest.py" flags --manifest "$MANIFEST")
else
    # Compatibility mode: generated RTL is beside its templates, as in x-heep.
    RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mosaic-fusesoc.XXXXXX")"
    FUSESOC_ROOT="$RUN_ROOT/core-root"
    # Match FuseSoC's historical default layout so existing Makefile targets
    # still find build/<VLNV>/sim-verilator in compatibility mode.
    BUILD_ROOT="$REPO_ROOT/build/openhwgroup.org_systems_core-v-mini-mcu_1.0.5"
    mkdir -p "$FUSESOC_ROOT"
    cp "$REPO_ROOT/core-v-mini-mcu.core" "$FUSESOC_ROOT/"
    cp "$REPO_ROOT/waiver_v5.core" "$FUSESOC_ROOT/"
    for directory in hw tb util configs sw flow scripts; do
        [ ! -e "$REPO_ROOT/$directory" ] || ln -s "$REPO_ROOT/$directory" "$FUSESOC_ROOT/$directory"
    done
fi

echo "MOSAIC-SoC FuseSoC setup"
echo "  REGTOOL:           $REGTOOL"
echo "  PERIPH_STRUCTS_GEN: $PERIPH_STRUCTS_GEN"
echo "  TEMPLATE_FILE:     $TEMPLATE_FILE"
echo "  core root:         $FUSESOC_ROOT"
echo "  build root:        $BUILD_ROOT"
if [ -n "$MANIFEST" ]; then
    echo "  manifest:          $MANIFEST"
fi
echo

mkdir -p "$BUILD_ROOT"
"$FUSESOC" --cores-root "$FUSESOC_ROOT" run \
    --build-root "$BUILD_ROOT" \
    --target=sim --tool=verilator --setup \
    "${FUSESOC_ARGS[@]}" \
    openhwgroup.org:systems:core-v-mini-mcu

# This x-heep FuseSoC fork can return zero after dependency-resolution errors.
# A successful setup always emits the backend command file; treat its absence
# as failure so callers never continue with a stale/shared export.
if ! find "$BUILD_ROOT" -type f -name '*.vc' -path '*sim-verilator*' -print -quit | grep -q .; then
    echo "ERROR: FuseSoC reported success but emitted no sim-verilator .vc" >&2
    exit 1
fi

echo
echo "FuseSoC setup completed successfully."
echo "FUSESOC_STAGE_ROOT=$FUSESOC_ROOT"
echo "FUSESOC_BUILD_ROOT=$BUILD_ROOT"
