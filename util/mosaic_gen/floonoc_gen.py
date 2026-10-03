#!/usr/bin/env python3

"""floonoc_gen.py — floogen topology emitter for the MOSAIC `bus: floonoc` fabric.

Builds the floogen YAML for the compact MOSAIC topology (one router;
one AXI manager endpoint per hart with instr+data merged upstream, one shared
manager endpoint for debug+DMA, and two subordinate endpoints: `mem`
([0, MEM_SIZE)) and `periph` (everything above)), then runs floogen to
generate `floo_mosaic_noc_pkg.sv` + `floo_mosaic_noc.sv` into
``hw/ip/floonoc_fabric/``.

For non-floonoc buses it writes minimal stub files with the same names so the
checked-in FuseSoC core (mosaic:ip:floonoc_fabric) always resolves.

`build_topology()` is the single source of truth for the topology — the
mosaic topo-viz skill renders from the same dict.
"""

import logging
import shutil
import subprocess
from pathlib import Path

NOC_NAME = "mosaic"
FABRIC_DIR = "hw/ip/floonoc_fabric"

STUB_HEADER = (
    "// GENERATED STUB — the active config does not use `bus: floonoc`.\n"
    "// The real content is produced by floogen via util/mosaic_gen/floonoc_gen.py\n"
    "// when a floonoc config is generated. Do not edit, do not commit.\n"
)


def build_topology(num_harts: int, mem_size: int, route_algo: str = "ID") -> dict:
    """The compact MOSAIC floogen topology as a plain dict (YAML-ready).

    Managers: hart0..hartN (per-hart instr+data merged upstream by an
    n-to-one) and `shared` (debug + all DMA ports merged). Subordinates:
    `mem` = [0, mem_size), `periph` = everything above (the tier demux in
    front of the fabric already peeled off the EXT space).
    """
    if route_algo != "ID":
        raise ValueError(
            "compact MOSAIC FlooNoC has one router and supports only ID routing"
        )

    endpoints = []
    for h in range(num_harts):
        endpoints.append(
            {
                "name": f"hart{h}",
                "mgr_port_protocol": ["axi_in"],
            }
        )
    endpoints.append(
        {
            "name": "shared",
            "mgr_port_protocol": ["axi_in"],
        }
    )
    endpoints.append(
        {
            "name": "mem",
            "addr_range": {"base": 0x0000_0000, "size": mem_size},
            "sbr_port_protocol": ["axi_out"],
        }
    )
    endpoints.append(
        {
            "name": "periph",
            "addr_range": {"start": mem_size, "end": 0xFFFF_FFFF},
            "sbr_port_protocol": ["axi_out"],
        }
    )

    return {
        "name": NOC_NAME,
        "description": "MOSAIC-SoC compact FlooNoC fabric (single router)",
        "network_type": "axi",
        # A single router has no geometry: decode by ID table.
        "routing": {"route_algo": "ID", "use_id_table": True},
        "protocols": [
            {
                "name": "axi_in",
                "protocol": "AXI4",
                "data_width": 32,
                "addr_width": 32,
                "id_width": 2,
                "user_width": 1,
            },
            {
                "name": "axi_out",
                "protocol": "AXI4",
                "data_width": 32,
                "addr_width": 32,
                "id_width": 2,
                "user_width": 1,
            },
        ],
        "endpoints": endpoints,
        "routers": [{"name": "router"}],
        "connections": [{"src": ep["name"], "dst": "router"} for ep in endpoints],
    }


def _write_stubs(outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"floo_{NOC_NAME}_noc_pkg.sv").write_text(
        STUB_HEADER + f"package floo_{NOC_NAME}_noc_pkg;\nendpackage\n"
    )
    (outdir / f"floo_{NOC_NAME}_noc.sv").write_text(
        STUB_HEADER + f"module floo_{NOC_NAME}_noc;\nendmodule\n"
    )


def _patch_router_map(top_file: Path, num_endpoints: int) -> None:
    """Widen the generated router ID table to the full identity map.

    floogen's gen_router_tables emits ID-table rules only for SUBORDINATE
    endpoints (network.py filters ni.is_sbr()), so response flits destined
    for manager-only endpoints (our hart/shared chimneys) miss the table and
    the router's addr_decode falls through to port 0 — every non-hart0
    response is silently misdelivered to hart0. In the MOSAIC single-router
    topology router port i == endpoint id i, so the identity map is correct
    for both the request and response planes.
    """
    src = top_file.read_text()

    start = src.index("localparam int unsigned RouterMapNumRules")
    end = src.index("};", start) + 2
    rules = ",\n".join(
        f"'{{    idx: {i},\n    start_addr: {i},\n    end_addr: {i + 1}}}"
        for i in reversed(range(num_endpoints))
    )
    block = (
        "// Patched by floonoc_gen.py: full identity map (see _patch_router_map)\n"
        f"localparam int unsigned RouterMapNumRules = {num_endpoints};\n\n"
        "localparam route_map_rule_t[RouterMapNumRules-1:0] RouterMap = '{\n"
        f"{rules}\n\n}};"
    )
    src = src[:start] + block + src[end:]

    # The router instantiation carries the rule count as a literal too.
    src = src.replace(".NumAddrRules (2)", f".NumAddrRules ({num_endpoints})")

    top_file.write_text(src)


def _find_floogen(repo_root: Path) -> str:
    venv_floogen = repo_root / ".venv" / "bin" / "floogen"
    if venv_floogen.exists():
        return str(venv_floogen)
    found = shutil.which("floogen")
    if found:
        return found
    raise RuntimeError(
        "floogen not found — install it into the project venv with "
        "`make venv` (it installs the pinned FlooNoC generator)"
    )


def generate(
    cfg,
    num_harts: int,
    mem_size: int,
    repo_root,
    *,
    output_dir=None,
    work_dir=None,
) -> None:
    """Emit the fabric files for the active config (real NoC or stubs).

    :param cfg: parsed MosaicConfig (bus/bus_opts fields used)
    :param num_harts: total hart count
    :param mem_size: total RAM size in bytes (the mem endpoint range)
    :param repo_root: repository root path
    :param output_dir: optional isolated output directory.  When omitted, keep
        the historical template-adjacent location for compatibility.
    :param work_dir: optional directory for the generated floogen YAML.
    """
    import yaml

    repo_root = Path(repo_root)
    outdir = Path(output_dir) if output_dir is not None else repo_root / FABRIC_DIR

    if getattr(cfg, "bus", "obi") != "floonoc":
        _write_stubs(outdir)
        return

    route_algo = (cfg.bus_opts or {}).get("floonoc", {}).get("route_algo", "ID")
    topo = build_topology(num_harts, mem_size, route_algo)

    cfg_dir = (
        Path(work_dir) if work_dir is not None else repo_root / "build" / "floonoc"
    )
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "mosaic_noc.yml"
    cfg_file.write_text(yaml.safe_dump(topo, sort_keys=False))

    floogen = _find_floogen(repo_root)
    cmd = [floogen, "rtl", "-c", str(cfg_file), "-o", str(outdir), "--no-format"]
    logging.info("[MOSAIC] running floogen: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"floogen failed (exit {result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    num_endpoints = num_harts + 3  # harts + shared + mem + periph
    _patch_router_map(outdir / f"floo_{NOC_NAME}_noc.sv", num_endpoints)
    logging.info(
        "[MOSAIC] floogen generated %s and %s (router map patched to %d rules)",
        outdir / f"floo_{NOC_NAME}_noc_pkg.sv",
        outdir / f"floo_{NOC_NAME}_noc.sv",
        num_endpoints,
    )
