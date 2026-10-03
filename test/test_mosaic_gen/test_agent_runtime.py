"""Agent-loop, event-stream, terminal, and live-process contracts."""

from __future__ import annotations

from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
import stat
import sys
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from harness.agent import (
    AgentRunner,
    _derive_config_contract,
    _requested_core_names,
    classify_request_scope,
)
from harness.agent_tools import AgentToolRegistry
from harness.__main__ import _external_agent_command
from harness.core import SkillResult, run_cmd
from harness.events import EventStream, JsonlJournal, JsonlRenderer, TerminalRenderer
from harness.llm import (
    AnthropicToolProvider,
    OpenAIToolProvider,
    ProviderEvent,
    ToolCallingProvider,
    _iter_sse,
)
from harness.skills.flow_runner import FLOWS


class ScriptedProvider(ToolCallingProvider):
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def stream(self, system, messages, tools):
        self.requests.append(json.loads(json.dumps(messages)))
        if not self.turns:
            raise AssertionError("provider called beyond script")
        yield from self.turns.pop(0)


class FailingProvider(ToolCallingProvider):
    def stream(self, system, messages, tools):
        raise RuntimeError("provider unavailable")
        yield  # pragma: no cover


class FakeRegistry:
    def __init__(self, results=None, repo_root=REPO_ROOT):
        self.results = list(results or [])
        self.calls = []
        self.repo_root = Path(repo_root)

    def schemas(self):
        return [
            {
                "name": "config_validate",
                "description": "validate",
                "input_schema": {"type": "object"},
            },
            {
                "name": "topology_check",
                "description": "check",
                "input_schema": {"type": "object"},
            },
            {
                "name": "flow_run",
                "description": "flow",
                "input_schema": {"type": "object"},
            },
        ]

    def execute(self, name, arguments, on_output=None):
        self.calls.append((name, dict(arguments)))
        if on_output:
            on_output(f"live:{name}")
        if self.results:
            return self.results.pop(0)
        return SkillResult(ok=True, skill=name, summary=f"{name} passed")


def test_topology_generic_full_soc_flow_is_registered():
    spec = FLOWS["tb-soc-generic"]
    assert spec["cmd"] == ["bash", "tb/mosaic_soc/run_generic.sh"]
    assert spec["require_exit_success"] is True


def _tool_turn(name, arguments, call_id="call-1", text="Inspecting…"):
    return [
        ProviderEvent("text_delta", text=text),
        ProviderEvent(
            "tool_delta",
            tool_id=call_id,
            tool_name_delta=name,
            arguments_delta=json.dumps(arguments),
        ),
        ProviderEvent("message_end", stop_reason="tool_calls"),
    ]


def _final_turn(text="Evidence complete."):
    return [
        ProviderEvent("text_delta", text=text),
        ProviderEvent("message_end", stop_reason="stop"),
    ]


@pytest.mark.parametrize(
    "prompt_text,scope",
    [
        ("do not build a SoC; only explain it", "analysis"),
        ("analysis only: tell me how to build a SoC", "analysis"),
        ("analyze the SoC without a build", "analysis"),
        ("analyze this RTL wrapper only", "analysis"),
        ("tell me whether the simulation passes, but do not run it", "analysis"),
        ("check the build without running anything", "analysis"),
        ("just inspect the wrapper integration", "analysis"),
        ("only inspect whether physical hardening would pass", "analysis"),
        ("I only want to know if the build passes", "analysis"),
        ("assess the physical hardening outlook", "analysis"),
        ("simulation status", "analysis"),
        ("wrapper integration status", "analysis"),
        ("how do I build a MOSAIC SoC?", "analysis"),
        ("inspect the topology and then run the wake demo", "simulation"),
        ("review the wrapper and then integrate it", "integration"),
        ("review the report and then triage the DRC", "drc"),
        ("run the simulation and report the results", "simulation"),
        ("run the existing testbench; do not regenerate the TB", "testbench"),
        ("could you build it", "simulation"),
        ("verify configs/mosaic.yaml", "simulation"),
        ("synthesize this design", "rtl"),
        ("run the full-SoC wake demo", "simulation"),
        ("build it", "simulation"),
        ("an SoC with one cv32e20 and two serv cores", "simulation"),
        ("generate a YAML config", "config"),
        ("integrate a new AHB core wrapper", "integration"),
        ("generate a single-hart testbench", "testbench"),
        ("document and visualize this config", "documentation"),
        ("triage this DRC report", "drc"),
        ("harden this design to GDS", "physical"),
    ],
)
def test_user_request_scope_classifier_is_conservative(prompt_text, scope):
    assert classify_request_scope(prompt_text) == scope


def test_agent_multi_turn_tool_result_is_returned_to_provider():
    provider = ScriptedProvider(
        [
            _tool_turn("config_validate", {"path": "mosaic.yaml"}),
            _final_turn(),
        ]
    )
    registry = FakeRegistry()
    stream = EventStream()
    result = AgentRunner(registry, stream, provider=provider).run(
        "analyze this configuration", driver="api", required_evidence="analysis"
    )

    assert result.ok
    assert registry.calls == [("config_validate", {"path": "mosaic.yaml"})]
    second_request = provider.requests[1]
    tool_result = next(message for message in second_request if message["role"] == "tool")
    assert tool_result["tool_call_id"] == "call-1"
    assert json.loads(tool_result["content"])["ok"] is True
    kinds = [event.kind for event in stream.events]
    assert kinds.index("tool_start") < kinds.index("tool_output") < kinds.index("tool_end")
    assert kinds[-1] == "session_end"


def test_provider_failure_is_a_clean_agent_result():
    stream = EventStream()
    result = AgentRunner(FakeRegistry(), stream, provider=FailingProvider()).run(
        "analyze config", driver="api"
    )
    assert not result.ok
    assert "provider unavailable" in result.summary
    assert any(event.kind == "error" for event in stream.events)
    assert stream.events[-1].kind == "session_end"


def test_truncated_provider_stream_cannot_finish_after_successful_evidence():
    provider = ScriptedProvider(
        [
            _tool_turn("config_validate", {"path": "mosaic.yaml"}),
            [ProviderEvent("text_delta", text="Looks complete")],
        ]
    )
    result = AgentRunner(FakeRegistry(), EventStream(), provider=provider).run(
        "inspect config", driver="api", required_evidence="analysis"
    )
    assert not result.ok
    assert "without a terminal event" in result.summary


def test_unknown_and_malformed_model_tools_are_observations_not_execution():
    provider = ScriptedProvider(
        [
            _tool_turn("raw_shell", {"command": "rm -rf /"}, "unknown"),
            [
                ProviderEvent("tool_delta", tool_id="bad", tool_name_delta="config_validate", arguments_delta="{"),
                ProviderEvent("message_end", stop_reason="tool_calls"),
            ],
            _final_turn("Stopped after safe tool errors."),
            _final_turn("The failures remain unresolved."),
            _final_turn("Cannot claim success."),
        ]
    )
    registry = AgentToolRegistry(allow_write=False, allow_execute=False)
    result = AgentRunner(registry, EventStream(), provider=provider).run(
        "inspect only", driver="api", required_evidence="analysis"
    )
    assert not result.ok
    assert any("unknown agent tool" in failure for failure in result.details["agent"]["failures"])
    assert any("invalid arguments" in failure for failure in result.details["agent"]["failures"])


