"""Generate a PLIC register/RTL context for every resolved MOSAIC hart.

OpenTitan's checked-in RV_PLIC snapshot has one target baked into generated
types and registers.  Merely widening the output port cannot make it a real
multi-hart PLIC.  This helper renders the upstream templates and runs the
vendored regtool in the configuration bundle, producing a self-consistent
``rv_plic.sv``, register package, and register top for exactly ``num_harts``.
"""

from pathlib import Path
import subprocess
import sys

from mako.template import Template

from build_manifest import atomic_write_text


LOGICAL_RTL_DIR = Path(
    "hw/vendor/lowrisc/opentitan/hw/ip/rv_plic/rtl"
)


def generate(num_harts: int, repo_root: Path, output_dir: Path, work_dir: Path):
    """Generate configuration-sized PLIC RTL and return the three output paths."""

    if type(num_harts) is not int or not 1 <= num_harts <= 16:
        raise ValueError(f"PLIC target count must be 1..16, got {num_harts!r}")

    repo_root = Path(repo_root).resolve()
    output_dir = Path(output_dir).resolve()
    work_dir = Path(work_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    plic_root = repo_root / "hw/vendor/lowrisc/opentitan/hw/ip/rv_plic"
    hjson_template = plic_root / "data/rv_plic.hjson.tpl"
    rtl_template = plic_root / "data/rv_plic.sv.tpl"
    regtool = repo_root / "hw/vendor/lowrisc/opentitan/util/regtool.py"

    context = {"src": 64, "target": num_harts, "prio": 7}
    hjson_text = Template(filename=str(hjson_template)).render_unicode(**context)
    hjson_path = work_dir / "rv_plic.hjson"
    atomic_write_text(hjson_path, hjson_text)

    subprocess.run(
        [sys.executable, str(regtool), "-r", "-t", str(output_dir), str(hjson_path)],
        cwd=repo_root,
        check=True,
        text=True,
        capture_output=True,
    )

    plic_rtl = Template(filename=str(rtl_template)).render_unicode(**context)
    plic_path = output_dir / "rv_plic.sv"
    atomic_write_text(plic_path, plic_rtl)

    outputs = (
        plic_path,
        output_dir / "rv_plic_reg_pkg.sv",
        output_dir / "rv_plic_reg_top.sv",
    )
    missing = [str(path) for path in outputs if not path.is_file()]
    if missing:
        raise RuntimeError("PLIC generation did not produce: " + ", ".join(missing))
    return outputs

