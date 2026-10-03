"""Typed, bounded tools exposed to the built-in oh-my-soc agent loop."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

from .core import REPO_ROOT, SkillResult


@dataclass(frozen=True)
class AgentToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    effect: str = "read"  # read | write | execute

    def wire_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


TOOL_SPECS = (
    AgentToolSpec(
        "request_scope",
        "Classify the user's requested outcome before acting. Respect explicit negation: analysis means no requested artifact, config means validated YAML, rtl means generated RTL, simulation means a passing executable test, and physical means a physical-design flow.",
        {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": [
                        "analysis",
                        "config",
                        "rtl",
                        "simulation",
                        "physical",
                        "integration",
                        "testbench",
                        "documentation",
                        "drc",
                    ],
                },
                "rationale": {"type": "string"},
            },
            "required": ["scope", "rationale"],
            "additionalProperties": False,
        },
    ),
    AgentToolSpec(
        "soc_plan",
        "Parse and repair a natural-language SoC request without writing files.",
        {
            "type": "object",
            "properties": {"request": {"type": "string"}},
            "required": ["request"],
            "additionalProperties": False,
        },
    ),
    AgentToolSpec(
        "soc_generate",
        "Generate a validated mosaic YAML from a natural-language SoC request.",
        {
            "type": "object",
            "properties": {
                "request": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["request"],
            "additionalProperties": False,
        },
        effect="write",
    ),
    AgentToolSpec(
        "config_generate",
        "Generate a MOSAIC YAML from an explicit structured topology when the natural-language parser needs correction.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "cores": {"type": "array"},
                "sram_kb": {"type": "integer"},
                "boot_rom_kb": {"type": "integer"},
                "bus": {"type": "string"},
                "tdu": {"type": "boolean"},
                "mode": {"type": "string"},
                "peripherals": {"type": "array"},
                "target": {"type": "string"},
                "output": {"type": "string"},
            },
            "required": ["name", "cores"],
            "additionalProperties": False,
        },
        effect="write",
    ),
    AgentToolSpec(
        "config_validate",
        "Validate an existing MOSAIC YAML against the authoritative schema.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
    AgentToolSpec(
        "topology_check",
        "Run semantic topology, boot-layout, memory-bank, and target checks.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
    AgentToolSpec(
        "flow_run",
        "Run one registered EDA/simulation flow with hard result gates.",
        {
            "type": "object",
            "properties": {
                "flow": {"type": "string"},
                "config": {"type": "string"},
            },
            "required": ["flow"],
            "additionalProperties": False,
        },
        effect="execute",
    ),
    AgentToolSpec(
        "flow_list",
        "List every registered EDA, simulation, firmware, and test flow.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    AgentToolSpec(
        "doc_config",
        "Generate a human-readable configuration summary.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
    AgentToolSpec(
        "drc_analyze",
        "Analyze a DRC/LVS report and return classified violations and fixes.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "format": {"type": "string"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
    AgentToolSpec(
        "drc_scan",
        "Scan a report directory and triage every recognized DRC/LVS report.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
    AgentToolSpec(
        "topology_render",
        "Render a checked MOSAIC topology as HTML or SVG.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "output": {"type": "string"},
                "svg": {"type": "boolean"},
            },
            "required": ["path", "output"],
            "additionalProperties": False,
        },
        effect="write",
    ),
    AgentToolSpec(
        "doc_dashboard",
        "Generate the harness/project status dashboard summary.",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "additionalProperties": False,
        },
    ),
    AgentToolSpec(
        "wrapper_analyze",
        "Analyze RTL ports and classify the native bus in memory. Set output to persist analysis JSON before wrapper scaffolding.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "top": {"type": "string"},
                "output": {"type": "string"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        effect="read",
    ),
    AgentToolSpec(
        "wrapper_scaffold",
        "Stage or explicitly apply a wrapper integration from an analysis JSON. Apply mode requires separate integration approval.",
        {
            "type": "object",
            "properties": {
                "core": {"type": "string"},
                "analysis": {"type": "string"},
                "vendor_from": {"type": "string"},
                "family": {"type": "string"},
                "apply": {"type": "boolean"},
            },
            "required": ["core", "analysis"],
            "additionalProperties": False,
        },
        effect="write",
    ),
    AgentToolSpec(
        "tb_generate",
        "Generate a self-checking single-core SCI testbench.",
        {
            "type": "object",
            "properties": {
                "core": {"type": "string"},
                "watchdog": {"type": "integer"},
            },
            "required": ["core"],
            "additionalProperties": False,
        },
        effect="write",
    ),
    AgentToolSpec(
        "tb_run",
        "Run an existing generated single-core SCI testbench.",
        {
            "type": "object",
            "properties": {
                "core": {"type": "string"},
                "timeout": {"type": "integer"},
            },
            "required": ["core"],
            "additionalProperties": False,
        },
        effect="execute",
    ),
    AgentToolSpec(
        "tb_wake_demo",
        "Run the generated core in the full-SoC TDU wake demo.",
        {
            "type": "object",
            "properties": {
                "core": {"type": "string"},
                "execute": {"type": "boolean"},
            },
            "required": ["core"],
            "additionalProperties": False,
        },
        effect="execute",
    ),
    AgentToolSpec(
        "tb_matrix_plan",
        "Enumerate the combination-coverage plan for a tier (validate/render/sim) without executing anything.",
        {
            "type": "object",
            "properties": {
                "tier": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        },
        effect="read",
    ),
    AgentToolSpec(
        "tb_matrix_run",
        "Execute a tb-matrix tier gate over the covering set (validate is in-process; render/sim run EDA flows). Resumes past configs that already passed.",
        {
            "type": "object",
            "properties": {
                "tier": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": [],
            "additionalProperties": False,
        },
        effect="execute",
    ),
    AgentToolSpec(
        "session_new",
        "Start a new gated session for the user's request, quoted verbatim. Clears all evidence from the previous request and returns the suggested scope, the scopes this installation allows, and which of them need a person's approval. Call it before request_scope whenever the user asks for something new.",
        {
            "type": "object",
            "properties": {"request": {"type": "string"}},
            "required": ["request"],
            "additionalProperties": False,
        },
        effect="read",
    ),
    AgentToolSpec(
        "session_status",
        "Report whether the current session's evidence satisfies its scope (done) or what is still missing. Call it before claiming success.",
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        effect="read",
    ),
)

#: Served by the MCP session (they act on its AgentState), never by the
#: registry. The in-process loop is bound to one request and does not list them.
SESSION_TOOLS = frozenset({"session_new", "session_status"})


def _validate_arguments(spec: AgentToolSpec, arguments: Mapping[str, Any]) -> list[str]:
    schema = spec.parameters
    errors = []
    required = set(schema.get("required", []))
    missing = required - set(arguments)
    if missing:
        errors.append(f"missing required fields: {sorted(missing)}")
    properties = schema.get("properties", {})
    unknown = set(arguments) - set(properties)
    if unknown and schema.get("additionalProperties") is False:
        errors.append(f"unknown fields: {sorted(unknown)}")
    expected_types = {
        "string": str,
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for key, value in arguments.items():
        property_schema = properties.get(key, {})
        expected = property_schema.get("type")
        if expected in expected_types and type(value) is not expected_types[expected]:
            errors.append(f"{key} must be {expected}, got {type(value).__name__}")
        allowed = property_schema.get("enum")
        if allowed is not None and value not in allowed:
            errors.append(f"{key} must be one of {allowed}, got {value!r}")
    return errors


def flow_needs_approval(flow: str) -> bool:
    """The flow's own declaration, never its name."""
    from .flow_spec import FlowSpec
    from .skills.flow_runner import FLOWS

    entry = FLOWS.get(flow)
    return entry is not None and FlowSpec.from_mapping(flow, entry).approval


