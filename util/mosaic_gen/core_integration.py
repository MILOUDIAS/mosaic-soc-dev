"""Does a catalog core actually have an integration path in this repository?

``core_registry`` says what a core *is*.  This module answers whether the three
parts needed to build one are present, which is a question about files on disk
and therefore cannot live in that deliberately pure module.

AGENTS.md states the rule: a new core needs its name in ``AVAILABLE_CPUS``, an
``hw/sci/<core>_sci.sv`` wrapper, and a ``% elif group.name == "<core>":``
branch in ``cpu_subsystem.sv.tpl``.  Nothing enforced it.  There is a fourth
part, which AGENTS.md folds into the second and elaboration cannot tell apart
from it: the wrapper has to be listed in ``hw/sci/sci.core``, or FuseSoC never
compiles it and the module is unresolved even though the file is right there.  The matrix happens to
be complete today, and a fifteenth name added tomorrow would pass validation and
then fail during elaboration as an unresolved module reference, which is a
confusing way to learn that a wrapper was never written.

The check is deliberately about REFUSAL QUALITY rather than about preventing the
gap.  A core with no wrapper is a legitimate work-in-progress; a core with no
wrapper that reports "module qerv_sci not found" three tool invocations later is
not.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

from .core_registry import CORE_SPECS

SCI_DIR = "hw/sci"
SCI_CORE_FILE = "hw/sci/sci.core"
CPU_SUBSYSTEM_TPL = "hw/mosaic_soc/cpu_subsystem.sv.tpl"


@dataclass(frozen=True)
class CoreIntegration:
    """What a core has, and what it is missing."""

    name: str
    in_catalog: bool
    sci_module: Optional[str]      # None when the core needs no wrapper
    sci_file: Optional[Path]       # where that wrapper must live
    has_sci: bool
    has_branch: bool
    in_fileset: bool = True        # wrapper is listed in sci.core

    @property
    def complete(self) -> bool:
        return (self.in_catalog and self.has_sci and self.has_branch
                and self.in_fileset)

    @property
    def missing(self) -> List[str]:
        """Human-readable list of what is absent, in build order."""

        gaps: List[str] = []
        if not self.in_catalog:
            gaps.append(
                f"no entry in core_registry.CORE_SPECS for '{self.name}'"
            )
        if not self.has_sci:
            gaps.append(
                f"no SCI wrapper at {self.sci_file} "
                f"(module '{self.sci_module}')"
            )
        if not self.has_branch:
            gaps.append(
                f'no `% elif group.name == "{self.name}":` branch in '
                f"{CPU_SUBSYSTEM_TPL}"
            )
        if not self.in_fileset:
            # Indistinguishable from a missing wrapper at elaboration time,
            # and much harder to see, because the file is sitting right there.
            gaps.append(
                f"'{self.sci_module}.sv' is not listed in {SCI_CORE_FILE}, "
                "so FuseSoC never compiles it"
            )
        return gaps

    def refusal(self) -> str:
        """Why this core cannot be built, naming the part that is missing.

        The point of the whole module: a caller can print this instead of
        letting the generator emit RTL that references a module nobody wrote.
        """

        if self.complete:
            return ""
        parts = "; ".join(self.missing)
        return (
            f"core '{self.name}' has no complete integration path: {parts}. "
            "See AGENTS.md for the three parts a new core needs."
        )


def compiled_wrappers(repo_root: Path) -> Set[str]:
    """Filenames FuseSoC will actually compile, from every fileset in sci.core.

    Parsed, not grepped. A substring search over the raw text is satisfied by
    a commented-out entry and by the legacy ``files_rtl`` aggregate, so it
    reports a wrapper as built when nothing builds it -- which is the exact
    failure this function exists to catch.
    """

    core_file = repo_root / SCI_CORE_FILE
    if not core_file.is_file():
        return set()
    text = core_file.read_text()
    # CAPI=2 files open with a bare `CAPI=2:` marker line before the YAML.
    body = text.split("\n", 1)[1] if text.startswith("CAPI=2:") else text
    try:
        doc = yaml.safe_load(body) or {}
    except yaml.YAMLError:
        return set()
    found: Set[str] = set()
    for fileset in (doc.get("filesets") or {}).values():
        for entry in (fileset or {}).get("files") or []:
            # An entry is either "name.sv" or {"name.sv": {...}}.
            found.add(next(iter(entry)) if isinstance(entry, dict) else entry)
    return found


def inspect(name: str, repo_root: Path) -> CoreIntegration:
    """Resolve one core against the files in ``repo_root``."""

    spec = CORE_SPECS.get(name)
    if spec is None:
        return CoreIntegration(name, False, None, None, False, False)

    if spec.sci:
        module = spec.sci_module
        path = repo_root / SCI_DIR / f"{module}.sv"
        has_sci = path.is_file()
        in_fileset = f"{module}.sv" in compiled_wrappers(repo_root)
    else:
        # x-heep-native cores are instantiated directly and need no wrapper.
        module, path, has_sci, in_fileset = None, None, True, True

    tpl = repo_root / CPU_SUBSYSTEM_TPL
    branch = f'group.name == "{name}"'
    has_branch = tpl.is_file() and branch in tpl.read_text()

    return CoreIntegration(name, True, module, path, has_sci, has_branch,
                           in_fileset)


def audit(repo_root: Path) -> Dict[str, CoreIntegration]:
    """Every catalog core, resolved. The invariant the test suite enforces."""

    return {name: inspect(name, repo_root) for name in sorted(CORE_SPECS)}


def incomplete(repo_root: Path) -> Dict[str, CoreIntegration]:
    """Only the cores that cannot currently be built."""

    return {n: r for n, r in audit(repo_root).items() if not r.complete}