def test_failed_gate_is_observed_and_cannot_be_bypassed(tmp_path):
    config = tmp_path / "x.yaml"
    config.write_text("soc: {name: x}\n")
    provider = ScriptedProvider(
        [
            _tool_turn(
                "flow_run",
                    {"flow": "mosaic-gen-config", "config": str(config)},
                "bad-flow",
            ),
            _tool_turn(
                "topology_check", {"path": str(config)}, "topology"
            ),
            _tool_turn(
                "flow_run",
                    {"flow": "mosaic-gen-config", "config": str(config)},
                "good-flow",
            ),
            _final_turn("Recovered after respecting the gate."),
        ]
    )
    registry = FakeRegistry(repo_root=tmp_path)
    stream = EventStream()
    result = AgentRunner(registry, stream, provider=provider).run(
        f"inspect and render {config}", driver="api", required_evidence="rtl"
    )

    assert result.ok
    # The premature flow never reached the executor; its failed observation did
    # reach the next provider turn, which then selected topology_check.
    assert registry.calls == [
        ("topology_check", {"path": str(config)}),
        ("flow_run", {"flow": "mosaic-gen-config", "config": str(config)}),
    ]
    first_tool_result = next(
        message for message in provider.requests[1] if message["role"] == "tool"
    )
    assert json.loads(first_tool_result["content"])["ok"] is False
    assert "blocked until topology_check" in first_tool_result["content"]


def test_build_request_cannot_finish_before_full_soc_evidence(tmp_path):
    config_path = tmp_path / "generated.yaml"
    config_path.write_text("soc: {name: generated}\n")
    config = str(config_path)
    provider = ScriptedProvider(
        [
            _final_turn("Looks done."),
            _tool_turn("topology_check", {"path": config}, "topology"),
            _tool_turn(
                "flow_run",
                {"flow": "mosaic-gen-config", "config": config},
                "generate",
            ),
            _tool_turn(
                "flow_run",
                {"flow": "tb-soc-wake", "config": config},
                "verify",
            ),
            _final_turn("The full-SoC gate is now proven."),
        ]
    )
    registry = FakeRegistry(repo_root=tmp_path)
    events = EventStream()
    result = AgentRunner(registry, events, provider=provider).run(
        f"build and verify {config}", driver="api", required_evidence="simulation"
    )
    assert result.ok
    assert result.details["verified"] is True
    assert result.details["verified_configs"] == [config]
    assert any(event.kind == "recovery" for event in events.events)


def test_duplicate_call_loop_guard_stops_execution():
    repeated = _tool_turn("config_validate", {"path": "x.yaml"})
    provider = ScriptedProvider(
        [repeated, repeated, repeated, _final_turn(), _final_turn(), _final_turn()]
    )
    registry = FakeRegistry()
    result = AgentRunner(
        registry, EventStream(), provider=provider, duplicate_limit=2
    ).run("inspect config", driver="api", required_evidence="analysis")
    assert not result.ok
    assert len(registry.calls) == 2
    assert any("duplicate-call guard" in failure for failure in result.details["agent"]["failures"])


def test_successful_alternative_call_resolves_same_tool_failure():
    provider = ScriptedProvider(
        [
            _tool_turn("config_validate", {"path": "bad.yaml"}, "bad"),
            _tool_turn("config_validate", {"path": "good.yaml"}, "good"),
            _final_turn("Recovered with the corrected path."),
        ]
    )
    registry = FakeRegistry(
        [
            SkillResult(ok=False, skill="config_validate", summary="bad config"),
            SkillResult(ok=True, skill="config_validate", summary="valid config"),
        ]
    )
    result = AgentRunner(registry, EventStream(), provider=provider).run(
        "validate a config", driver="api", required_evidence="analysis"
    )
    assert result.ok
    assert result.details["agent"]["failures"] == ["config_validate: bad config"]


def test_auto_scope_must_be_explicit_before_other_tools():
    provider = ScriptedProvider(
        [
            _tool_turn(
                "request_scope",
                {"scope": "analysis", "rationale": "the user requested inspection only"},
                "scope",
            ),
            _tool_turn("config_validate", {"path": "mosaic.yaml"}, "validate"),
            _final_turn("Validated without requesting side effects."),
        ]
    )
    result = AgentRunner(FakeRegistry(), EventStream(), provider=provider).run(
        "do not build a SoC; only inspect the config", driver="api"
    )
    assert result.ok
    assert result.details["agent"]["request_scope"] == "analysis"


def test_model_cannot_widen_user_derived_scope():
    registry = FakeRegistry()
    runner = AgentRunner(registry, EventStream())
    runner.state.required_scope = "analysis"
    runner.state.scope_locked = True
    runner._scope_required = True
    denied = runner._invoke(
        "request_scope",
        {"scope": "physical", "rationale": "model wants more authority"},
        step=1,
    )
    assert not denied.ok
    assert registry.calls == []


def test_deterministic_analysis_request_has_no_side_effects():
    registry = FakeRegistry()
    result = AgentRunner(registry, EventStream()).run(
        "do not build a SoC; only explain one serv worker",
        driver="deterministic",
    )
    assert result.ok
    assert registry.calls == [("soc_plan", {"request": "do not build a SoC; only explain one serv worker"})]


def test_deterministic_existing_testbench_run_does_not_regenerate():
    registry = FakeRegistry()
    result = AgentRunner(registry, EventStream()).run(
        "run the serv testbench; do not regenerate the testbench",
        driver="deterministic",
    )

    assert result.ok
    assert result.details["verified"] is True
    assert registry.calls == [("tb_run", {"core": "serv"})]


def test_tool_specific_scope_blocks_unrelated_writes_and_simulation():
    registry = FakeRegistry()
    runner = AgentRunner(registry, EventStream())
    runner.state.required_scope = "config"
    runner.state.scope = "config"
    runner.state.scope_locked = True
    assert not runner._invoke(
        "wrapper_scaffold",
        {"core": "x", "analysis": "analysis.json"},
        step=1,
    ).ok
    assert not runner._invoke("tb_run", {"core": "serv"}, step=2).ok
    assert registry.calls == []


def test_verify_existing_artifacts_cannot_regenerate_sources(tmp_path):
    config = tmp_path / "configs" / "existing.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("soc: {name: existing}\n")
    registry = FakeRegistry(repo_root=tmp_path)
    runner = AgentRunner(registry, EventStream())
    runner.state.required_scope = "simulation"
    runner.state.scope = "simulation"
    runner.state.user_request = f"verify {config}"
    runner.state.config_writes_allowed = False
    runner.state.testbench_writes_allowed = False
    runner.state.wake_demo_allowed = False

    assert not runner._invoke(
        "config_generate",
        {"name": "replacement", "cores": []},
        step=1,
    ).ok
    assert not runner._invoke(
        "soc_generate", {"request": runner.state.user_request}, step=2
    ).ok
    assert not runner._invoke("tb_generate", {"core": "serv"}, step=3).ok
    assert not runner._invoke(
        "tb_wake_demo", {"core": "serv", "execute": True}, step=4
    ).ok
    assert registry.calls == []


