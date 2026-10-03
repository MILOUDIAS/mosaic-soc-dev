"""Bounded agent loop over the deterministic MOSAIC harness tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Mapping, Optional
import uuid

from .agent_tools import AgentToolRegistry
from .core import SkillResult, VALID_CORE_IPS
from .events import AgentEvent, EventStream
from .llm import ProviderEvent, ToolCallingProvider


SYSTEM_PROMPT = """You are the oh-my-soc MOSAIC hardware agent. You operate by
calling the typed deterministic tools provided to you; never claim a command
ran unless its tool result says ok=true. Choose tools based on the user's
request, inspect every result, and recover from a failed non-physical gate when
the result gives a safe correction. Never invent paths, core support, metrics,
or tapeout evidence.

Your first tool call MUST be request_scope. The runtime derives a non-escalatable
outcome scope from the user's request; call request_scope with that exact scope
and explain why. Respect negation: "do not build; only explain" is analysis.

For a request to create/build/verify an SoC, use this evidence chain in order:
soc_plan -> soc_generate -> topology_check -> flow_run(mosaic-gen-config) ->
flow_run(tb-soc-generic) -> doc_config. A failed gate must be corrected and rerun;
it may not be bypassed. If the parser's interpretation is wrong, use
config_generate with an explicit structured topology instead of editing YAML.
Physical hardening is unavailable without explicit
user approval and is never implied by an RTL simulation pass.

