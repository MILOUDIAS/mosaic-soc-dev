"""Emit a complete hardening config: invariant template plus derived floorplan.

Phase 1 measured the split this module depends on. Of the keys in Block A's
signoff config, 33 were byte-identical in Block B's and carried over untouched
onto a design they were never tuned for. Five are design-specific:
``DESIGN_NAME``, ``CLOCK_PERIOD``, ``FP_SIZING``, ``DIE_AREA``, ``CORE_AREA``.

So the template is a real file with its comments intact -- those comments
record what each PDN and timing value cost to learn, and regenerating them
through a YAML dumper would throw that away -- and only the five derived keys
are appended. The output is concatenation, the same shape ``run_signoff.sh``
already uses to append the resolved file list, so the derived block is visibly
separate from the reviewed one.

``CLOCK_PERIOD`` is derived even though both hardened designs happen to use
100 ns: they agree because both chose 10 MHz, not because the value is a
property of the flow. Treating a coincidence as an invariant is how a template
acquires a value nobody can justify.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, List, Optional, Tuple

from harness.physical.floorplan import (
    DEFAULT_MARGIN_UM,
    Floorplan,
    SocInput,
    clock_period_ns,
    derive_floorplan,
)

# Keys the template must NOT contain, because they are derived per design.
DERIVED_KEYS = ("DESIGN_NAME", "CLOCK_PERIOD", "FP_SIZING", "DIE_AREA",
                "CORE_AREA", "FP_DEF_TEMPLATE")

DEFAULT_TEMPLATE = "flow/librelane/signoff_template.yaml"

#: A template declares its process in a COMMENT, read from the raw text before
#: YAML parsing. A comment because the rendered file goes to LibreLane, which
#: has no key for this and should not be handed one it does not know.
TEMPLATE_TECHNOLOGY_MARKER = "# mosaic-technology:"

#: Assumed when a template carries no marker. Every template that predates the
#: marker is GF180's, so this preserves their meaning rather than making them
#: suddenly ambiguous.
#: The process the shared template was written for. Not decoration: the
#: template hardcodes GF180 corner names in `PNR_CORNERS`
#: (nom_tt_025C_5v00 / max_ss_125C_4v50 / min_ff_n40C_5v50) and a
#: `MAX_TRANSITION_CONSTRAINT` of 4 ns, which is GF180's typical-corner limit
#: at 5 V. IHP sg13g2 is a 1.2 V process whose corners are named
#: `typ_1p20V_25C`; neither value means anything there.
#:
#: Emitting this template for another process produced a config that named a
#: PDK nowhere, carried another process's corners, and would have been
#: hardened by run_signoff.sh against `--pdk gf180mcuD` regardless -- a run
#: that looks successful and measures the wrong process.
TEMPLATE_TECHNOLOGY = "gf180mcu"

#: Keys in the shared template whose values are process-specific.
PROCESS_SPECIFIC_KEYS = ("PNR_CORNERS", "MAX_TRANSITION_CONSTRAINT")

_MODULE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def die_side_from_def(def_path: Path) -> Optional[float]:
    """The DIEAREA edge length in microns, or None if the file does not say.

    DEF coordinates are in database units; the divisor is on the UNITS line and
    is not always 1000 (external padframe DEFs have used 200). Reading the number
    without the divisor is off by 5x and looks plausible, so both are required.
    """
    from .defpins import die_area
    try:
        area = die_area(def_path.read_text())
    except OSError:
        return None
    if area is None:
        return None
    _, (x0, y0, x1, y1) = area
    return max(x1 - x0, y1 - y0)


DEFAULT_CONFIG_DIR = "flow/librelane/experimental"


def _resolve_pin_template(pin_template: str, repo_root: Path,
                          config_dir: Optional[str] = None) -> Path:
    """Where `dir::` points: the directory the generated config will live in.

    LibreLane resolves `dir::` against the config it is reading, so this has to
    follow wherever the caller is writing that config. It used to hardcode
    experimental/, which was true until integration runs started writing theirs
    to flow/librelane/integration/ and a correct DEF path then resolved to a
    file that does not exist.
    """
    if pin_template.startswith("dir::"):
        base = repo_root / (config_dir or DEFAULT_CONFIG_DIR)
        return base / pin_template[5:]
    return Path(pin_template)


def _format_value(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(v) for v in value) + "]"
    if isinstance(value, float):
        # Trim a trailing .0 so DIE_AREA reads as LibreLane writes it.
        return f"{value:g}"
    return str(value)


def render_derived_block(
    design_name: str,
    floorplan: Floorplan,
    clock_ns: Optional[float],
    pin_template: Optional[str] = None,
) -> str:
    """The appended block, annotated with where each number came from."""
    lines = [
        "",
        "# ── DERIVED PER DESIGN — do not hand-edit ──────────────────────────",
        "#",
        "# Generated by `mosaic physical-intent harden` from the SoC config",
        "# and its objectives. Everything above this line is the shared",
        f"# template ({DEFAULT_TEMPLATE}) and applies to every design.",
        "#",
        (f"# cell area   {floorplan.logic_um2:,.0f} um2 post-CTS "
         f"[{floorplan.basis}]" if floorplan.logic_um2 is not None else
         f"# cell area   NOT ESTIMATED [{floorplan.basis}] -- no area "
         f"calibration for this technology; the die is an input, not a "
         f"derivation, and nothing checked that the cells fit it"),
        f"#             {floorplan.reason}",
        f"# utilisation {floorplan.target_utilisation:.1%} target",
        f"#             core {floorplan.core_side_um:.1f} um square",
        f"# margin      {floorplan.margin_um:g} um per side for the power ring",
        f"# die         {floorplan.die_side_um:.1f} um square"
        f" = {floorplan.die_area_mm2:.4f} mm2",
    ]
    # Routability caveats belong in the artefact, not only on someone's
    # terminal: the config outlives the command that produced it.
    for warning in floorplan.warnings:
        lines.append(f"# routability {warning}")
    lines += [
        "",
        f"DESIGN_NAME: {design_name}",
    ]
    if clock_ns is not None:
        lines += [
            f"# {1000.0 / clock_ns:g} MHz, from soc.objectives.target_clock_mhz."
            " A request, not a result:",
            "# STA decides whether it was met.",
            f"CLOCK_PERIOD: {_format_value(clock_ns)}",
        ]
    for key, value in floorplan.as_librelane().items():
        lines.append(f"{key}: {_format_value(value)}")
    if pin_template:
        # An external padframe DEF fixes every pin's edge, offset and
        # layer. It is per-design by construction (each block gets its own
        # position in the pad ring), which is why it is derived here and refused in the shared
        # template.
        #
        # LibreLane matches pin SETS in strict mode: one port the DEF does not
        # name, or one DEF pin the wrapper does not declare, fails the run after
        # synthesis rather than before it. That is deliberate -- a wrapper that
        # silently dropped a pad control would otherwise reach GDS.
        lines += [
            "",
            "# Pin placement comes from an external padframe DEF, not from",
            "# our floorplan. DIE_AREA above must equal the DEF's DIEAREA or the",
            "# pins land outside the die.",
            f"FP_DEF_TEMPLATE: {pin_template}",
        ]
    for reference in floorplan.references:
        lines.append(f"# calibration: {reference}")
    return "\n".join(lines) + "\n"


def generate_hardening_config(
    soc: SocInput,
    design_name: str,
    *,
    repo_root: Path,
    template: str = DEFAULT_TEMPLATE,
    target_utilisation: Optional[float] = None,
    margin_um: float = DEFAULT_MARGIN_UM,
    clock_period_override: Optional[float] = None,
    repair_margin_override: Optional[int] = None,
    pin_template: Optional[str] = None,
    config_dir: Optional[str] = None,
) -> Tuple[Optional[str], List[str]]:
    """Render a complete hardening config. Returns ``(text, errors)``."""
    template_path = repo_root / template
    if not template_path.is_file():
        return None, [f"no hardening template at {template}"]
    text = template_path.read_text()

    # Refuse before rendering rather than emit a config that would harden the
    # wrong process. This is the failure the IHP probe found: every layer above
    # accepted `pdk: ihp-sg13g2` and this step produced a GF180 config.
    from harness.intent import coerce as _coerce
    design_pdk = getattr(_coerce(soc), "pdk", None)
    template_pdk = TEMPLATE_TECHNOLOGY
    for line in text.splitlines():
        if line.startswith(TEMPLATE_TECHNOLOGY_MARKER):
            template_pdk = line.split(":", 1)[1].strip()
            break
    if design_pdk and design_pdk != template_pdk:
        present = [k for k in PROCESS_SPECIFIC_KEYS
                   if any(line.startswith(f"{k}:") for line in text.splitlines())]
        return None, [
            f"design declares pdk {design_pdk!r} but {template} is written for "
            f"{template_pdk!r}: {present or list(PROCESS_SPECIFIC_KEYS)} "
            "carry that process's corner names and slew limit, the config "
            "names no PDK of its own, and flow/librelane/experimental/"
            "run_signoff.sh hardens with a hardcoded --pdk gf180mcuD. The run "
            "would succeed and measure the wrong process. Supply a template "
            f"for {design_pdk!r} via --template"
        ]

    # A template carrying a derived key would silently win or lose depending on
    # YAML merge order, so refuse rather than resolve it.
    present = [k for k in DERIVED_KEYS
               if any(line.startswith(f"{k}:") for line in text.splitlines())]
    if present:
        return None, [
            f"template {template} sets design-specific key(s) {present}; "
            "those are derived per design and must not be in the shared template"
        ]

    # A per-design repair margin REPLACES the template's line rather than
    # being appended after it. Appending would work -- YAML takes the last of
    # a duplicated key -- but "works because of an ordering rule" is exactly
    # what DERIVED_KEYS exists to prevent, and a reader of the emitted file
    # would see two values and have to know which wins.
    margin = (repair_margin_override if repair_margin_override is not None
              else getattr(_coerce(soc).objectives, "repair_margin_pct", None))
    if margin is not None:
        key = "GRT_DESIGN_REPAIR_MAX_SLEW_PCT"
        lines, replaced = [], 0
        for line in text.splitlines():
            if line.startswith(f"{key}:"):
                lines.append(f"{key}: {margin}"
                             f"  # per-design override of the template's"
                             f" {line.split(':',1)[1].strip()}")
                replaced += 1
            else:
                lines.append(line)
        if replaced != 1:
            return None, [
                f"design overrides {key} to {margin} but {template} sets it "
                f"{replaced} times; the override rewrites exactly one line, so "
                "0 would silently do nothing and 2 is ambiguous"]
        text = "\n".join(lines) + "\n"

    # DESIGN_NAME becomes a SystemVerilog module name, so it has to be a legal
    # identifier -- `isalnum()` is not that test: it accepts "3leading", which
    # a synthesiser rejects far downstream and unhelpfully.
    if not _MODULE_NAME.fullmatch(design_name or ""):
        return None, [
            f"design name {design_name!r} is not a valid module name: it must "
            "start with a letter or underscore and contain only letters, "
            "digits and underscores"
        ]

    die_override = None
    if pin_template:
        def_path = _resolve_pin_template(pin_template, repo_root, config_dir)
        die_override = die_side_from_def(def_path)
        if die_override is None:
            return None, [
                f"cannot read DIEAREA from {def_path}; FP_DEF_TEMPLATE needs a "
                "DEF with both a UNITS DISTANCE MICRONS line and a DIEAREA line"
            ]

    floorplan, errors = derive_floorplan(
        soc, target_utilisation=target_utilisation, margin_um=margin_um,
        die_um_override=die_override)
    if floorplan is None:
        return None, errors


    clock_ns = clock_period_override
    if clock_ns is None:
        clock_ns = clock_period_ns(soc)
    if clock_ns is None:
        return None, [
            "no clock period: set soc.objectives.target_clock_mhz, or pass an "
            "explicit override. It is not defaulted, because a clock nobody "
            "chose is the kind of number that ends up in a datasheet"
        ]
    if clock_ns <= 0:
        return None, ["clock period must be greater than 0 ns"]

    return text + render_derived_block(
        design_name, floorplan, clock_ns, pin_template), []


def wrapper_path_for(design_name: str) -> str:
    """Where the delivery wrapper for a design must live.

    LibreLane requires DESIGN_NAME to equal the top module name, and signoff
    waivers are scoped by design name, so the wrapper is per design and named
    after it. `run_signoff.sh` derives the same path from the same rule.
    """
    return f"flow/librelane/experimental/{design_name}.sv"