def flow_scopes(flow: str) -> frozenset:
    from .flow_spec import FlowSpec
    from .skills.flow_runner import FLOWS

    entry = FLOWS.get(flow)
    return (FlowSpec.from_mapping(flow, entry).scopes
            if entry is not None else frozenset())


def tb_matrix_needs_approval(arguments: Mapping[str, Any]) -> bool:
    # validate runs in-process in seconds; render and sim launch EDA flows
    # over the whole covering array, which is what makes the skill HOURS.
    return str(arguments.get("tier", "validate")) != "validate"


class AgentToolRegistry:
    """Execute only registered harness operations; never arbitrary shell."""

    def __init__(
        self,
        repo_root: Path = REPO_ROOT,
        *,
        allow_write: bool = True,
        allow_execute: bool = True,
        allow_physical: bool = False,
        allow_integration: bool = False,
        approvals: Any = None,
    ):
        from .approvals import Approvals

        self.repo_root = repo_root
        self.allow_write = allow_write
        self.allow_execute = allow_execute
        # The two flags are static grants typed by a person at a TTY
        # (`agent --allow-physical`). Token files come in through `approvals`.
        static = ({"physical"} if allow_physical else set()) | (
            {"integration"} if allow_integration else set())
        base = approvals if approvals is not None else Approvals()
        self.approvals = Approvals(
            static=frozenset(base.static | static), token_dir=base.token_dir)
        self.specs = {spec.name: spec for spec in TOOL_SPECS}

    @property
    def allow_physical(self) -> bool:
        return self.approvals.granted("physical")

    @property
    def allow_integration(self) -> bool:
        return self.approvals.granted("integration")

    def schemas(self) -> list[Dict[str, Any]]:
        return [spec.wire_schema() for spec in TOOL_SPECS
                if spec.name not in SESSION_TOOLS]

    def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        on_output: Optional[Callable[[str], None]] = None,
    ) -> SkillResult:
        spec = self.specs.get(name)
        if spec is None:
            return SkillResult(
                ok=False,
                skill="agent",
                summary=f"unknown agent tool '{name}'",
                errors=[f"available: {sorted(self.specs)}"],
            )
        argument_errors = _validate_arguments(spec, arguments)
        if argument_errors:
            return SkillResult(
                ok=False,
                skill=name,
                summary=f"invalid arguments for {name}",
                errors=argument_errors,
            )
        if spec.effect == "write" and not self.allow_write:
            return SkillResult(
                ok=False,
                skill=name,
                summary=f"tool '{name}' needs write approval",
                errors=["agent session is read-only"],
            )
        if spec.effect == "execute" and not self.allow_execute:
            return SkillResult(
                ok=False,
                skill=name,
                summary=f"tool '{name}' needs execute approval",
                errors=["agent session is dry-run/read-only"],
            )

        if name in SESSION_TOOLS:
            return SkillResult(
                ok=False,
                skill=name,
                summary=f"{name} is served by the MCP session, not the registry",
                errors=["the in-process agent loop is bound to one request"],
            )
        if name == "request_scope":
            return SkillResult(
                ok=True,
                skill=name,
                summary=f"request scope: {arguments['scope']} — {arguments['rationale']}",
                details={"scope": arguments["scope"], "rationale": arguments["rationale"]},
            )
        if name == "soc_plan":
            from .skills.soc_from_prompt import SocFromPrompt

            return SocFromPrompt(self.repo_root).plan(str(arguments["request"]))
        if name == "soc_generate":
            from .skills.soc_from_prompt import SocFromPrompt

            return SocFromPrompt(self.repo_root).run(
                str(arguments["request"]),
                execute=False,
                name=str(arguments["name"]) if arguments.get("name") else None,
            )
        if name == "config_generate":
            from .skills.config_author import ConfigAuthor

            output = (
                self._write_path(arguments["output"], allowed_roots=("configs", "build"))
                if arguments.get("output")
                else None
            )
            return ConfigAuthor(self.repo_root).generate(
                name=str(arguments["name"]),
                cores=list(arguments["cores"]),
                sram_kb=int(arguments.get("sram_kb", 32)),
                boot_rom_kb=int(arguments.get("boot_rom_kb", 2)),
                bus=str(arguments.get("bus", "obi")),
                tdu=bool(arguments.get("tdu", False)),
                sched_mode=str(arguments.get("mode", "static")),
                peripherals=list(arguments.get("peripherals", ["uart"])),
                target=str(arguments.get("target", "rtl")),
                output_path=output,
            )
        if name == "config_validate":
            from .skills.config_author import ConfigAuthor

            return ConfigAuthor(self.repo_root).validate_file(
                self._path(arguments["path"])
            )
        if name == "topology_check":
            from .skills.topo_viz import TopoViz

            return TopoViz(self.repo_root).check(self._path(arguments["path"]))
        if name == "flow_run":
            from .skills.flow_runner import FlowRunner

            flow = str(arguments["flow"])
            # Defence in depth under the scope-aware gate: an approval flow
            # runs only if a person approved at least one scope it may run in.
            if flow_needs_approval(flow) and not any(
                    self.approvals.granted(s) for s in flow_scopes(flow)):
                return SkillResult(
                    ok=False,
                    skill=name,
                    summary=f"flow '{flow}' requires explicit approval",
                    errors=["rerun agent with --allow-physical, or have a "
                            "person run `oh-my-soc approve <scope>`"],
                )
            return FlowRunner(self.repo_root).run(
                flow,
                config=(str(arguments["config"]) if arguments.get("config") else None),
                on_output=on_output,
            )
        if name == "flow_list":
            from .skills.flow_runner import FlowRunner

            return FlowRunner(self.repo_root).list_flows()
        if name == "doc_config":
            from .skills.doc_gen import DocGen

            return DocGen(self.repo_root).config_summary(self._path(arguments["path"]))
        if name == "drc_analyze":
            from .skills.drc_triage import DRCTriage

            return DRCTriage(self.repo_root).analyze_file(
                self._path(arguments["path"]), fmt=arguments.get("format")
            )
        if name == "drc_scan":
            from .skills.drc_triage import DRCTriage

            return DRCTriage(self.repo_root).triage_directory(
                self._path(arguments["path"])
            )
        if name == "topology_render":
            from .skills.topo_viz import TopoViz

            return TopoViz(self.repo_root).render(
                self._path(arguments["path"]),
                output=self._write_path(
                    arguments["output"], allowed_roots=("build", "docs")
                ),
                svg_only=bool(arguments.get("svg", False)),
            )
        if name == "doc_dashboard":
            from .skills.doc_gen import DocGen

            return DocGen(self.repo_root).dashboard_summary(
                self._path(arguments["path"]) if arguments.get("path") else None
            )
        if name == "wrapper_analyze":
            from .skills.wrapper_smith import WrapperSmith

            output = arguments.get("output")
            if output and not self.allow_write:
                return SkillResult(
                    ok=False,
                    skill=name,
                    summary="persisting wrapper analysis needs write approval",
                    errors=["agent session is read-only"],
                )
            return WrapperSmith(self.repo_root).analyze(
                self._path(arguments["path"]),
                top=arguments.get("top"),
                out=(
                    self._write_path(
                        output, allowed_roots=("build/wrapper_smith",)
                    )
                    if output
                    else None
                ),
                persist=bool(output),
                allow_external_parsers=self.allow_execute,
            )
        if name == "wrapper_scaffold":
            from .skills.wrapper_smith import WrapperSmith

            apply = bool(arguments.get("apply", False))
            if apply and not self.approvals.granted("integration"):
                return SkillResult(
                    ok=False,
                    skill=name,
                    summary="wrapper apply requires explicit integration approval",
                    errors=["rerun agent with --allow-integration, or have a "
                            "person run `oh-my-soc approve integration`"],
                )
            return WrapperSmith(self.repo_root).scaffold(
                str(arguments["core"]),
                analysis=self._path(arguments["analysis"]),
                apply=apply,
                vendor_from=(
                    self._path(arguments["vendor_from"])
                    if arguments.get("vendor_from")
                    else None
                ),
                family_override=arguments.get("family"),
            )
        if name == "tb_generate":
            from .skills.tb_smith import TbSmith

            return TbSmith(self.repo_root).generate(
                str(arguments["core"]),
                watchdog_cycles=int(arguments.get("watchdog", 200_000)),
            )
        if name == "tb_run":
            from .skills.tb_smith import TbSmith

            return TbSmith(self.repo_root).run(
                str(arguments["core"]),
                timeout=int(arguments.get("timeout", 600)),
                on_output=on_output,
            )
        if name == "tb_wake_demo":
            from .skills.tb_smith import TbSmith

            return TbSmith(self.repo_root).wake_demo(
                str(arguments["core"]),
                execute=bool(arguments.get("execute", True)),
                on_output=on_output,
            )
        if name == "tb_matrix_plan":
            from .skills.tb_matrix import TbMatrix

            return TbMatrix(self.repo_root).plan(
                tier=str(arguments.get("tier", "render")),
            )
        if name == "tb_matrix_run":
            from .skills.tb_matrix import TbMatrix

            if tb_matrix_needs_approval(arguments) and not any(
                    self.approvals.granted(s)
                    for s in ("simulation", "integration", "physical")):
                return SkillResult(
                    ok=False,
                    skill=name,
                    summary="tb-matrix above the validate tier requires explicit approval",
                    errors=["have a person run `oh-my-soc approve simulation`"],
                )

            return TbMatrix(self.repo_root).run(
                tier=str(arguments.get("tier", "validate")),
                limit=(int(arguments["limit"])
                       if arguments.get("limit") is not None else None),
                on_output=on_output,
            )
        raise AssertionError(name)

    def _path(self, value: Any) -> Path:
        path = Path(str(value))
        path = (path if path.is_absolute() else self.repo_root / path).resolve()
        try:
            path.relative_to(self.repo_root.resolve())
        except ValueError as error:
            raise ValueError(
                f"agent path must stay inside {self.repo_root}: {path}"
            ) from error
        return path

    def _write_path(
        self, value: Any, *, allowed_roots: tuple[str, ...] = ("build",)
    ) -> Path:
        path = self._path(value)
        for root in allowed_roots:
            allowed = (self.repo_root / root).resolve()
            try:
                path.relative_to(allowed)
                return path
            except ValueError:
                continue
        raise ValueError(
            f"agent output must stay under one of {list(allowed_roots)}: {path}"
        )


def tool_names(specs: Iterable[AgentToolSpec] = TOOL_SPECS) -> list[str]:
    return [spec.name for spec in specs]