For analysis-only requests, choose the narrow read tool (config_validate,
drc_analyze, wrapper_analyze, or topology_check). Explain your next decision
briefly before each tool call. Finish with a concise evidence-based summary.
For wrapper integration, first persist wrapper_analyze under
build/wrapper_smith/, then pass that exact current analysis path to
wrapper_scaffold. Never choose an arbitrary repository output path.
"""


@dataclass
class _Call:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class _Turn:
    text: str
    calls: List[_Call]
    stop_reason: str = ""
    terminal_seen: bool = False


REQUEST_SCOPES = {
    "analysis",
    "config",
    "rtl",
    "simulation",
    "physical",
    "integration",
    "testbench",
    "documentation",
    "drc",
}


def classify_request_scope(request: str) -> str:
    """Conservatively derive the user's authorized outcome from their text.

    This is a policy ceiling, not model output. Ambiguous requests remain
    analysis-only; callers can override it explicitly with --require-evidence.
    """

    text = request.lower().replace("don’t", "don't")
    read_pattern = (
        r"\b(?:analysis|analy[sz]e|assess|evaluate|inspect|review|check|"
        r"explain|describe|determine|understand|know|whether|"
        r"tell\s+me|can\s+you\s+tell|how\s+(?:to|do|can)|what\s+would)\b"
    )
    read_intent = re.search(read_pattern, text)
    modal_advice = re.search(
        r"\b(?:can|could|may|should)\s+(?:i|we)\b|"
        r"\bdo\s+(?:i|we)\s+need\s+to\b|"
        r"\b(?:is|would)\s+it\s+(?:be\s+)?(?:ok(?:ay)?|possible|safe)\s+to\b",
        text,
    )
    if modal_advice:
        return "analysis"
    # A leading/standalone denial stays read-only. If the user first requests
    # an operation and then denies a later mutation ("run; do not regenerate"),
    # classify only the positive prefix; dynamic tool policy enforces the denial.
    denied_effect = re.search(
        r"\b(?:do\s+not|don't|never)\b.{0,64}\b(?:apply|build|change|create|"
        r"edit|execute|generate|harden|integrate|modify|overwrite|regenerate|"
        r"rewrite|run|scaffold|simulate|synthesize|test|update|vendor|write)\b|"
        r"\bwithout\b.{0,48}\b(?:anything|apply|build|change|execution|"
        r"generat|modif|run|side\s+effects?|simulat|test|writ)\w*\b",
        text,
    )
    if denied_effect:
        prefix = text[:denied_effect.start()].strip(" ,;:-")
        positive_prefix = re.search(
            r"\b(?:apply|build|create|execute|generate|harden|integrate|run|"
            r"simulate|synthesize|test|verify|wrap)\b",
            prefix,
        )
        if not positive_prefix:
            return "analysis"
        text = prefix
        read_intent = re.search(read_pattern, text)
    read_only_limit = re.search(
        r"\b(?:analysis[- ]only|read[- ]only|no\s+(?:changes?|writes?|execution)|"
        r"without\s+(?:a\s+)?(?:build|building|changes?|modification|writing|"
        r"execution|running)|(?:analy[sz]e|inspect|review|explain|describe)"
        r".{0,40}\bonly|(?:just|only)\s+(?:analy[sz]e|inspect|review|check|"
        r"explain|describe))\b",
        text,
    )
    if read_intent and read_only_limit:
        return "analysis"
    followup_action = re.search(
        r"\b(?:and|but|then)\s+(?:please\s+)?(?:apply|build|create|execute|"
        r"document|generate|harden|integrate|render|run|scaffold|simulate|"
        r"synthesize|tape\s*out|test|triage|vendor|visuali[sz]e|write)\b",
        text,
    )
    if re.search(
        r"\b(?:how\s+(?:to|do\s+i|can\s+i)|tell\s+me\s+how|what\s+would\s+it\s+take)\b",
        text,
    ) and not followup_action:
        return "analysis"
    if read_intent and not followup_action:
        return "analysis"
    if re.search(r"\b(?:apply|integrate|wrap)\b", text) or re.search(
        r"\bvendor\b.{0,24}\b(?:core|ip|rtl|sources?)\b", text
    ) or re.search(
        r"\b(?:author|create|generate|write)\b.{0,36}"
        r"\b(?:integration|wrapper)\b",
        text,
    ):
        return "integration"
    if re.search(r"\btb-smith\b", text) or re.search(
        r"\b(?:author|build|create|execute|generate|run|verify|write)\b.{0,36}"
        r"\b(?:single[- ]hart\s+)?(?:test\s*bench|testbench|tb)\b",
        text,
    ):
        return "testbench"
    if re.search(r"\b(?:document|visuali[sz]e)\b", text) or re.search(
        r"\b(?:author|create|generate|render|write)\b.{0,36}"
        r"\b(?:dashboard|diagram|documentation)\b",
        text,
    ):
        return "documentation"
    if re.search(r"\b(?:drc|lvs)\b", text) and not re.search(
        r"\b(?:harden|gds|place\s*(?:and|&)\s*route|tapeout)\b", text
    ):
        return "drc"
    if re.search(r"\b(?:harden|tape\s*out)\b", text) or re.search(
        r"\b(?:bring|create|execute|generate|perform|produce|run|start|take)\b"
        r".{0,48}\b(?:gds|hardening|place\s*(?:and|&)\s*route|pnr|tapeout)\b",
        text,
    ):
        return "physical"
    if re.search(
        r"\b(?:verify|simulate|execute)\b|\bwake[- ]demo\b|"
        r"\brun\s+(?:the\s+)?(?:test|demo|simulation|full[- ]soc)\b|"
        r"(?:^|\b(?:and|please|then|to)\s+|\b(?:can|could|would)\s+you\s+)build\b|"
        r"\b(?:create|generate|make)\b.{0,36}\b(?:soc|chip)\b|"
        r"\b(?:soc|chip)\s+with\b",
        text,
    ):
        return "simulation"
    if re.search(r"\b(?:synthesize|synthesis|generate\s+rtl|render\s+rtl)\b", text):
        return "rtl"
    if re.search(
        r"\b(?:create|generate|write|author|make)\b.{0,36}\b(?:config|yaml)\b",
        text,
    ):
        return "config"
    return "analysis"


def _analysis_tools_for(request: str) -> set[str]:
    """Select evidence tools relevant to a read-only request."""

    text = request.lower()
    if re.search(r"\b(?:wrap|wrapper|integrat(?:e|ion)|vendor)\b", text):
        return {"wrapper_analyze"}
    if re.search(r"\b(?:drc|lvs|design\s+rule)\b", text):
        return {"drc_analyze", "drc_scan"}
    if re.search(r"\b(?:topology|memory\s+map|boot\s+layout)\b", text):
        return {"topology_check"}
    if re.search(r"\b(?:config(?:uration)?|yaml|hjson)\b", text):
        return {"config_validate", "topology_check"}
    if re.search(r"\b(?:document|documentation|dashboard)\b", text):
        return {"doc_config", "doc_dashboard"}
    return {"soc_plan"}


def _integration_needs_apply(request: str) -> bool:
    text = request.lower()
    staging_only = re.search(r"\b(?:draft|scaffold|skeleton|stage)\b", text)
    apply_intent = re.search(r"\b(?:apply|install|integrate|vendor)\b", text)
    return bool(apply_intent or not staging_only)


def _testbench_needs_run(request: str) -> bool:
    text = request.lower()
    run_intent = re.search(
        r"\b(?:execute|run|verify)\b|\btest\b(?!\s*(?:bench|fixture))", text
    )
    generate_only = re.search(
        r"\b(?:author|create|generate|scaffold|write)\b.{0,36}"
        r"\b(?:single[- ]hart\s+)?(?:test\s*bench|testbench|tb)\b",
        text,
    )
    return bool(run_intent or not generate_only)


def _requested_config_paths(request: str, repo_root: Path) -> set[str]:
    paths: set[str] = set()
    for token in re.findall(
        r"(?<![A-Za-z0-9_.-])/?(?:[A-Za-z0-9_.-]+/)*"
        r"[A-Za-z0-9_.-]+\.(?:hjson|ya?ml)\b",
        request,
        re.IGNORECASE,
    ):
        candidate = (repo_root / token).resolve()
        try:
            candidate.relative_to(repo_root)
        except ValueError:
            continue
        paths.add(str(candidate))
    return paths


def _requested_core_names(request: str) -> set[str]:
    text = request.lower()
    cores = {
        core for core in VALID_CORE_IPS if re.search(rf"\b{re.escape(core)}\b", text)
    }
    candidates = re.findall(
        r"\b([a-z][a-z0-9_]*)\s+core\b|"
        r"\b(?:for|named)\s+(?:the\s+)?([a-z][a-z0-9_]*)\b",
        text,
    )
    ignored = {
        "a", "an", "new", "the", "this", "that", "single", "riscv",
        "ahb", "axi", "obi", "wishbone", "tilelink", "wrapper", "soc",
        "called", "core",
    }
    for before_core, after_marker in candidates:
        candidate = before_core or after_marker
        if candidate and candidate not in ignored:
            cores.add(candidate)
    action_candidates = re.findall(
        r"\b(?:integrate|scaffold|vendor|wrap)\s+"
        r"(?:(?:a|an|the|new)\s+){0,3}([a-z][a-z0-9_]*)\b|"
        r"\b(?:create|execute|generate|run|verify)\s+(?:the\s+)?"
        r"([a-z][a-z0-9_]*)\s+(?:test\s*bench|testbench|tb)\b|"
        r"\b(?:test\s*bench|testbench|tb)\s+(?:for\s+)?"
        r"([a-z][a-z0-9_]*)\b|"
        r"\b([a-z][a-z0-9_]*)['’]s\s+(?:test\s*bench|testbench|tb)\b|"
        r"\bcore\s+(?:called|named)\s+([a-z][a-z0-9_]*)\b",
        text,
    )
    for group in action_candidates:
        candidate = next((item for item in group if item), "")
        if candidate and candidate not in ignored:
            cores.add(candidate)
    for candidate in re.findall(
        r"\bcore\s+(?:called|named)\s+([a-z][a-z0-9_]*)\b", text
    ):
        if candidate not in ignored:
            cores.add(candidate)
    return cores


def _config_write_intent(request: str, requested_configs: set[str]) -> bool:
    if re.search(
        r"\b(?:do\s+not|don't|never)\b.{0,64}"
        r"\b(?:author|create|edit|generate|modify|overwrite|regenerate|rewrite|"
        r"update|write)\b.{0,48}\b(?:config|hjson|yaml)\b",
        request,
        re.IGNORECASE,
    ):
        return False
    if not requested_configs:
        return True
    return bool(
        re.search(
            r"\b(?:author|create|edit|generate|modify|overwrite|regenerate|"
            r"rewrite|update|write)\b.{0,64}\b(?:config|hjson|yaml)\b|"
            r"\b(?:author|create|generate|overwrite|regenerate|rewrite|write)\b"
            r".{0,64}\.(?:hjson|ya?ml)\b",
            request,
            re.IGNORECASE,
        )
    )


def _testbench_write_intent(request: str, scope: str) -> bool:
    if re.search(
        r"\b(?:do\s+not|don't|never)\b.{0,64}"
        r"\b(?:author|create|edit|generate|modify|overwrite|regenerate|rewrite|"
        r"update|write)\b.{0,48}\b(?:test\s*bench|testbench|tb)\b",
        request,
        re.IGNORECASE,
    ):
        return False
    if scope == "integration":
        return True
    return bool(
        re.search(
            r"\b(?:author|create|generate|regenerate|scaffold|write)\b.{0,48}"
            r"\b(?:test\s*bench|testbench|tb)\b",
            request,
            re.IGNORECASE,
        )
    )


def _derive_config_contract(
    request: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Any], set[str], Optional[str]]:
    """Build the same supported prompt contract ConfigAuthor will emit."""

    from .skills.config_author import CORE_DEFAULTS, PRESETS
    from .skills.soc_from_prompt import _repair, parse_prompt

    intent = parse_prompt(request)
    explicit: set[str] = set()
    if intent.core_groups:
        explicit.add("cores")
    if intent.sram_kb is not None:
        explicit.add("sram_kb")
    if intent.boot_rom_kb is not None:
        explicit.add("boot_rom_kb")
    if intent.bus is not None:
        explicit.add("bus")
    if intent.tdu_explicit:
        explicit.add("tdu")
    if intent.sched_mode is not None:
        explicit.add("mode")
    if intent.peripherals_explicit:
        explicit.add("peripherals")
    _repair(intent)

    if intent.preset and intent.preset in PRESETS:
        soc = copy.deepcopy(PRESETS[intent.preset]["soc"])
        return (
            list(soc.get("cores", [])),
            {
                "sram_kb": soc.get("memory", {}).get("sram_kb"),
                "boot_rom_kb": soc.get("memory", {}).get("boot_rom_kb"),
                "bus": soc.get("bus"),
                "tdu": soc.get("scheduler", {}).get("tdu"),
                "mode": soc.get("scheduler", {}).get("mode"),
                "peripherals": list(soc.get("peripherals", [])),
            },
            {"cores", "sram_kb", "boot_rom_kb", "bus", "tdu", "mode", "peripherals"},
            intent.preset,
        )

    cores = [copy.deepcopy(group) for group in intent.core_groups]
    if any("chunksize" in group or "isa" in group for group in cores):
        explicit.add("core_params")
    used_boots = {
        int(group["boot_addr"])
        for group in cores
        if group.get("role") != "titan" and isinstance(group.get("boot_addr"), int)
    }
    next_boot = 0x1000
    contracts = []
    for group in cores:
        entry = dict(group)
        for key, value in CORE_DEFAULTS.get(str(entry.get("ip", "")), {}).items():
            if key != "sim_only":
                entry.setdefault(key, value)
        if entry.get("role") != "titan" and "boot_addr" not in entry:
            while next_boot in used_boots:
                next_boot += 0x1000
            entry["boot_addr"] = next_boot
            used_boots.add(next_boot)
            next_boot += 0x1000
        contracts.append(entry)
    worker_present = any(core.get("role") != "titan" for core in contracts)
    soc_contract = {
        "sram_kb": 32 if intent.sram_kb is None else intent.sram_kb,
        "boot_rom_kb": 2 if intent.boot_rom_kb is None else intent.boot_rom_kb,
        "bus": intent.bus or "obi",
        "tdu": bool(intent.tdu or worker_present),
        "mode": intent.sched_mode or ("dynamic" if intent.tdu else "static"),
        "peripherals": list(
            intent.peripherals if intent.peripherals_explicit else ["uart"]
        ),
    }
    return contracts, soc_contract, explicit, None


def _flow_gates_signoff(flow: str) -> bool:
    """A flow whose success means parsed DRC/LVS signoff evidence passed.

    Read from the flow table (flow_runner gates these on signoff_status), not
    from a hand-kept list of flow names.
    """
    from .skills.flow_runner import FLOWS

    return "signoff" in FLOWS.get(flow, {})


@dataclass
class AgentState:
    repo_root: Path
    user_request: str = ""
    scope: Optional[str] = None
    required_scope: Optional[str] = None
    scope_locked: bool = False
    # Plugin (unlocked) MCP sessions: the request is model-supplied, so the
    # ceiling is this user-set allowlist, and physical/integration also need
    # a person's approval token. None means the ceiling is `required_scope`.
    scope_allowlist: Optional[frozenset] = None
    scope_approval_required: bool = False
    request_bound: bool = False
    generated_configs: Dict[str, str] = field(default_factory=dict)
    exact_request_configs: Dict[str, str] = field(default_factory=dict)
    topology_ok: Dict[str, str] = field(default_factory=dict)
    generated_ok: Dict[str, str] = field(default_factory=dict)
    verified_configs: Dict[str, str] = field(default_factory=dict)
    generic_verified_configs: Dict[str, str] = field(default_factory=dict)
    # A registered hardening flow completed AND its parsed signoff evidence
    # passed. flow_runner gates harden-* on signoff_status (DRC + LVS), so
    # this is stronger than "the command exited zero" -- but it is NOT
    # qualification: timing is recorded and not gated, and area, power,
    # antenna, IR/EM and multi-corner closure are not evaluated at all.
    # Named for exactly what it establishes (roadmap 14.3 asked for
    # `physical_command_completed`; typed DRC/LVS evidence now exists, so
    # the honest name is narrower than `physical_ok` and wider than that).
    physical_drc_lvs_ok: bool = False
    requested_configs: set[str] = field(default_factory=set)
    requested_cores: set[str] = field(default_factory=set)
    requested_config_initial: Dict[str, str] = field(default_factory=dict)
    config_writes_allowed: bool = True
    testbench_writes_allowed: bool = False
    wake_demo_allowed: bool = False
    planned_request_ok: bool = False
    planned_topology: List[tuple[str, str, int]] = field(default_factory=list)
    planned_core_contracts: List[Dict[str, Any]] = field(default_factory=list)
    planned_soc_contract: Dict[str, Any] = field(default_factory=dict)
    planned_explicit_fields: set[str] = field(default_factory=set)
    planned_preset: Optional[str] = None
    integration_requires_apply: bool = True
    testbench_requires_run: bool = True
    required_analysis_tools: set[str] = field(default_factory=set)
    wrapper_analyses: Dict[str, str] = field(default_factory=dict)
    wrapper_analysis_inputs: Dict[str, Dict[str, str]] = field(default_factory=dict)
    wrapper_analysis_roots: Dict[str, str] = field(default_factory=dict)
    wrapper_staged: Dict[str, Dict[str, str]] = field(default_factory=dict)
    wrapper_applied: Dict[str, Dict[str, str]] = field(default_factory=dict)
    wrapper_apply_smoke_ok: set[str] = field(default_factory=set)
    wrapper_staged_analysis: Dict[str, str] = field(default_factory=dict)
    wrapper_applied_analysis: Dict[str, str] = field(default_factory=dict)
    wrapper_staged_analysis_digests: Dict[str, str] = field(default_factory=dict)
    wrapper_applied_analysis_digests: Dict[str, str] = field(default_factory=dict)
    wrapper_staged_pending: Dict[str, bool] = field(default_factory=dict)
    wrapper_applied_pending: Dict[str, bool] = field(default_factory=dict)
    wrapper_staged_source_digests: Dict[str, str] = field(default_factory=dict)
    wrapper_applied_source_digests: Dict[str, str] = field(default_factory=dict)
    testbenches_generated: Dict[str, Dict[str, str]] = field(default_factory=dict)
    testbenches_run: Dict[str, Dict[str, str]] = field(default_factory=dict)
    wake_demos_run: Dict[str, Dict[str, str]] = field(default_factory=dict)
    generated_source_digests: Dict[str, str] = field(default_factory=dict)
    verified_source_digests: Dict[str, str] = field(default_factory=dict)
    generic_verified_source_digests: Dict[str, str] = field(default_factory=dict)
    testbench_generate_source_digests: Dict[str, str] = field(default_factory=dict)
    testbench_run_source_digests: Dict[str, str] = field(default_factory=dict)
    wake_demo_source_digests: Dict[str, str] = field(default_factory=dict)
    physical_source_digest: Optional[str] = None
    physical_configs: Dict[str, str] = field(default_factory=dict)
    successful_tools: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    open_failures: Dict[str, str] = field(default_factory=dict)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)

    def _path(self, value: Any) -> Path:
        path = Path(str(value))
        if not path.is_absolute():
            path = self.repo_root / path
        return path.resolve()

    def fingerprint(self, value: Any) -> tuple[str, Optional[str]]:
        path = self._path(value)
        try:
            path.relative_to(self.repo_root)
        except ValueError:
            return str(path), None
        if not path.is_file():
            return str(path), None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return str(path), digest

    def has_current(self, evidence: Mapping[str, str], value: Any) -> bool:
        path, digest = self.fingerprint(value)
        return digest is not None and evidence.get(path) == digest

    def current_paths(self, evidence: Mapping[str, str]) -> List[str]:
        return [path for path in evidence if self.has_current(evidence, path)]

    def all_current(self, evidence: Mapping[str, str]) -> bool:
        return bool(evidence) and len(self.current_paths(evidence)) == len(evidence)

    def source_digest(self) -> str:
        # Reuse the same complete source-closure identity as MOSAIC generation.
        # It excludes volatile build products but includes RTL, TBs, configs,
        # software, flow inputs, scripts, and generator sources.
        from util.xheep_gen.build_manifest import _generator_source_record

        return _generator_source_record(self.repo_root)["sha256"]

    def _source_current(self, recorded: Optional[str]) -> bool:
        return bool(recorded) and recorded == self.source_digest()

    def _bound_config_paths(
        self, evidence: Mapping[str, str], source_digests: Mapping[str, str]
    ) -> set[str]:
        current_source = self.source_digest()
        valid = {
            path
            for path in self.current_paths(evidence)
            if source_digests.get(path) == current_source
            and self._requested_config_unchanged(path)
            and self._config_matches_target(path)
        }
        if self.requested_configs:
            return valid if self.requested_configs <= valid else set()
        generated = set(
            self.current_paths(
                self.generated_configs if self.planned_topology else self.exact_request_configs
            )
        )
        return valid & generated

    def _target_cores(self, available: set[str]) -> set[str]:
        if self.requested_cores:
            return available if self.requested_cores <= available else set()
        return available

    def _requested_config_unchanged(self, path: str) -> bool:
        if self.config_writes_allowed:
            return True
        initial = self.requested_config_initial.get(path)
        return initial is None or self.has_current({path: initial}, path)

    def _config_matches_target(self, path: str) -> bool:
        from .core import load_yaml

        try:
            cfg = load_yaml(Path(path))
            soc = cfg.get("soc", {})
            actual_cores = [dict(item) for item in soc.get("cores", [])]
        except Exception:
            return False
        if self.planned_core_contracts:
            actual_serialized = sorted(
                json.dumps(core, sort_keys=True, separators=(",", ":"))
                for core in actual_cores
            )
            expected_serialized = sorted(
                json.dumps(core, sort_keys=True, separators=(",", ":"))
                for core in self.planned_core_contracts
            )
            if actual_serialized != expected_serialized:
                return False
        actual_ips = {str(core.get("ip", "")) for core in actual_cores}
        if self.requested_cores and not self.requested_cores <= actual_ips:
            return False
        memory = soc.get("memory", {})
        scheduler = soc.get("scheduler", {})
        actual_contract = {
            "sram_kb": memory.get("sram_kb"),
            "boot_rom_kb": memory.get("boot_rom_kb"),
            "bus": soc.get("bus"),
            "tdu": scheduler.get("tdu"),
            "mode": scheduler.get("mode"),
            "peripherals": list(soc.get("peripherals", [])),
        }
        fields = (
            self.planned_explicit_fields
            if self.requested_configs
            else set(self.planned_soc_contract)
        )
        for field_name in fields - {"cores", "core_params"}:
            expected = self.planned_soc_contract.get(field_name)
            actual = actual_contract.get(field_name)
            if field_name == "peripherals" and self.requested_configs:
                if not set(expected or []) <= set(actual or []):
                    return False
            elif actual != expected:
                return False
        return bool(self.requested_configs or self.planned_request_ok)

    def _analysis_current(self, analysis_path: str) -> bool:
        return (
            self.has_current(self.wrapper_analyses, analysis_path)
            and self.all_current(self.wrapper_analysis_inputs.get(analysis_path, {}))
        )

    def _config_cores(self, paths: set[str]) -> set[str]:
        from .core import load_yaml

        cores: set[str] = set()
        for path in paths:
            try:
                cfg = load_yaml(Path(path))
                for item in cfg.get("soc", {}).get("cores", []):
                    if isinstance(item, Mapping) and isinstance(item.get("ip"), str):
                        cores.add(str(item["ip"]))
            except Exception:  # completion remains fail-closed
                continue
        return cores

    def _current_generated_testbench_cores(self) -> set[str]:
        current_source = self.source_digest()
        return {
            core
            for core, evidence in self.testbenches_generated.items()
            if self.all_current(evidence)
            and self.testbench_generate_source_digests.get(core) == current_source
        }

    def _current_unit_testbench_run_cores(self) -> set[str]:
        current_source = self.source_digest()
        return {
            core
            for core, evidence in self.testbenches_run.items()
            if self.all_current(evidence)
            and self.testbench_run_source_digests.get(core) == current_source
        }

    def _current_full_soc_cores(self) -> set[str]:
        current_source = self.source_digest()
        verified_paths = {
            path
            for path in self.current_paths(self.generic_verified_configs)
            if self.generic_verified_source_digests.get(path) == current_source
        }
        return self._config_cores(verified_paths)

    def _current_testbench_run_cores(self) -> set[str]:
        return self._current_unit_testbench_run_cores() | self._current_full_soc_cores()

    def _invalidate(self, value: Any) -> None:
        path, _ = self.fingerprint(value)
        self.topology_ok.pop(path, None)
        self.generated_ok.pop(path, None)
        self.verified_configs.pop(path, None)
        self.generic_verified_configs.pop(path, None)

    def _record_files(
        self, evidence: Dict[str, str], base: Path, values: Any
    ) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            # WrapperSmith annotates edited paths with a parenthesized action.
            relative = str(value).split(" (", 1)[0]
            candidate = Path(relative)
            path = candidate if candidate.is_absolute() else base / candidate
            resolved, digest = self.fingerprint(path)
            if digest:
                evidence[resolved] = digest

    def _record_tree(self, evidence: Dict[str, str], value: Any) -> None:
        root = self._path(value)
        if root.is_file():
            self._record_files(evidence, self.repo_root, [root])
            return
        if root.is_dir():
            hdl_suffixes = {".sv", ".svh", ".v", ".vh"}
            self._record_files(
                evidence,
                self.repo_root,
                [
                    path
                    for path in root.rglob("*")
                    if path.is_file() and path.suffix.lower() in hdl_suffixes
                ],
            )

    def observe(
        self, name: str, arguments: Mapping[str, Any], result: SkillResult
    ) -> None:
        self.tool_results.append(
            {
                "tool": name,
                "arguments": dict(arguments),
                "ok": result.ok,
                "summary": result.summary,
            }
        )
        call_key = f"{name}:{json.dumps(dict(arguments), default=str, sort_keys=True)}"
        if not result.ok:
            self.failures.append(f"{name}: {result.summary}")
            self.open_failures[call_key] = result.summary
            return
        # A successful retry/alternative invocation of the same typed tool
        # resolves its earlier failed attempts. Failures from a different tool
        # (for example an attempted unknown/raw-shell call) remain outstanding.
        for failed_key in list(self.open_failures):
            if failed_key.startswith(f"{name}:"):
                self.open_failures.pop(failed_key, None)
        self.successful_tools.append(name)
        if name == "request_scope":
            self.scope = str(arguments["scope"])
        elif name == "soc_plan" and str(arguments.get("request", "")) == self.user_request:
            intent = result.details.get("intent", {})
            groups = intent.get("core_groups", []) if isinstance(intent, Mapping) else []
            topology = []
            for group in groups:
                if isinstance(group, Mapping) and isinstance(group.get("ip"), str):
                    topology.append(
                        (
                            str(group["ip"]),
                            str(group.get("role", "")),
                            int(group.get("count", 1)),
                        )
                    )
            observed_preset = (
                intent.get("preset") if isinstance(intent, Mapping) else None
            )
            self.planned_request_ok = bool(
                (topology or observed_preset)
                and observed_preset == self.planned_preset
                and (
                    bool(self.planned_preset)
                    or sorted(topology) == sorted(self.planned_topology)
                )
            )
        elif name == "soc_generate":
            config = result.details.get("config", {}).get("path")
            if config:
                self._invalidate(config)
                path, digest = self.fingerprint(config)
                if digest:
                    self.generated_configs[path] = digest
                    self.exact_request_configs[path] = digest
        elif name == "config_generate":
            config = result.details.get("path")
            if config:
                self._invalidate(config)
                path, digest = self.fingerprint(config)
                if digest:
                    self.generated_configs[path] = digest
        elif name == "wrapper_analyze":
            analysis_path = result.details.get("analysis_path")
            if analysis_path:
                path, digest = self.fingerprint(analysis_path)
                if digest:
                    self.wrapper_analyses[path] = digest
                    analysis = result.details.get("analysis", {})
                    source_root = (
                        analysis.get("source_root")
                        if isinstance(analysis, Mapping)
                        else None
                    )
                    inputs = self.wrapper_analysis_inputs.setdefault(path, {})
                    if source_root:
                        self._record_tree(inputs, source_root)
                        self.wrapper_analysis_roots[path] = str(
                            self._path(source_root)
                        )
                    self._record_files(
                        inputs,
                        self.repo_root,
                        [
                            Path(__file__).parent / "skills" / "wrapper_smith.py",
                            Path(__file__).parent
                            / "templates"
                            / "wrapper"
                            / "families.py",
                        ],
                    )
        elif name == "topology_check":
            path, digest = self.fingerprint(arguments["path"])
            if digest:
                self.topology_ok[path] = digest
        elif name == "flow_run":
            flow = arguments["flow"]
            config = arguments.get("config", "")
            path, digest = self.fingerprint(config) if config else ("", None)
            if flow == "mosaic-gen-config":
                if digest:
                    self.generated_ok[path] = digest
                    self.generated_source_digests[path] = self.source_digest()
            elif flow in {
                "tb-soc-generic",
                "tb-soc-wake",
                "tb-soc-titan",
                "tb-soc-fw",
            }:
                if digest:
                    self.verified_configs[path] = digest
                    self.verified_source_digests[path] = self.source_digest()
                    if flow == "tb-soc-generic":
                        self.generic_verified_configs[path] = digest
                        self.generic_verified_source_digests[path] = (
                            self.source_digest()
                        )
            elif _flow_gates_signoff(flow):
                self.physical_drc_lvs_ok = True
                physical_config = config or str(self.repo_root / "mosaic.yaml")
                physical_path, physical_digest = self.fingerprint(physical_config)
                if physical_digest:
                    self.physical_configs[physical_path] = physical_digest
                self.physical_source_digest = self.source_digest()
        elif name == "wrapper_scaffold":
            core = str(arguments.get("core", ""))
            analysis_path, analysis_digest = self.fingerprint(
                arguments.get("analysis", "")
            )
            todos = result.details.get("todos", [])
            has_pending = any(
                isinstance(todo, Mapping) and todo.get("tag") == "pending"
                for todo in todos
            )
            applied = bool(arguments.get("apply", False))
            base = self.repo_root if applied else self._path(result.details.get("stage", "."))
            evidence_by_core = self.wrapper_applied if applied else self.wrapper_staged
            evidence: Dict[str, str] = {}
            evidence_by_core[core] = evidence
            for key in ("written", "edited", "skipped_existing"):
                self._record_files(evidence, base, result.details.get(key, []))
            if applied:
                self.wrapper_applied_analysis[core] = analysis_path
                if analysis_digest:
                    self.wrapper_applied_analysis_digests[core] = analysis_digest
                self.wrapper_applied_pending[core] = has_pending
                self.wrapper_applied_source_digests[core] = self.source_digest()
                self.wrapper_apply_smoke_ok.discard(core)
                smoke = result.details.get("fusesoc_smoke")
                if isinstance(smoke, Mapping) and smoke.get("ok"):
                    self.wrapper_apply_smoke_ok.add(core)
            else:
                self.wrapper_staged_analysis[core] = analysis_path
                if analysis_digest:
                    self.wrapper_staged_analysis_digests[core] = analysis_digest
                self.wrapper_staged_pending[core] = has_pending
                self.wrapper_staged_source_digests[core] = self.source_digest()
        elif name == "tb_generate":
            core = str(arguments.get("core", ""))
            base = self._path(result.details.get("dir", "."))
            self._record_files(
                self.testbenches_generated.setdefault(core, {}),
                base,
                result.details.get("files", []),
            )
            self.testbench_generate_source_digests[core] = self.source_digest()
        elif name == "tb_run":
            core = str(arguments.get("core", ""))
            tb_dir = self.repo_root / "tb" / "sci" / core
            self._record_files(
                self.testbenches_run.setdefault(core, {}),
                self.repo_root,
                [
                    tb_dir / "run.sh",
                    tb_dir / "deps.f",
                    tb_dir / f"tb_{core}_sci.sv",
                ],
            )
            self.testbench_run_source_digests[core] = self.source_digest()
        elif name == "tb_wake_demo" and bool(arguments.get("execute", True)):
            core = str(arguments.get("core", ""))
            config = result.details.get("config", {}).get("path")
            if config:
                self._record_files(
                    self.wake_demos_run.setdefault(core, {}),
                    self.repo_root,
                    [config],
                )
                self.wake_demo_source_digests[core] = self.source_digest()

    def bind_request(self, request: str, authorized_scope: str) -> None:
        """Derive everything the gates read from the request text.

        One function for the in-process loop and the MCP session. It used to
        live inline in `AgentRunner.run`, so an MCP session never set the plan
        contract or the write intents, and soc_generate, config_generate,
        tb_generate and tb_wake_demo were refused over MCP no matter what.
        """
        self.required_analysis_tools = _analysis_tools_for(request)
        self.user_request = request
        self.integration_requires_apply = _integration_needs_apply(request)
        self.testbench_requires_run = _testbench_needs_run(request)
        self.requested_configs = _requested_config_paths(request, self.repo_root)
        self.requested_cores = _requested_core_names(request)
        (
            self.planned_core_contracts,
            self.planned_soc_contract,
            self.planned_explicit_fields,
            self.planned_preset,
        ) = _derive_config_contract(request)
        self.planned_topology = [
            (
                str(core.get("ip", "")),
                str(core.get("role", "")),
                int(core.get("count", 1)),
            )
            for core in self.planned_core_contracts
        ]
        for requested_path in self.requested_configs:
            path, digest = self.fingerprint(requested_path)
            if digest:
                self.requested_config_initial[path] = digest
        self.config_writes_allowed = _config_write_intent(
            request, self.requested_configs
        )
        self.testbench_writes_allowed = _testbench_write_intent(
            request, authorized_scope
        )
        self.wake_demo_allowed = bool(
            self.config_writes_allowed
            and (
                authorized_scope == "integration"
                or re.search(r"\bwake[- ]demo\b", request, re.IGNORECASE)
            )
        )
        self.request_bound = True

    def completion_error(self) -> Optional[str]:
        if self.scope is None or self.required_scope is None:
            return "request scope has not been classified"
        if self.open_failures:
            return "unresolved tool failures remain"
        scope = self.required_scope
        if scope == "analysis":
            useful = set(self.successful_tools) - {"request_scope"}
            required = self.required_analysis_tools or {"soc_plan"}
            return (
                None
                if useful & required
                else "analysis has no successful request-relevant evidence tool"
            )
        if scope == "config":
            config_evidence = (
                self.generated_configs
                if self.requested_configs or self.planned_topology
                else self.exact_request_configs
            )
            current = {
                path
                for path in self.current_paths(config_evidence)
                if self._requested_config_unchanged(path)
                and self._config_matches_target(path)
            }
            if self.requested_configs and not self.requested_configs <= current:
                current = set()
            return None if current else "no current requested validated config was generated"
        if scope == "rtl":
            return (
                None
                if self._bound_config_paths(
                    self.generated_ok, self.generated_source_digests
                )
                else "no current target-bound mosaic-gen-config evidence"
            )
        if scope == "simulation":
            return (
                None
                if self._bound_config_paths(
                    self.verified_configs, self.verified_source_digests
                )
                else "no current target-bound full-SoC verification evidence"
            )
        if scope == "physical":
            physical_paths = set(self.current_paths(self.physical_configs))
            config_ok = bool(physical_paths) and (
                not self.requested_configs
                or self.requested_configs <= physical_paths
            )
            return (
                None
                if self.physical_drc_lvs_ok
                and config_ok
                and self._source_current(self.physical_source_digest)
                else "no current target-bound approved physical-flow evidence "
                "(a hardening run whose DRC and LVS evidence passed)"
            )
        if scope == "integration":
            canonical_unit_runs = (
                self._current_generated_testbench_cores()
                & self._current_unit_testbench_run_cores()
            )
            full_soc_runs = self._current_full_soc_cores()
            applied = {
                core
                for core, evidence in self.wrapper_applied.items()
                if core in self.wrapper_apply_smoke_ok
                and core in canonical_unit_runs
                and core in full_soc_runs
                and not self.wrapper_applied_pending.get(core, True)
                and (
                    self.repo_root
                    / "hw"
                    / "vendor"
                    / "mosaic"
                    / core
                    / f"{core}.core"
                ).is_file()
                and self.all_current(evidence)
                and self._source_current(
                    self.wrapper_applied_source_digests.get(core)
                )
                and bool(self.wrapper_applied_analysis.get(core))
                and self._analysis_current(self.wrapper_applied_analysis[core])
                and self.wrapper_applied_analysis_digests.get(core)
                == self.wrapper_analyses.get(self.wrapper_applied_analysis[core])
                and self.wrapper_analysis_roots.get(
                    self.wrapper_applied_analysis[core]
                )
                == str(
                    (
                        self.repo_root / "hw" / "vendor" / "mosaic" / core
                    ).resolve()
                )
            }
            if self.integration_requires_apply:
                if self._target_cores(applied):
                    return None
                return "no current target-bound applied wrapper with passing FuseSoC smoke evidence"
            staged = {
                core
                for core, evidence in self.wrapper_staged.items()
                if self.all_current(evidence)
                and self._source_current(
                    self.wrapper_staged_source_digests.get(core)
                )
                and bool(self.wrapper_staged_analysis.get(core))
                and self._analysis_current(self.wrapper_staged_analysis[core])
                and self.wrapper_staged_analysis_digests.get(core)
                == self.wrapper_analyses.get(self.wrapper_staged_analysis[core])
            }
            if self._target_cores(staged | applied):
                return None
            return "no current target-bound wrapper scaffold evidence"
        if scope == "testbench":
            generated_cores = self._current_generated_testbench_cores()
            run_cores = self._current_testbench_run_cores()
            if self.testbench_requires_run:
                return (
                    None
                    if self._target_cores(run_cores)
                    else "no current target-bound passing testbench run evidence"
                )
            return (
                None
                if self._target_cores(generated_cores | run_cores)
                else "no current target-bound generated testbench evidence"
            )
        if scope == "documentation":
            return (
                None
                if any(
                    name in self.successful_tools
                    for name in {"doc_config", "doc_dashboard", "topology_render"}
                )
                else "no successful documentation evidence"
            )
        if scope == "drc":
            return (
                None
                if any(name in self.successful_tools for name in {"drc_analyze", "drc_scan"})
                else "no successful DRC/LVS analysis evidence"
            )
        return f"unsupported request scope {scope!r}"


def _result_payload(result: SkillResult, max_chars: int = 16000) -> str:
    payload = {
        "ok": result.ok,
        "skill": result.skill,
        "summary": result.summary,
        "details": result.details,
        "errors": result.errors,
    }
    text = json.dumps(payload, default=str, sort_keys=True)
    if len(text) <= max_chars:
        return text
    compact = {
        "ok": result.ok,
        "skill": result.skill,
        "summary": result.summary,
        "errors": result.errors,
        "note": f"details truncated from {len(text)} characters",
    }
    return json.dumps(compact, default=str, sort_keys=True)


class AgentRunner:
    """Execute a deterministic workflow or a genuine model/tool loop."""

    def __init__(
        self,
        registry: AgentToolRegistry,
        events: EventStream,
        *,
        provider: Optional[ToolCallingProvider] = None,
        max_turns: int = 12,
        duplicate_limit: int = 2,
    ):
        self.registry = registry
        self.events = events
        self.provider = provider
        self.max_turns = max_turns
        self.duplicate_limit = duplicate_limit
        repo_root = Path(getattr(registry, "repo_root", Path.cwd())).resolve()
        self.state = AgentState(repo_root=repo_root)
        self._scope_required = False
        self._system_prompt = SYSTEM_PROMPT
        self._call_counts: Dict[str, int] = {}

    def run(
        self,
        request: str,
        *,
        driver: str,
        name: Optional[str] = None,
        dry_run: bool = False,
        required_evidence: str = "auto",
    ) -> SkillResult:
        session_id = uuid.uuid4().hex[:12]
        authorized_scope = (
            classify_request_scope(request)
            if required_evidence == "auto"
            else required_evidence
        )
        self.state.bind_request(request, authorized_scope)
        self.events.emit(
            "session_start",
            f"driver={driver} · scope={authorized_scope} · session={session_id} · {request}",
            details={
                "driver": driver,
                "session_id": session_id,
                "request": request,
                "authorized_scope": authorized_scope,
            },
        )
        try:
            if driver == "deterministic":
                self.state.required_scope = authorized_scope
                result = self._run_deterministic(
                    request,
                    name=name,
                    dry_run=dry_run,
                    authorized_scope=authorized_scope,
                )
            elif driver == "api":
                if self.provider is None:
                    result = SkillResult(
                        ok=False,
                        skill="agent",
                        summary="API agent has no configured provider",
                        errors=["run oh-my-soc setup --driver api"],
                    )
                else:
                    self.state.required_scope = authorized_scope
                    self.state.scope_locked = True
                    if required_evidence != "auto":
                        self.state.scope = authorized_scope
                    self._system_prompt = (
                        SYSTEM_PROMPT
                        + "\n\nThe harness derived and locked the authorized outcome scope to "
                        + repr(authorized_scope)
                        + ". Your request_scope call must use exactly this value."
                    )
                    result = self._run_model(request, dry_run=dry_run)
            else:
                result = SkillResult(
                    ok=False,
                    skill="agent",
                    summary=f"in-process runner does not handle driver '{driver}'",
                )
        except KeyboardInterrupt:
            result = SkillResult(
                ok=False,
                skill="agent",
                summary="agent cancelled by user",
                errors=["cancelled"],
            )
        except Exception as error:
            self.events.emit("error", str(error), status="error")
            result = SkillResult(
                ok=False,
                skill="agent",
                summary=f"agent failed: {error}",
                errors=[str(error)],
            )
        result.details.setdefault("agent", {})
        result.details["agent"].update(
            {
                "session_id": session_id,
                "driver": driver,
                "successful_tools": self.state.successful_tools,
                "failures": self.state.failures,
                "tool_results": self.state.tool_results,
                "event_count": self.events.event_count + 1,
                "request_scope": self.state.scope,
                "authorized_scope": authorized_scope,
            }
        )
        end_status = (
            "verified"
            if result.ok and bool(result.details.get("verified"))
            else "ok" if result.ok else "error"
        )
        self.events.emit(
            "session_end",
            result.summary,
            status=end_status,
            details={"result": result.to_json()},
        )
        return result

    def _run_deterministic(
        self,
        request: str,
        *,
        name: Optional[str],
        dry_run: bool,
        authorized_scope: str,
    ) -> SkillResult:
        if authorized_scope == "testbench":
            cores = sorted(self.state.requested_cores)
            self.state.scope = authorized_scope
            self.events.emit(
                "plan",
                "deterministic testbench evidence plan",
                details={
                    "steps": [
                        *(
                            ["Generate the requested canonical single-hart testbench"]
                            if self.state.testbench_writes_allowed
                            else []
                        ),
                        *(
                            ["Run the requested existing testbench and require TB PASS"]
                            if self.state.testbench_requires_run
                            else []
                        ),
                    ],
                    "cores": cores,
                },
            )
            if not cores:
                return SkillResult(
                    ok=False,
                    skill="agent",
                    summary="deterministic testbench request names no core",
                    errors=["name the core whose testbench should be generated or run"],
                )
            if dry_run:
                return SkillResult(
                    ok=True,
                    skill="agent",
                    summary=f"dry-run testbench plan complete for {', '.join(cores)}",
                    details={"cores": cores, "verified": False, "dry_run": True},
                )
            completed: List[str] = []
            for core in cores:
                if self.state.testbench_writes_allowed:
                    generated = self._invoke(
                        "tb_generate", {"core": core}, step=len(completed) + 1
                    )
                    if not generated.ok:
                        return self._failed(generated)
                if self.state.testbench_requires_run:
                    run = self._invoke(
                        "tb_run", {"core": core}, step=len(completed) + 1
                    )
                    if not run.ok:
                        return self._failed(run)
                completed.append(core)
            return SkillResult(
                ok=True,
                skill="agent",
                summary=(
                    f"testbench PASS for {', '.join(completed)}"
                    if self.state.testbench_requires_run
                    else f"generated testbench for {', '.join(completed)}"
                ),
                details={
                    "cores": completed,
                    "verified": self.state.testbench_requires_run,
                },
            )

        all_steps = [
            "Parse intent and report deterministic repairs",
            "Generate a schema-valid configuration",
            "Check topology and boot/memory semantics",
            "Render the isolated RTL bundle",
            "Run the full-SoC EXIT SUCCESS gate",
            "Summarize the verified configuration",
        ]
        stage_count = {
            "analysis": 1,
            "config": 2,
            "rtl": 4,
            "simulation": 6,
        }.get(authorized_scope, 1)
        steps = all_steps[:1] if dry_run else all_steps[:stage_count]
        self.events.emit("plan", "deterministic evidence plan", details={"steps": steps})
        plan = self._invoke("soc_plan", {"request": request}, step=1)
        if not plan.ok or dry_run or authorized_scope == "analysis":
            return SkillResult(
                ok=plan.ok,
                skill="agent",
                summary=(
                    f"dry-run plan complete: {plan.summary}"
                    if plan.ok and dry_run
                    else f"analysis complete without side effects: {plan.summary}"
                    if plan.ok
                    else f"planning failed: {plan.summary}"
                ),
                details={"plan": plan.details, "authorized_scope": authorized_scope},
                errors=plan.errors,
            )
        if authorized_scope not in {"config", "rtl", "simulation"}:
            return SkillResult(
                ok=False,
                skill="agent",
                summary=f"deterministic workflow does not own '{authorized_scope}' requests",
                errors=["use the API agent or invoke the matching deterministic skill directly"],
            )
        generated = self._invoke(
            "soc_generate",
            {"request": request, **({"name": name} if name else {})},
            step=2,
        )
        if not generated.ok:
            return self._failed(generated)
        config = generated.details.get("config", {}).get("path")
        if not config:
            return SkillResult(
                ok=False,
                skill="agent",
                summary="config generator returned no path",
                errors=["soc_generate contract violation"],
            )
        if authorized_scope == "config":
            return SkillResult(
                ok=True,
                skill="agent",
                summary=f"generated validated config from request: {config}",
                details={"config": config, "verified": False},
            )
        stages = [
            (3, "topology_check", {"path": config}),
            (4, "flow_run", {"flow": "mosaic-gen-config", "config": config}),
        ]
        if authorized_scope == "simulation":
            stages.extend(
                [
                    (5, "flow_run", {"flow": "tb-soc-generic", "config": config}),
                    (6, "doc_config", {"path": config}),
                ]
            )
        for step, tool, arguments in stages:
            result = self._invoke(tool, arguments, step=step)
            if not result.ok:
                return self._failed(result)
        if authorized_scope == "rtl":
            return SkillResult(
                ok=True,
                skill="agent",
                summary=f"generated RTL from request: {config}",
                details={"config": config, "verified": False},
            )
        return SkillResult(
            ok=True,
            skill="agent",
            summary=f"verified SoC from request; EXIT SUCCESS for {config}",
            details={"config": config, "verified": True},
        )

    def _run_model(self, request: str, *, dry_run: bool) -> SkillResult:
        assert self.provider is not None
        self._scope_required = True
        messages: List[Dict[str, Any]] = [{"role": "user", "content": request}]
        completion_reminders = 0
        for turn_index in range(1, self.max_turns + 1):
            self.events.emit(
                "thinking", f"model turn {turn_index}: choosing the next evidence step", step=turn_index
            )
            turn = self._provider_turn(messages)
            if not turn.terminal_seen:
                return SkillResult(
                    ok=False,
                    skill="agent",
                    summary="provider stream ended without a terminal event",
                    errors=["truncated provider stream"],
                )
            valid_reasons = (
                {"tool_calls", "tool_use"}
                if turn.calls
                else {"stop", "end_turn"}
            )
            if turn.stop_reason not in valid_reasons:
                return SkillResult(
                    ok=False,
                    skill="agent",
                    summary=f"provider stopped before completion: {turn.stop_reason}",
                    errors=["provider terminal condition was not a normal stop"],
                )
            assistant_record = {
                "role": "assistant",
                "content": turn.text,
                "tool_calls": [asdict(call) for call in turn.calls],
            }
            messages.append(assistant_record)
            if turn.calls:
                for call in turn.calls:
                    result = self._invoke(
                        call.name, call.arguments, step=turn_index, call_id=call.id
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": _result_payload(result),
                        }
                    )
                continue

            if dry_run and any(
                name != "request_scope" for name in self.state.successful_tools
            ):
                return SkillResult(
                    ok=True,
                    skill="agent",
                    summary=turn.text.strip() or "agent dry-run complete",
                    details={"verified": False, "dry_run": True},
                )
            completion_error = self.state.completion_error()
            if completion_error:
                completion_reminders += 1
                self.events.emit(
                    "recovery",
                    f"model attempted to finish with incomplete evidence: {completion_error}; continuing",
                    step=turn_index,
                    status="retry",
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Completion is blocked: {completion_error}. Continue with "
                            "the required typed tools and resolve failed calls; do not "
                            "claim completion from prose."
                        ),
                    }
                )
                if completion_reminders <= 2:
                    continue
                return SkillResult(
                    ok=False,
                    skill="agent",
                    summary="model repeatedly stopped before required evidence",
                    errors=[completion_error],
                )
            ok = bool(self.state.successful_tools)
            return SkillResult(
                ok=ok,
                skill="agent",
                summary=turn.text.strip() or "agent finished without a final explanation",
                details={
                    "verified": bool(
                        self.state.current_paths(self.state.verified_configs)
                    ),
                    "verified_configs": self.state.current_paths(
                        self.state.verified_configs
                    ),
                    "request_scope": self.state.scope,
                },
                errors=[] if ok else ["no successful tool evidence"],
            )
        return SkillResult(
            ok=False,
            skill="agent",
            summary=f"agent exceeded the {self.max_turns}-turn limit",
            errors=["bounded loop guard stopped the session"],
        )

    def _provider_turn(self, messages: List[Dict[str, Any]]) -> _Turn:
        assert self.provider is not None
        text_parts: List[str] = []
        calls: Dict[int, Dict[str, str]] = {}
        stop_reason = ""
        terminal_seen = False
        for event in self.provider.stream(
            self._system_prompt, messages, self.registry.schemas()
        ):
            if not isinstance(event, ProviderEvent):
                raise TypeError(f"provider yielded unsupported event {event!r}")
            if event.kind == "text_delta":
                text_parts.append(event.text)
                self.events.emit("assistant_delta", event.text)
            elif event.kind == "tool_delta":
                pending = calls.setdefault(
                    event.tool_index, {"id": "", "name": "", "arguments": ""}
                )
                pending["id"] = event.tool_id or pending["id"]
                pending["name"] += event.tool_name_delta
                pending["arguments"] += event.arguments_delta
            elif event.kind == "message_end":
                terminal_seen = True
                stop_reason = event.stop_reason
        parsed_calls: List[_Call] = []
        for index, pending in sorted(calls.items()):
            call_id = pending["id"] or f"call-{uuid.uuid4().hex[:10]}"
            try:
                arguments = json.loads(pending["arguments"] or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments are not an object")
            except (json.JSONDecodeError, ValueError) as error:
                arguments = {"__malformed_arguments__": str(error)}
            parsed_calls.append(
                _Call(call_id, pending["name"] or f"unknown_{index}", arguments)
            )
        return _Turn("".join(text_parts), parsed_calls, stop_reason, terminal_seen)

    def _invoke(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        step: int,
        call_id: Optional[str] = None,
    ) -> SkillResult:
        call_key = f"{name}:{json.dumps(dict(arguments), default=str, sort_keys=True)}"
        self._call_counts[call_key] = self._call_counts.get(call_key, 0) + 1
        self.events.emit(
            "decision",
            f"step {step}: call {name}",
            step=step,
            tool=name,
            details={"call_id": call_id},
        )
        self.events.emit(
            "tool_start",
            f"running {name}",
            step=step,
            tool=name,
            status="running",
            details={"arguments": dict(arguments), "call_id": call_id},
        )
        if self._call_counts[call_key] > self.duplicate_limit:
            result = SkillResult(
                ok=False,
                skill=name,
                summary=f"duplicate-call guard blocked repeated {name}",
                errors=[f"same call exceeded limit {self.duplicate_limit}"],
            )
        else:
            precondition = self._gate_precondition(name, arguments)
            if precondition is not None:
                result = precondition
            else:
                try:
                    result = self.registry.execute(
                        name,
                        arguments,
                        on_output=lambda line: self.events.emit(
                            "tool_output",
                            line,
                            step=step,
                            tool=name,
                            status="running",
                            details={"call_id": call_id},
                        ),
                    )
                except Exception as error:
                    result = SkillResult(
                        ok=False,
                        skill=name,
                        summary=f"tool '{name}' rejected the request: {error}",
                        errors=[str(error)],
                    )
        self.state.observe(name, arguments, result)
        self.events.emit(
            "gate" if name in {"topology_check", "flow_run"} else "tool_end",
            result.summary,
            step=step,
            tool=name,
            status="ok" if result.ok else "error",
            details={"call_id": call_id, "errors": result.errors},
        )
        return result

    # The gates themselves live in harness/gates.py so that the MCP session
    # enforces the same ones. Two copies of an authorization rule is one copy
    # too many; see that module's docstring.
    def _gate_precondition(
        self, name: str, arguments: Mapping[str, Any]
    ) -> Optional[SkillResult]:
        from .gates import gate_precondition

        return gate_precondition(
            self.state, self.registry, self._scope_required, name, arguments
        )

    def _scope_effect_precondition(
        self, name: str, arguments: Mapping[str, Any]
    ) -> Optional[SkillResult]:
        from .gates import scope_effect_precondition

        return scope_effect_precondition(
            self.state, self.registry, self._scope_required, name, arguments
        )

    @staticmethod
    def _failed(result: SkillResult) -> SkillResult:
        return SkillResult(
            ok=False,
            skill="agent",
            summary=f"stopped at failed gate: {result.summary}",
            errors=result.errors,
        )