def test_generic_existing_testbench_run_does_not_authorize_wake_config_write():
    runner = AgentRunner(FakeRegistry(), EventStream(), provider=FailingProvider())

    runner.run("run the serv testbench", driver="api")

    assert runner.state.required_scope == "testbench"
    assert not runner.state.testbench_writes_allowed
    assert not runner.state.wake_demo_allowed


@pytest.mark.parametrize(
    "prompt_text,config_write,tb_write",
    [
        ("verify configs/mosaic.yaml; do not update the config", False, False),
        ("verify configs/mosaic.yaml; do not regenerate YAML", False, False),
        ("run serv testbench; do not regenerate the testbench", True, False),
    ],
)
def test_explicit_write_negation_dominates_generation_words(
    prompt_text, config_write, tb_write
):
    runner = AgentRunner(FakeRegistry(), EventStream(), provider=FailingProvider())

    runner.run(prompt_text, driver="api")

    assert runner.state.config_writes_allowed is config_write
    assert runner.state.testbench_writes_allowed is tb_write


def test_requested_existing_config_digest_cannot_be_replaced_before_pass(tmp_path):
    config = tmp_path / "configs" / "existing.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("soc: {name: existing, cores: []}\n")
    runner = AgentRunner(FakeRegistry(repo_root=tmp_path), EventStream())
    runner.state.required_scope = "simulation"
    runner.state.scope = "simulation"
    runner.state.requested_configs = {str(config)}
    path, digest = runner.state.fingerprint(config)
    assert digest
    runner.state.requested_config_initial[path] = digest
    runner.state.config_writes_allowed = False

    config.write_text("soc: {name: replacement, cores: []}\n")
    runner.state.observe(
        "flow_run",
        {"flow": "tb-soc-wake", "config": str(config)},
        SkillResult(True, "flow-runner", "EXIT SUCCESS"),
    )
    assert "target-bound" in runner.state.completion_error()


@pytest.mark.parametrize(
    "prompt_text,core",
    [
        ("integrate foobar", "foobar"),
        ("run the foobar testbench", "foobar"),
        ("generate a testbench for foobar", "foobar"),
        ("run foobar's testbench", "foobar"),
        ("integrate a core called foobar", "foobar"),
    ],
)
def test_unknown_requested_core_names_are_bound(prompt_text, core):
    assert core in _requested_core_names(prompt_text)


def test_prompt_contract_binds_platform_and_per_core_parameters():
    cores, soc, explicit, preset = _derive_config_contract(
        "build one cv32e20 controller and one fazyrv worker with chunksize 4, "
        "64 KB SRAM, log bus, TDU dynamic, uart and gpio"
    )

    assert preset is None
    assert soc == {
        "sram_kb": 64,
        "boot_rom_kb": 2,
        "bus": "log",
        "tdu": True,
        "mode": "dynamic",
        "peripherals": ["gpio", "uart"],
    }
    fazy = next(core for core in cores if core["ip"] == "fazyrv")
    assert fazy["isa"] == "rv32i"
    assert fazy["chunksize"] == 4
    assert {"cores", "sram_kb", "bus", "tdu", "mode", "peripherals", "core_params"} <= explicit


def test_analysis_completion_requires_request_relevant_tool():
    registry = FakeRegistry()
    runner = AgentRunner(registry, EventStream())
    runner.state.required_scope = "analysis"
    runner.state.scope = "analysis"
    runner.state.required_analysis_tools = {"wrapper_analyze"}

    assert runner._invoke("flow_list", {}, step=1).ok
    assert "request-relevant" in runner.state.completion_error()
    assert runner._invoke(
        "wrapper_analyze", {"path": "hw/vendor/example.sv"}, step=2
    ).ok
    assert runner.state.completion_error() is None


def test_analysis_scope_blocks_persisted_wrapper_output():
    registry = FakeRegistry()
    runner = AgentRunner(registry, EventStream())
    runner.state.required_scope = "analysis"
    runner.state.scope = "analysis"

    result = runner._invoke(
        "wrapper_analyze",
        {"path": "hw/vendor/example.sv", "output": "build/wrapper_smith/x.json"},
        step=1,
    )

    assert not result.ok
    assert "cannot persist" in result.summary
    assert registry.calls == []


def test_wrapper_analysis_can_remain_in_memory_in_read_only_session(tmp_path):
    rtl = tmp_path / "tiny.sv"
    rtl.write_text(
        "module tiny(input logic clk_i, input logic rst_ni, "
        "output logic req_o); endmodule\n"
    )
    registry = AgentToolRegistry(
        repo_root=tmp_path, allow_write=False, allow_execute=False
    )

    result = registry.execute("wrapper_analyze", {"path": "tiny.sv"})

    assert result.ok
    assert result.details["analysis_path"] is None
    assert not (tmp_path / "build").exists()


def test_agent_wrapper_output_cannot_overwrite_arbitrary_repo_file(tmp_path):
    rtl = tmp_path / "tiny.sv"
    rtl.write_text("module tiny(input logic clk_i); endmodule\n")
    readme = tmp_path / "README.md"
    readme.write_text("keep me\n")
    runner = AgentRunner(AgentToolRegistry(repo_root=tmp_path), EventStream())
    runner.state.required_scope = "integration"
    runner.state.scope = "integration"

    result = runner._invoke(
        "wrapper_analyze",
        {"path": "tiny.sv", "output": "README.md"},
        step=1,
    )

    assert not result.ok
    assert "build/wrapper_smith" in result.summary
    assert readme.read_text() == "keep me\n"


