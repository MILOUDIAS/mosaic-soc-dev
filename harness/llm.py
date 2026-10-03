"""Provider adapters for the compatibility translator and agent runtime.

``translate_intent`` remains the legacy, one-request translation path used
only by explicit ``soc-from-prompt --llm`` calls. The streamed provider classes
below it power the real bounded model/tool/observation loop in ``agent.py``;
they can propose typed tool calls but cannot execute commands or bypass gates.
Deterministic parsing remains the default and fallback for the compatibility
path, while ``mosaic agent --driver api`` uses the streamed adapters.

Two wire formats cover essentially every provider:
  anthropic          — api.anthropic.com/v1/messages
  openai-compatible  — <base_url>/chat/completions (OpenAI, Groq, Ollama, ...)

``opencode-go`` is a self-describing preset for the OpenAI-compatible subset
of OpenCode Go.  It is normalized to the existing OpenAI transport before a
request is made; generic ``openai`` and ``anthropic`` configurations retain
their existing behavior.

API keys are NEVER stored: the config records only the ENV VAR NAME.
"""

import json
import os
import re
from dataclasses import dataclass
import urllib.request
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5",   # fast + cheap; translation is easy work
    "openai": "gpt-4o-mini",
}

OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_GO_DEFAULT_MODEL = "kimi-k2.7-code"
OPENCODE_GO_ENV_KEY = "OPENCODE_API_KEY"

# OpenCode documented these model IDs on its OpenAI-compatible Chat Completions
# endpoint as checked on 2026-07-15.  Keep this transport-specific list explicit:
# its MiniMax/Qwen Go models use the Anthropic Messages endpoint instead and
# must be configured through the generic anthropic kind.
OPENCODE_GO_OPENAI_MODELS = frozenset({
    "glm-5.2",
    "glm-5.1",
    "kimi-k2.7-code",
    "kimi-k2.6",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "mimo-v2.5",
    "mimo-v2.5-pro",
    # Listed by `opencode models` under the opencode-go provider, which is what
    # this preset talks to. Added 2026-09-17 to measure a genuinely small,
    # free-tier model against the prompt contract (harness/prompt_eval.py)
    # rather than a large one standing in for it.
    "muse-spark-1.3-contributor",
    "muse-spark-1.2-contributor",
})


