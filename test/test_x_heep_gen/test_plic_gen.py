from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util/xheep_gen"))

from plic_gen import generate


def test_configuration_sized_plic_has_one_context_per_hart(tmp_path):
    outputs = generate(4, REPO_ROOT, tmp_path / "rtl", tmp_path / "work")
    assert len(outputs) == 3

    package = (tmp_path / "rtl/rv_plic_reg_pkg.sv").read_text()
    register_top = (tmp_path / "rtl/rv_plic_reg_top.sv").read_text()
    plic = (tmp_path / "rtl/rv_plic.sv").read_text()

    assert "parameter int NumTarget = 4;" in package
    assert "rv_plic_reg2hw_ie3_mreg_t" in package
    assert "msip3" in package
    assert "gen_target" in plic
    assert "reg2hw.ie3" in plic
    assert "msip3" in register_top


@pytest.mark.parametrize("targets", [0, 17, True])
def test_plic_target_bounds_fail_closed(tmp_path, targets):
    with pytest.raises(ValueError, match="1..16"):
        generate(targets, REPO_ROOT, tmp_path / "rtl", tmp_path / "work")