def test_integration_completion_requires_applied_current_files_and_smoke(tmp_path):
    analysis = tmp_path / "analysis.json"
    analysis.write_text("{}\n")
    source = tmp_path / "source.sv"
    source.write_text("module source; endmodule\n")
    stage_file = tmp_path / "stage" / "hw" / "sci" / "new_core_sci.sv"
    stage_file.parent.mkdir(parents=True)
    stage_file.write_text("// staged\n")
    applied_file = tmp_path / "hw" / "sci" / "new_core_sci.sv"
    applied_file.parent.mkdir(parents=True)
    applied_file.write_text("// applied\n")
    vendor_core = tmp_path / "hw" / "vendor" / "mosaic" / "new_core" / "new_core.core"
    vendor_core.parent.mkdir(parents=True)
    vendor_core.write_text("CAPI=2:\nname: mosaic:ip:new_core\n")
    tb_dir = tmp_path / "tb" / "sci" / "new_core"
    tb_dir.mkdir(parents=True)
    (tb_dir / "run.sh").write_text("#!/bin/sh\n")
    (tb_dir / "deps.f").write_text("\n")
    (tb_dir / "tb_new_core_sci.sv").write_text("module tb; endmodule\n")
    full_config = tmp_path / "configs" / "mosaic_new_core.yaml"
    full_config.parent.mkdir(parents=True)
    full_config.write_text(
        "soc:\n  cores:\n    - {ip: new_core, role: atlas, count: 1}\n"
    )
    registry = FakeRegistry(
        [
            SkillResult(
                True,
                "wrapper-smith",
                "staged",
                details={
                    "stage": str(tmp_path / "stage"),
                    "written": ["hw/sci/new_core_sci.sv"],
                    "edited": [],
                },
            ),
            SkillResult(
                True,
                "wrapper-smith",
                "applied",
                details={
                    "stage": str(tmp_path / "stage"),
                    "written": ["hw/sci/new_core_sci.sv"],
                    "edited": [],
                    "fusesoc_smoke": {"ok": True},
                },
            ),
        ],
        repo_root=tmp_path,
    )
    runner = AgentRunner(registry, EventStream())
    runner.state.required_scope = "integration"
    runner.state.scope = "integration"
    runner.state.integration_requires_apply = True
    analysis_path, analysis_digest = runner.state.fingerprint(analysis)
    assert analysis_digest
    runner.state.wrapper_analyses[analysis_path] = analysis_digest
    source_path, source_digest = runner.state.fingerprint(source)
    assert source_digest
    runner.state.wrapper_analysis_inputs[analysis_path] = {
        source_path: source_digest
    }
    runner.state.wrapper_analysis_roots[analysis_path] = str(vendor_core.parent)

    assert runner._invoke(
        "wrapper_scaffold",
        {"core": "new_core", "analysis": str(analysis)},
        step=1,
    ).ok
    assert "applied wrapper" in runner.state.completion_error()
    assert runner._invoke(
        "wrapper_scaffold",
        {"core": "new_core", "analysis": str(analysis), "apply": True},
        step=2,
    ).ok
    assert "applied wrapper" in runner.state.completion_error()
    runner.state.observe(
        "tb_generate",
        {"core": "new_core"},
        SkillResult(
            True,
            "tb-smith",
            "generated",
            details={
                "dir": str(tb_dir),
                "files": ["run.sh", "deps.f", "tb_new_core_sci.sv"],
            },
        ),
    )
    runner.state.observe(
        "tb_run",
        {"core": "new_core"},
        SkillResult(True, "tb-smith", "TB PASS"),
    )
    runner.state.observe(
        "flow_run",
        {"flow": "tb-soc-wake", "config": str(full_config)},
        SkillResult(True, "flow-runner", "EXIT SUCCESS"),
    )
    assert "applied wrapper" in runner.state.completion_error()
    runner.state.observe(
        "flow_run",
        {"flow": "tb-soc-generic", "config": str(full_config)},
        SkillResult(True, "flow-runner", "EXIT SUCCESS"),
    )
    assert runner.state.completion_error() is None

    analysis.write_text('{"family": "changed"}\n')
    _, replacement_digest = runner.state.fingerprint(analysis)
    assert replacement_digest
    runner.state.wrapper_analyses[analysis_path] = replacement_digest
    assert "applied wrapper" in runner.state.completion_error()
    analysis.write_text("{}\n")
    runner.state.wrapper_analyses[analysis_path] = analysis_digest
    assert runner.state.completion_error() is None

    applied_file.write_text("// changed after smoke\n")
    assert "applied wrapper" in runner.state.completion_error()
    runner.state.observe(
        "wrapper_scaffold",
        {"core": "new_core", "analysis": str(analysis)},
        SkillResult(
            True,
            "wrapper-smith",
            "fresh dry scaffold",
            details={
                "stage": str(tmp_path / "stage"),
                "written": ["hw/sci/new_core_sci.sv"],
                "edited": [],
            },
        ),
    )
    assert "applied wrapper" in runner.state.completion_error()


def test_testbench_run_request_is_not_satisfied_by_generation(tmp_path):
    tb_dir = tmp_path / "tb" / "sci" / "new_core"
    tb_dir.mkdir(parents=True)
    run_script = tb_dir / "run.sh"
    run_script.write_text("#!/bin/sh\n")
    registry = FakeRegistry(
        [
            SkillResult(
                True,
                "tb-smith",
                "generated",
                details={"dir": str(tb_dir), "files": ["run.sh"]},
            ),
            SkillResult(
                True,
                "tb-smith",
                "TB PASS",
                details={"metrics": {"pass": True}},
            ),
        ],
        repo_root=tmp_path,
    )
    runner = AgentRunner(registry, EventStream())
    runner.state.required_scope = "testbench"
    runner.state.scope = "testbench"
    runner.state.testbench_requires_run = True
    runner.state.testbench_writes_allowed = True

    assert runner._invoke("tb_generate", {"core": "new_core"}, step=1).ok
    assert "passing testbench run" in runner.state.completion_error()
    runner.state.testbench_requires_run = False
    assert runner.state.completion_error() is None
    runner.state.testbench_requires_run = True
    assert runner._invoke("tb_run", {"core": "new_core"}, step=2).ok
    assert runner.state.completion_error() is None

    run_script.write_text("#!/bin/sh\n# changed\n")
    assert "passing testbench run" in runner.state.completion_error()


def test_testbench_generation_and_run_source_snapshots_are_operation_atomic(tmp_path):
    tb_dir = tmp_path / "tb" / "sci" / "new_core"
    tb_dir.mkdir(parents=True)
    for name, content in {
        "run.sh": "#!/bin/sh\n",
        "deps.f": "\n",
        "tb_new_core_sci.sv": "module tb; endmodule\n",
    }.items():
        (tb_dir / name).write_text(content)
    wrapper = tmp_path / "hw" / "sci" / "new_core_sci.sv"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("module dut; endmodule\n")
    runner = AgentRunner(FakeRegistry(repo_root=tmp_path), EventStream())

    runner.state.observe(
        "tb_generate",
        {"core": "new_core"},
        SkillResult(
            True,
            "tb-smith",
            "generated",
            details={
                "dir": str(tb_dir),
                "files": ["run.sh", "deps.f", "tb_new_core_sci.sv"],
            },
        ),
    )
    wrapper.write_text("module dut; wire changed; endmodule\n")
    runner.state.observe(
        "tb_run",
        {"core": "new_core"},
        SkillResult(True, "tb-smith", "TB PASS"),
    )

    assert "new_core" not in runner.state._current_generated_testbench_cores()
    assert "new_core" in runner.state._current_unit_testbench_run_cores()


def test_only_generic_full_soc_flow_counts_for_integration(tmp_path):
    config = tmp_path / "configs" / "core.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "soc:\n  cores:\n    - {ip: new_core, role: atlas, count: 1}\n"
    )
    runner = AgentRunner(FakeRegistry(repo_root=tmp_path), EventStream())
    passed = SkillResult(True, "flow-runner", "EXIT SUCCESS")

    runner.state.observe(
        "flow_run", {"flow": "tb-soc-wake", "config": str(config)}, passed
    )
    assert runner.state._current_full_soc_cores() == set()
    runner.state.observe(
        "flow_run", {"flow": "tb-soc-generic", "config": str(config)}, passed
    )
    assert runner.state._current_full_soc_cores() == {"new_core"}