def normalize_api_config(api_cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a transport-ready API configuration.

    ``kind=opencode-go`` remains useful provenance in the persisted user
    config, while this boundary maps it onto the already-tested OpenAI wire
    format.  The preset is deliberately tied to OpenCode's official endpoint
    and documented Chat Completions model family; custom gateways continue to
    use the generic ``openai``/``anthropic`` kinds.
    """

    cfg = dict(api_cfg)
    if cfg.get("kind") != "opencode-go":
        return cfg

    model = cfg.get("model") or OPENCODE_GO_DEFAULT_MODEL
    if model.startswith("opencode-go/"):
        raise ValueError(
            "OpenCode Go API requests use the raw model ID "
            f"({model.removeprefix('opencode-go/')!r}), not the OpenCode TUI prefix"
        )
    if model not in OPENCODE_GO_OPENAI_MODELS:
        raise ValueError(
            f"OpenCode Go model {model!r} is not in the documented "
            "OpenAI-compatible family; configure Messages models with "
            "--api-kind anthropic --base-url https://opencode.ai/zen/go"
        )

    base_url = (cfg.get("base_url") or OPENCODE_GO_BASE_URL).rstrip("/")
    if base_url != OPENCODE_GO_BASE_URL:
        raise ValueError(
            "the opencode-go preset only sends its credential to "
            f"{OPENCODE_GO_BASE_URL}; use --api-kind openai for a custom endpoint"
        )

    cfg.update({
        "kind": "openai",
        "model": model,
        "base_url": OPENCODE_GO_BASE_URL,
        "env_key": cfg.get("env_key") or OPENCODE_GO_ENV_KEY,
    })
    return cfg


_SYSTEM = """You translate a natural-language SoC request into JSON for the \
MOSAIC-SoC generator. Respond with ONE JSON object and nothing else:
{"cores": [{"ip": str, "count": int, "role"?: "titan"|"atlas"|"nano",
            "isa"?: str}],
 "sram_kb": int|null, "boot_rom_kb": int|null, "scratchpad_bytes": int|null,
 "no_sram": bool|null,
 "bus": "obi"|"log"|"floonoc"|null, "target_clock_mhz": number|null,
 "tdu": bool|null, "sched_mode": "static"|"dynamic"|"power-aware"|null,
 "peripherals": [str], "name": str|null}
Registered cores (use ONLY these ips): {cores}
Registered peripherals: {periphs}
Common spellings of those ips, which are NOT inventions: {aliases}
Rules: titan = orchestrator (free-running); atlas/nano = workers (dormant
until TDU wake). If the user names no orchestrator, do NOT invent one — the
harness adds it deterministically and reports the repair. Omit "role" for any
core whose part the request does not state: the harness assigns roles from the
topology, and calling a core "atlas" or "nano" that the user never called a
worker makes it add an orchestrator nobody asked for. Do not invent
boot addresses. If a requested core is not registered, leave it OUT of
"cores" and add the token to an "unrecognized" list in the JSON.
Do not invent cores either: "a big core" or "four little ones" names no
registered ip, so those words belong in "unrecognized" and not in a core you
chose for the user. Put a stated memory size, boot ROM, scratchpad, ISA or
clock target in ITS OWN field -- folding one into another, a scratchpad into
"sram_kb" say, changes the design into something nobody asked for.
Set "no_sram": true only when the request says the part has no on-chip RAM and
runs from external memory (XIP). A bare "sram_kb": 0 without it is refused,
because a zero nobody asked for is a typo and not a design.
Use null for "tdu" unless the request actually mentions it. A worker role
beside "tdu": false is a design that cannot exist -- nothing would ever wake
those harts -- and the harness refuses it, so guessing false costs the
request. Null lets the harness decide from the topology."""


def _extract_json(text: str) -> Dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON object in LLM reply: {text[:200]!r}")
    return json.loads(m.group(0))


#: Sent on every request. A gateway behind Cloudflare can reject a request that
#: carries no User-Agent before it reaches any model: opencode.ai answers
#: `403 error code: 1010`, which reads exactly like an expired credential and
#: is not one. Measured 2026-09-17 -- every opencode-go call from this harness
#: was being refused that way, and the same request with a User-Agent got a
#: real answer from the gateway. Callers may still override it.
_USER_AGENT = "mosaic-harness/1.0"


def _post(url: str, headers: Dict[str, str], payload: Dict[str, Any],
          timeout: int = 60) -> Dict[str, Any]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": _USER_AGENT, **headers})
    # Imported as a name, not as `urllib.error`: binding `urllib` locally would
    # shadow the module-level import and make the Request above unbound.
    from urllib.error import HTTPError
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        # The body is the actionable half of a refusal: "requires explicit opt
        # in: <url>", "Insufficient balance", "only available hosted in China".
        # Without it every provider refusal reads as a bare 403 and looks like
        # a dead credential. The body is the gateway's own message; the
        # credential is in the request, never in the response.
        detail = exc.read()[:400].decode("utf-8", "replace").strip()
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from None


def alias_hint(aliases: Mapping[str, str]) -> str:
    """"pico/picorv = picorv32; cve2/e20 = cv32e20" — the spellings the
    grammar already accepts, grouped by the ip each one means.

    Rendered from the alias table rather than written out by hand, so the
    contract cannot drift from what the grammar takes. A model told only the
    canonical names refuses the rest: "two pico cores" came back as
    unrecognized (measured by harness/prompt_eval.py).
    """
    by_ip: Dict[str, List[str]] = {}
    for alias, ip in sorted(aliases.items()):
        by_ip.setdefault(ip, []).append(alias)
    return "; ".join(f"{'/'.join(spellings)} = {ip}"
                     for ip, spellings in sorted(by_ip.items())) or "none"


def translate_intent(prompt: str, api_cfg: Dict[str, Any],
                     valid_cores, valid_periphs,
                     aliases: Optional[Mapping[str, str]] = None
                     ) -> Dict[str, Any]:
    """prompt -> structured intent dict via the configured provider.

    Raises on any failure — callers fall back to the deterministic grammar.
    """
    api_cfg = normalize_api_config(api_cfg)
    env_key = api_cfg.get("env_key") or (
        "ANTHROPIC_API_KEY" if api_cfg["kind"] == "anthropic" else "OPENAI_API_KEY")
    key = os.environ.get(env_key, "")
    if not key:
        raise RuntimeError(f"API key env var {env_key} is not set")

    system = _SYSTEM.replace("{cores}", ", ".join(sorted(valid_cores))) \
                    .replace("{periphs}", ", ".join(sorted(valid_periphs))) \
                    .replace("{aliases}", alias_hint(aliases or {}))
    model = api_cfg.get("model") or DEFAULT_MODELS.get(api_cfg["kind"], "")

    if api_cfg["kind"] == "anthropic":
        base = api_cfg.get("base_url") or "https://api.anthropic.com"
        data = _post(
            f"{base}/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            {"model": model, "max_tokens": 1024, "system": system,
             "messages": [{"role": "user", "content": prompt}]})
        text = "".join(b.get("text", "") for b in data.get("content", []))
    else:  # openai-compatible
        base = api_cfg.get("base_url") or "https://api.openai.com/v1"
        data = _post(
            f"{base.rstrip('/')}/chat/completions",
            {"Authorization": f"Bearer {key}"},
            {"model": model,
             "messages": [{"role": "system", "content": system},
                          {"role": "user", "content": prompt}]})
        text = data["choices"][0]["message"]["content"]

    return _extract_json(text)


# ── Agent-loop provider stream ──────────────────────────────────────


@dataclass(frozen=True)
class ProviderEvent:
    """Provider-neutral streamed text/tool-call fragment."""

    kind: str  # text_delta | tool_delta | message_end
    text: str = ""
    tool_index: int = 0
    tool_id: str = ""
    tool_name_delta: str = ""
    arguments_delta: str = ""
    stop_reason: str = ""


class ToolCallingProvider:
    """Small provider interface consumed by :class:`AgentRunner`."""

    def stream(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> Iterable[ProviderEvent]:
        raise NotImplementedError


def _iter_sse(response) -> Iterator[tuple[str, Dict[str, Any]]]:
    """Yield ``(event_name, JSON data)`` from a text/event-stream response."""

    event_name = "message"
    data_lines: List[str] = []
    for raw in response:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data_lines:
                payload = "\n".join(data_lines)
                if payload != "[DONE]":
                    yield event_name, json.loads(payload)
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        payload = "\n".join(data_lines)
        if payload != "[DONE]":
            yield event_name, json.loads(payload)


def _request_stream(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout: int,
):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": _USER_AGENT, **dict(headers)},
    )
    return urllib.request.urlopen(request, timeout=timeout)


def _openai_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    wire: List[Dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "assistant":
            item: Dict[str, Any] = {
                "role": "assistant",
                "content": message.get("content") or None,
            }
            calls = message.get("tool_calls", [])
            if calls:
                item["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["arguments"]),
                        },
                    }
                    for call in calls
                ]
            wire.append(item)
        elif role == "tool":
            wire.append(
                {
                    "role": "tool",
                    "tool_call_id": message["tool_call_id"],
                    "content": message["content"],
                }
            )
        else:
            wire.append({"role": role, "content": message.get("content", "")})
    return wire


def _anthropic_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    wire: List[Dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "assistant":
            blocks: List[Dict[str, Any]] = []
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            blocks.extend(
                {
                    "type": "tool_use",
                    "id": call["id"],
                    "name": call["name"],
                    "input": call["arguments"],
                }
                for call in message.get("tool_calls", [])
            )
            target_role = "assistant"
        elif role == "tool":
            blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": message["content"],
                }
            ]
            target_role = "user"
        else:
            blocks = [{"type": "text", "text": message.get("content", "")}]
            target_role = "user"
        if wire and wire[-1]["role"] == target_role:
            wire[-1]["content"].extend(blocks)
        else:
            wire.append({"role": target_role, "content": blocks})
    return wire


class OpenAIToolProvider(ToolCallingProvider):
    def __init__(self, api_cfg: Mapping[str, Any], timeout: int = 120):
        self.api_cfg = dict(api_cfg)
        self.timeout = timeout

    def stream(self, system, messages, tools):
        env_key = self.api_cfg.get("env_key") or "OPENAI_API_KEY"
        key = os.environ.get(env_key, "")
        if not key:
            raise RuntimeError(f"API key env var {env_key} is not set")
        base = self.api_cfg.get("base_url") or "https://api.openai.com/v1"
        model = self.api_cfg.get("model") or DEFAULT_MODELS["openai"]
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
            for tool in tools
        ]
        payload = {
            "model": model,
            "stream": True,
            "messages": [
                {"role": "system", "content": system},
                *_openai_messages(messages),
            ],
            "tools": openai_tools,
            "tool_choice": "auto",
        }
        with _request_stream(
            f"{base.rstrip('/')}/chat/completions",
            {"Authorization": f"Bearer {key}"},
            payload,
            self.timeout,
        ) as response:
            stop_reason = ""
            for _event_name, data in _iter_sse(response):
                choices = data.get("choices", [])
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta", {})
                if delta.get("content"):
                    yield ProviderEvent("text_delta", text=delta["content"])
                for call in delta.get("tool_calls", []):
                    function = call.get("function", {})
                    yield ProviderEvent(
                        "tool_delta",
                        tool_index=int(call.get("index", 0)),
                        tool_id=call.get("id", ""),
                        tool_name_delta=function.get("name", ""),
                        arguments_delta=function.get("arguments", ""),
                    )
                if choice.get("finish_reason"):
                    stop_reason = choice["finish_reason"]
            yield ProviderEvent("message_end", stop_reason=stop_reason)


class AnthropicToolProvider(ToolCallingProvider):
    def __init__(self, api_cfg: Mapping[str, Any], timeout: int = 120):
        self.api_cfg = dict(api_cfg)
        self.timeout = timeout

    def stream(self, system, messages, tools):
        env_key = self.api_cfg.get("env_key") or "ANTHROPIC_API_KEY"
        key = os.environ.get(env_key, "")
        if not key:
            raise RuntimeError(f"API key env var {env_key} is not set")
        base = self.api_cfg.get("base_url") or "https://api.anthropic.com"
        model = self.api_cfg.get("model") or DEFAULT_MODELS["anthropic"]
        payload = {
            "model": model,
            "max_tokens": 4096,
            "stream": True,
            "system": system,
            "messages": _anthropic_messages(messages),
            "tools": tools,
        }
        with _request_stream(
            f"{base.rstrip('/')}/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            payload,
            self.timeout,
        ) as response:
            stop_reason = ""
            for event_name, data in _iter_sse(response):
                if event_name == "content_block_start":
                    block = data.get("content_block", {})
                    if block.get("type") == "tool_use":
                        yield ProviderEvent(
                            "tool_delta",
                            tool_index=int(data.get("index", 0)),
                            tool_id=block.get("id", ""),
                            tool_name_delta=block.get("name", ""),
                            arguments_delta=(
                                json.dumps(block["input"])
                                if block.get("input")
                                else ""
                            ),
                        )
                elif event_name == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield ProviderEvent("text_delta", text=delta.get("text", ""))
                    elif delta.get("type") == "input_json_delta":
                        yield ProviderEvent(
                            "tool_delta",
                            tool_index=int(data.get("index", 0)),
                            arguments_delta=delta.get("partial_json", ""),
                        )
                elif event_name == "message_delta":
                    stop_reason = data.get("delta", {}).get("stop_reason", stop_reason)
                elif event_name == "message_stop":
                    yield ProviderEvent("message_end", stop_reason=stop_reason)


def create_tool_provider(api_cfg: Mapping[str, Any]) -> ToolCallingProvider:
    api_cfg = normalize_api_config(api_cfg)
    kind = api_cfg.get("kind")
    if kind == "anthropic":
        return AnthropicToolProvider(api_cfg)
    if kind == "openai":
        return OpenAIToolProvider(api_cfg)
    raise ValueError(f"unsupported API provider kind {kind!r}")