def test_simulation_completion_is_bound_to_requested_config(tmp_path):
    requested = tmp_path / "configs" / "requested.yaml"
    unrelated = tmp_path / "configs" / "unrelated.yaml"
    requested.parent.mkdir(parents=True)
    requested.write_text("soc: {name: requested}\n")
    unrelated.write_text("soc: {name: unrelated}\n")
    runner = AgentRunner(FakeRegistry(repo_root=tmp_path), EventStream())
    runner.state.required_scope = "simulation"
    runner.state.scope = "simulation"
    runner.state.requested_configs = {str(requested)}
    passed = SkillResult(True, "flow-runner", "EXIT SUCCESS")

    runner.state.observe(
        "flow_run", {"flow": "tb-soc-wake", "config": str(unrelated)}, passed
    )
    assert "target-bound" in runner.state.completion_error()
    runner.state.observe(
        "flow_run", {"flow": "tb-soc-wake", "config": str(requested)}, passed
    )
    assert runner.state.completion_error() is None


def test_natural_topology_completion_checks_planned_cores_counts_and_roles(tmp_path):
    wrong = tmp_path / "configs" / "wrong.yaml"
    correct = tmp_path / "configs" / "correct.yaml"
    wrong.parent.mkdir(parents=True)
    wrong.write_text(
        "soc:\n  name: wrong\n  cores:\n"
        "    - {ip: picorv32, role: nano, count: 1, isa: rv32i, boot_addr: 4096}\n"
        "  memory: {sram_kb: 64, boot_rom_kb: 2}\n"
        "  bus: log\n  scheduler: {tdu: false, mode: dynamic}\n"
        "  peripherals: [gpio]\n"
    )
    correct.write_text(
        "soc:\n  name: correct\n  cores:\n"
        "    - {ip: serv, role: nano, count: 2, isa: rv32i, boot_addr: 4096}\n"
        "  memory: {sram_kb: 32, boot_rom_kb: 2}\n"
        "  bus: obi\n  scheduler: {tdu: true, mode: static}\n"
        "  peripherals: [uart]\n"
    )
    runner = AgentRunner(FakeRegistry(repo_root=tmp_path), EventStream())
    runner.state.required_scope = "simulation"
    runner.state.scope = "simulation"
    runner.state.planned_request_ok = True
    runner.state.planned_topology = [("serv", "nano", 2)]
    runner.state.planned_core_contracts = [
        {
            "ip": "serv",
            "role": "nano",
            "count": 2,
            "isa": "rv32i",
            "boot_addr": 4096,
        }
    ]
    runner.state.planned_soc_contract = {
        "sram_kb": 32,
        "boot_rom_kb": 2,
        "bus": "obi",
        "tdu": True,
        "mode": "static",
        "peripherals": ["uart"],
    }
    passed = SkillResult(True, "flow-runner", "EXIT SUCCESS")

    runner.state.observe(
        "config_generate", {}, SkillResult(True, "config-author", "ok", details={"path": str(wrong)})
    )
    runner.state.observe(
        "flow_run", {"flow": "tb-soc-wake", "config": str(wrong)}, passed
    )
    assert "target-bound" in runner.state.completion_error()

    runner.state.observe(
        "config_generate", {}, SkillResult(True, "config-author", "ok", details={"path": str(correct)})
    )
    runner.state.observe(
        "flow_run", {"flow": "tb-soc-wake", "config": str(correct)}, passed
    )
    assert runner.state.completion_error() is None


def test_integration_completion_is_bound_to_requested_core(tmp_path):
    runner = AgentRunner(FakeRegistry(repo_root=tmp_path), EventStream())
    runner.state.required_scope = "integration"
    runner.state.scope = "integration"
    runner.state.requested_cores = {"hazard3"}
    for core in ("other", "hazard3"):
        analysis = tmp_path / "build" / "wrapper_smith" / f"{core}.json"
        analysis.parent.mkdir(parents=True, exist_ok=True)
        analysis.write_text("{}\n")
        source = tmp_path / f"{core}.sv"
        source.write_text(f"module {core}; endmodule\n")
        analysis_path, analysis_digest = runner.state.fingerprint(analysis)
        source_path, source_digest = runner.state.fingerprint(source)
        assert analysis_digest and source_digest
        runner.state.wrapper_analyses[analysis_path] = analysis_digest
        runner.state.wrapper_analysis_inputs[analysis_path] = {
            source_path: source_digest
        }
        wrapper = tmp_path / "hw" / "sci" / f"{core}_sci.sv"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text(f"module {core}_sci; endmodule\n")
        vendor_core = (
            tmp_path / "hw" / "vendor" / "mosaic" / core / f"{core}.core"
        )
        vendor_core.parent.mkdir(parents=True, exist_ok=True)
        vendor_core.write_text(f"CAPI=2:\nname: mosaic:ip:{core}\n")
        runner.state.wrapper_analysis_roots[analysis_path] = str(vendor_core.parent)
        tb_dir = tmp_path / "tb" / "sci" / core
        tb_dir.mkdir(parents=True, exist_ok=True)
        (tb_dir / "run.sh").write_text("#!/bin/sh\n")
        (tb_dir / "deps.f").write_text("\n")
        (tb_dir / f"tb_{core}_sci.sv").write_text("module tb; endmodule\n")
        full_config = tmp_path / "configs" / f"mosaic_{core}.yaml"
        full_config.parent.mkdir(parents=True, exist_ok=True)
        full_config.write_text(
            f"soc:\n  cores:\n    - {{ip: {core}, role: atlas, count: 1}}\n"
        )
        runner.state.observe(
            "wrapper_scaffold",
            {"core": core, "analysis": str(analysis), "apply": True},
            SkillResult(
                True,
                "wrapper-smith",
                "applied",
                details={
                    "stage": str(tmp_path / "build" / core),
                    "written": [f"hw/sci/{core}_sci.sv"],
                    "edited": [],
                    "fusesoc_smoke": {"ok": True},
                },
            ),
        )
        runner.state.observe(
            "tb_generate",
            {"core": core},
            SkillResult(
                True,
                "tb-smith",
                "generated",
                details={
                    "dir": str(tb_dir),
                    "files": ["run.sh", "deps.f", f"tb_{core}_sci.sv"],
                },
            ),
        )
        runner.state.observe(
            "tb_run", {"core": core}, SkillResult(True, "tb-smith", "TB PASS")
        )
        runner.state.observe(
            "flow_run",
            {"flow": "tb-soc-generic", "config": str(full_config)},
            SkillResult(True, "flow-runner", "EXIT SUCCESS"),
        )
        if core == "other":
            assert "target-bound" in runner.state.completion_error()
    assert runner.state.completion_error() is None


def test_staged_wrapper_is_bound_to_analyzed_source_lineage(tmp_path):
    source = tmp_path / "external_rtl" / "foobar.sv"
    source.parent.mkdir(parents=True)
    source.write_text("module foobar; endmodule\n")
    analysis = tmp_path / "build" / "wrapper_smith" / "foobar.json"
    analysis.parent.mkdir(parents=True)
    analysis.write_text("{}\n")
    staged = tmp_path / "build" / "wrapper_smith" / "stage" / "hw" / "sci" / "foobar_sci.sv"
    staged.parent.mkdir(parents=True)
    staged.write_text("module foobar_sci; endmodule\n")
    runner = AgentRunner(FakeRegistry(repo_root=tmp_path), EventStream())
    runner.state.required_scope = "integration"
    runner.state.scope = "integration"
    runner.state.integration_requires_apply = False
    runner.state.requested_cores = {"foobar"}
    analysis_path, analysis_digest = runner.state.fingerprint(analysis)
    source_path, source_digest = runner.state.fingerprint(source)
    assert analysis_digest and source_digest
    runner.state.wrapper_analyses[analysis_path] = analysis_digest
    runner.state.wrapper_analysis_inputs[analysis_path] = {source_path: source_digest}

    runner.state.observe(
        "wrapper_scaffold",
        {"core": "foobar", "analysis": str(analysis)},
        SkillResult(
            True,
            "wrapper-smith",
            "staged",
            details={
                "stage": str(tmp_path / "build" / "wrapper_smith" / "stage"),
                "written": ["hw/sci/foobar_sci.sv"],
                "edited": [],
            },
        ),
    )
    assert runner.state.completion_error() is None
    source.write_text("module foobar; wire changed; endmodule\n")
    assert "target-bound" in runner.state.completion_error()


def test_wrapper_scaffold_vendor_tree_must_match_analyzed_root(tmp_path):
    analyzed = tmp_path / "rtl" / "analyzed"
    unrelated = tmp_path / "rtl" / "unrelated"
    analyzed.mkdir(parents=True)
    unrelated.mkdir(parents=True)
    source = analyzed / "foobar.sv"
    source.write_text("module foobar; endmodule\n")
    analysis = tmp_path / "build" / "wrapper_smith" / "foobar.json"
    analysis.parent.mkdir(parents=True)
    analysis.write_text("{}\n")
    runner = AgentRunner(FakeRegistry(repo_root=tmp_path), EventStream())
    runner.state.required_scope = "integration"
    runner.state.scope = "integration"
    analysis_path, analysis_digest = runner.state.fingerprint(analysis)
    source_path, source_digest = runner.state.fingerprint(source)
    assert analysis_digest and source_digest
    runner.state.wrapper_analyses[analysis_path] = analysis_digest
    runner.state.wrapper_analysis_inputs[analysis_path] = {source_path: source_digest}
    runner.state.wrapper_analysis_roots[analysis_path] = str(analyzed)

    result = runner._invoke(
        "wrapper_scaffold",
        {
            "core": "foobar",
            "analysis": str(analysis),
            "vendor_from": str(unrelated),
        },
        step=1,
    )

    assert not result.ok
    assert "does not match" in result.summary


def test_wrapper_apply_requires_vendor_core_closure(tmp_path):
    source = tmp_path / "rtl" / "foobar.sv"
    source.parent.mkdir(parents=True)
    source.write_text("module foobar; endmodule\n")
    analysis = tmp_path / "build" / "wrapper_smith" / "foobar.json"
    analysis.parent.mkdir(parents=True)
    analysis.write_text("{}\n")
    runner = AgentRunner(FakeRegistry(repo_root=tmp_path), EventStream())
    runner.state.required_scope = "integration"
    runner.state.scope = "integration"
    analysis_path, analysis_digest = runner.state.fingerprint(analysis)
    source_path, source_digest = runner.state.fingerprint(source)
    assert analysis_digest and source_digest
    runner.state.wrapper_analyses[analysis_path] = analysis_digest
    runner.state.wrapper_analysis_inputs[analysis_path] = {source_path: source_digest}

    result = runner._invoke(
        "wrapper_scaffold",
        {"core": "foobar", "analysis": str(analysis), "apply": True},
        step=1,
    )

    assert not result.ok
    assert "complete vendor RTL core" in result.summary


def test_testbench_completion_is_bound_to_core_and_full_source_closure(tmp_path):
    for core in ("serv", "other"):
        tb_dir = tmp_path / "tb" / "sci" / core
        tb_dir.mkdir(parents=True)
        (tb_dir / "run.sh").write_text("#!/bin/sh\n")
        (tb_dir / "deps.f").write_text("\n")
        (tb_dir / f"tb_{core}_sci.sv").write_text("module tb; endmodule\n")
        sci = tmp_path / "hw" / "sci"
        sci.mkdir(parents=True, exist_ok=True)
        (sci / f"{core}_sci.sv").write_text("module dut; endmodule\n")
    runner = AgentRunner(FakeRegistry(repo_root=tmp_path), EventStream())
    runner.state.required_scope = "testbench"
    runner.state.scope = "testbench"
    runner.state.requested_cores = {"serv"}
    passed = SkillResult(True, "tb-smith", "TB PASS")

    runner.state.observe("tb_run", {"core": "other"}, passed)
    assert "target-bound" in runner.state.completion_error()
    runner.state.observe("tb_run", {"core": "serv"}, passed)
    assert runner.state.completion_error() is None

    (tmp_path / "hw" / "sci" / "serv_sci.sv").write_text(
        "module changed; endmodule\n"
    )
    assert "target-bound" in runner.state.completion_error()


def test_physical_completion_is_invalidated_by_source_change(tmp_path):
    config = tmp_path / "configs" / "physical.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("soc: {name: physical}\n")
    rtl = tmp_path / "hw" / "top.sv"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module top; endmodule\n")
    runner = AgentRunner(FakeRegistry(repo_root=tmp_path), EventStream())
    runner.state.required_scope = "physical"
    runner.state.scope = "physical"
    runner.state.requested_configs = {str(config)}

    runner.state.observe(
        "flow_run",
        {"flow": "harden-chip", "config": str(config)},
        SkillResult(True, "flow-runner", "physical pass"),
    )
    assert runner.state.completion_error() is None
    rtl.write_text("module top; wire changed; endmodule\n")
    assert "target-bound" in runner.state.completion_error()


def test_topology_evidence_is_bound_to_config_content(tmp_path):
    config = tmp_path / "x.yaml"
    config.write_text("soc: {name: before}\n")
    registry = FakeRegistry(repo_root=tmp_path)
    runner = AgentRunner(registry, EventStream())
    runner.state.scope = "rtl"

    assert runner._invoke("topology_check", {"path": str(config)}, step=1).ok
    config.write_text("soc: {name: after}\n")
    result = runner._invoke(
        "flow_run",
        {"flow": "mosaic-gen-config", "config": str(config)},
        step=2,
    )
    assert not result.ok
    assert "topology_check" in result.summary
    assert registry.calls == [("topology_check", {"path": str(config)})]


def test_generation_evidence_is_bound_to_config_content(tmp_path):
    config = tmp_path / "x.yaml"
    config.write_text("soc: {name: before}\n")
    registry = FakeRegistry(repo_root=tmp_path)
    runner = AgentRunner(registry, EventStream())
    runner.state.scope = "simulation"

    assert runner._invoke("topology_check", {"path": str(config)}, step=1).ok
    assert runner._invoke(
        "flow_run",
        {"flow": "mosaic-gen-config", "config": str(config)},
        step=2,
    ).ok
    config.write_text("soc: {name: after}\n")
    result = runner._invoke(
        "flow_run",
        {"flow": "tb-soc-wake", "config": str(config)},
        step=3,
    )
    assert not result.ok
    assert "mosaic-gen-config" in result.summary
    assert [name for name, _ in registry.calls] == ["topology_check", "flow_run"]


def test_streaming_process_output_arrives_before_process_exits(tmp_path):
    seen = []
    started = time.monotonic()
    result = run_cmd(
        [
            sys.executable,
            "-c",
            "import time; print('READY', flush=True); time.sleep(.35); print('DONE', flush=True)",
        ],
        cwd=tmp_path,
        timeout=5,
        on_output=lambda line: seen.append((line, time.monotonic() - started)),
    )
    assert result.returncode == 0
    assert seen[0][0] == "READY"
    assert seen[0][1] < 0.25
    assert "READY\nDONE" in result.stdout


def test_streaming_timeout_raises_cleanly(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        run_cmd(
            [sys.executable, "-c", "import time; print('READY', flush=True); time.sleep(10)"],
            cwd=tmp_path,
            timeout=0.2,
            on_output=lambda _line: None,
        )


def test_captured_timeout_uses_same_process_group_runner(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        run_cmd(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=tmp_path,
            timeout=0.2,
        )


def test_streaming_timeout_terminates_descendant_process_group(tmp_path):
    pid_file = tmp_path / "child.pid"
    program = (
        "import pathlib,subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); "
        "print('CHILD',flush=True); time.sleep(30)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_cmd(
            [sys.executable, "-c", program],
            cwd=tmp_path,
            timeout=0.3,
            on_output=lambda _line: None,
        )
    child_pid = int(pid_file.read_text())
    for _ in range(20):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"descendant process {child_pid} survived timeout")


def test_terminal_pipe_has_no_ansi_and_shows_tool_lifecycle():
    output = StringIO()
    renderer = TerminalRenderer(output, color=False)
    events = EventStream(renderer)
    events.emit("session_start", "driver=api")
    events.emit("tool_start", "run", tool="topology_check", details={"arguments": {"path": "x"}})
    events.emit("tool_output", "\033[31mchecking banks\033[0m", tool="topology_check")
    events.emit("tool_end", "clean", tool="topology_check", status="ok")
    events.emit("session_end", "done", status="ok")
    text = output.getvalue()
    assert "\033[" not in text
    assert "topology_check" in text
    assert "checking banks" in text
    assert text.index("→") < text.index("checking banks") < text.index("✓")


def test_jsonl_events_are_monotonic_and_machine_clean():
    output = StringIO()
    events = EventStream(JsonlRenderer(output))
    events.emit("session_start", "start")
    events.emit("session_end", "done", status="ok")
    parsed = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [event["sequence"] for event in parsed] == [1, 2]
    assert parsed[-1]["kind"] == "session_end"
    assert "\033[" not in output.getvalue()


def test_event_stream_retains_bounded_tail_but_sequences_all_events():
    events = EventStream(max_in_memory=3)
    for index in range(7):
        events.emit("tool_output", str(index))
    assert events.event_count == 7
    assert [event.sequence for event in events.events] == [5, 6, 7]


def test_journal_is_private_for_new_and_existing_files(tmp_path):
    path = tmp_path / "sessions" / "run.jsonl"
    path.parent.mkdir()
    path.write_text("")
    path.chmod(0o644)
    journal = JsonlJournal(path)
    EventStream(journal).emit("session_start", "private")
    journal.close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["message"] == "private"


def test_sse_parser_reassembles_fragment_events():
    source = BytesIO(
        b"event: content_block_delta\n"
        b'data: {"delta":{"type":"text_delta","text":"hello"}}\n\n'
        b"data: [DONE]\n\n"
    )
    events = list(_iter_sse(source))
    assert events == [
        ("content_block_delta", {"delta": {"type": "text_delta", "text": "hello"}})
    ]


def test_openai_provider_streams_fragmented_tool_call(monkeypatch):
    import harness.llm as llm

    monkeypatch.setenv("FAKE_OPENAI_KEY", "secret")
    response = BytesIO(
        b'data: {"choices":[{"delta":{"content":"Checking "}}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"config_","arguments":"{\\\"path\\\":"}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"validate","arguments":"\\\"mosaic.yaml\\\"}"}}]},"finish_reason":"tool_calls"}]}\n\n'
        b"data: [DONE]\n\n"
    )
    monkeypatch.setattr(llm, "_request_stream", lambda *a, **k: response)
    provider = OpenAIToolProvider(
        {"kind": "openai", "env_key": "FAKE_OPENAI_KEY", "base_url": "http://fake"}
    )
    events = list(provider.stream("system", [{"role": "user", "content": "x"}], []))
    assert "".join(event.text for event in events) == "Checking "
    tool_events = [event for event in events if event.kind == "tool_delta"]
    assert "".join(event.tool_name_delta for event in tool_events) == "config_validate"
    assert json.loads("".join(event.arguments_delta for event in tool_events)) == {
        "path": "mosaic.yaml"
    }


def test_opencode_go_provider_normalizes_to_openai_transport(monkeypatch):
    import harness.llm as llm

    monkeypatch.setenv("OPENCODE_API_KEY", "secret")
    response = BytesIO(
        b'data: {"choices":[{"delta":{"content":"Ready"},"finish_reason":"stop"}]}\n\n'
        b"data: [DONE]\n\n"
    )
    request = {}

    def fake_request(url, headers, payload, timeout):
        request.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return response

    monkeypatch.setattr(llm, "_request_stream", fake_request)
    provider = llm.create_tool_provider({"kind": "opencode-go"})
    events = list(provider.stream("system", [{"role": "user", "content": "x"}], []))
    assert request["url"] == "https://opencode.ai/zen/go/v1/chat/completions"
    assert request["headers"] == {"Authorization": "Bearer secret"}
    assert request["payload"]["model"] == "kimi-k2.7-code"
    assert events[-1].stop_reason == "stop"


def test_opencode_go_normalizer_rejects_tui_model_prefix():
    import harness.llm as llm

    with pytest.raises(ValueError, match="raw model ID"):
        llm.normalize_api_config(
            {"kind": "opencode-go", "model": "opencode-go/kimi-k2.7-code"}
        )


def test_opencode_go_normalizer_locks_credential_to_official_endpoint():
    import harness.llm as llm

    with pytest.raises(ValueError, match="only sends its credential"):
        llm.normalize_api_config(
            {"kind": "opencode-go", "base_url": "https://example.invalid/v1"}
        )


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_generic_api_config_remains_unchanged(kind):
    import harness.llm as llm

    config = {
        "kind": kind,
        "model": "custom-model",
        "base_url": "https://gateway.example/v1",
        "env_key": "CUSTOM_API_KEY",
    }
    assert llm.normalize_api_config(config) == config


def test_anthropic_provider_streams_fragmented_tool_call(monkeypatch):
    import harness.llm as llm

    monkeypatch.setenv("FAKE_ANTHROPIC_KEY", "secret")
    response = BytesIO(
        b"event: content_block_start\n"
        b'data: {"index":0,"content_block":{"type":"tool_use","id":"c1","name":"topology_check"}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"index":0,"delta":{"type":"input_json_delta","partial_json":"{\\\"path\\\":"}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"index":0,"delta":{"type":"input_json_delta","partial_json":"\\\"mosaic.yaml\\\"}"}}\n\n'
        b"event: message_delta\n"
        b'data: {"delta":{"stop_reason":"tool_use"}}\n\n'
        b"event: message_stop\n"
        b"data: {}\n\n"
    )
    monkeypatch.setattr(llm, "_request_stream", lambda *a, **k: response)
    provider = AnthropicToolProvider(
        {"kind": "anthropic", "env_key": "FAKE_ANTHROPIC_KEY", "base_url": "http://fake"}
    )
    events = list(provider.stream("system", [{"role": "user", "content": "x"}], []))
    tool_events = [event for event in events if event.kind == "tool_delta"]
    assert "".join(event.tool_name_delta for event in tool_events) == "topology_check"
    assert json.loads("".join(event.arguments_delta for event in tool_events)) == {
        "path": "mosaic.yaml"
    }
    assert events[-1].kind == "message_end"


def test_agent_cli_dry_run_is_visible_and_json_contract_is_clean(tmp_path):
    command = [
        str(REPO_ROOT / "mosaic"),
        "agent",
        "two serv workers with tdu",
        "--driver",
        "deterministic",
        "--dry-run",
        "--no-color",
    ]
    visible = subprocess.run(command, cwd=tmp_path, text=True, capture_output=True, timeout=30)
    assert visible.returncode == 0, visible.stderr
    assert "mosaic agent" in visible.stdout
    assert "→ soc_plan" in visible.stdout
    assert "complete" in visible.stdout

    machine = subprocess.run(
        [str(REPO_ROOT / "mosaic"), "--json", *command[1:-1]],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert machine.returncode == 0, machine.stderr
    payload = json.loads(machine.stdout)
    assert payload["ok"] is True
    assert payload["skill"] == "agent"
    assert "\033[" not in machine.stdout


def test_external_omp_tty_uses_real_ui_not_print_or_fake_skill_command():
    prompt = "Use the tool cards. User request: build a SoC"
    interactive = _external_agent_command("omp", "/bin/omp", prompt, interactive=True)
    # --no-tools drops omp's built-in bash/edit/write; the gated MCP server
    # from .omp/mcp.json is what the model keeps.
    assert interactive == ["/bin/omp", "--no-tools", prompt]
    assert "--print" not in interactive and "-p" not in interactive
    assert not any("/skill:" in token for token in interactive)

    headless = _external_agent_command("omp", "/bin/omp", prompt, interactive=False)
    assert headless[:4] == ["/bin/omp", "--no-tools", "--mode", "json"]


def test_agent_cli_jsonl_has_paired_final_event(tmp_path):
    result = subprocess.run(
        [
            str(REPO_ROOT / "mosaic"),
            "agent",
            "one serv worker with tdu",
            "--driver",
            "deterministic",
            "--dry-run",
            "--events-jsonl",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[0]["kind"] == "session_start"
    assert events[-1]["kind"] == "session_end"
    assert events[-1]["status"] == "ok"
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))


def test_agent_cli_rejects_json_and_jsonl_on_same_stdout():
    result = subprocess.run(
        [
            str(REPO_ROOT / "mosaic"),
            "--json",
            "agent",
            "inspect only",
            "--driver",
            "deterministic",
            "--dry-run",
            "--events-jsonl",
        ],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "mutually exclusive" in result.stderr


def test_cli_disables_apply_option_abbreviation():
    result = subprocess.run(
        [
            str(REPO_ROOT / "mosaic"),
            "wrapper-smith",
            "scaffold",
            "safe_core",
            "--from",
            "missing.json",
            "--app",
        ],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "unrecognized arguments: --app" in result.stderr


def test_omp_origin_is_denied_apply_and_physical_flow_in_python_policy():
    env = {**os.environ, "MOSAIC_AGENT_TOOL": "1"}
    apply = subprocess.run(
        [
            str(REPO_ROOT / "mosaic"),
            "wrapper-smith",
            "scaffold",
            "safe_core",
            "--from",
            "missing.json",
            "--apply",
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert apply.returncode != 0
    assert "explicit user-run command" in apply.stdout
    physical = subprocess.run(
        [str(REPO_ROOT / "mosaic"), "flow-runner", "run", "harden-chip"],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert physical.returncode != 0
    assert "explicit user-run command" in physical.stdout


@pytest.mark.parametrize(
    "driver,flag",
    [
        ("omp", "--dry-run"),
        ("omp", "--events-jsonl"),
        ("claude", "--allow-physical"),
        ("claude", "--headless"),
    ],
)
def test_external_driver_policy_flags_fail_closed(driver, flag):
    result = subprocess.run(
        [
            str(REPO_ROOT / "mosaic"),
            "agent",
            "inspect",
            "--driver",
            driver,
            flag,
        ],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "does not implement harness policy flags" in result.stdout


def test_high_impact_agent_tools_require_explicit_approval():
    registry = AgentToolRegistry(allow_physical=False, allow_integration=False)
    physical = registry.execute("flow_run", {"flow": "harden-chip"})
    assert not physical.ok
    assert "explicit approval" in physical.summary

    integration = registry.execute(
        "wrapper_scaffold",
        {"core": "new_core", "analysis": "analysis.json", "apply": True},
    )
    assert not integration.ok
    assert "integration approval" in integration.summary


def test_agent_tool_paths_cannot_escape_repository(tmp_path):
    registry = AgentToolRegistry(repo_root=REPO_ROOT)
    with pytest.raises(ValueError, match="must stay inside"):
        registry.execute("config_validate", {"path": str(tmp_path / "secret.yaml")})


def test_skill_without_required_subcommand_fails_clearly():
    result = subprocess.run(
        [str(REPO_ROOT / "mosaic"), "flow-runner"],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "required" in result.stderr


def test_the_ungated_omp_custom_tool_is_retired():
    """.omp/tools/mosaic.ts shelled to the CLI with no AgentState in front.

    omp reaches the gated MCP server through .omp/mcp.json instead; a custom
    tool left beside it would be a second, ungated path to the same flows.
    """
    assert not (REPO_ROOT / ".omp" / "tools" / "mosaic.ts").exists()
    assert (REPO_ROOT / ".omp" / "mcp.json").is_file()
