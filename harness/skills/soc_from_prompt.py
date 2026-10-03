"""soc-from-prompt skill — deterministic natural-language → SoC pipeline.

The no-LLM fallback path of the mosaic prompt→SoC story (and the CI-able
demo): a small, ordered regex grammar extracts core groups, memory, bus,
scheduler and peripherals from a prompt; every match is recorded with
provenance (`matched`) and every leftover content token is surfaced
(`unrecognized`) — nothing is silently guessed. Repairs (e.g. auto-adding a
TITAN) are applied deterministically and REPORTED in the summary.

An LLM agent uses the same slots through the .claude/skills/soc-from-prompt
card, but calls `plan` first to see how the deterministic parse reads the
request, then overrides via config-author when its own reading differs.

Pipeline (run --run): config-author generate → topo-viz check (GATE) →
flow-runner mosaic-gen-config (GATE) → flow-runner tb-soc-generic (GATE:
every configured hart executes before EXIT SUCCESS) → doc-gen config summary.
"""

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core import (
    SkillResult, REPO_ROOT, VALID_CORE_IPS, SIM_ONLY_CORES,
    VALID_PERIPHERALS, log,
)

# ── vocabulary ───────────────────────────────────────────────────────

# Aliases people actually write → canonical ip (canonical names also match).
CORE_ALIASES: Dict[str, str] = {
    "pico": "picorv32",
    "picorv": "picorv32",
    "cve2": "cv32e20",
    "e20": "cv32e20",
    "e40x": "cv32e40x",
    "small boom": "boom",
    "rocket-chip": "rocket",
}

ROLE_WORDS = {
    "titan": "titan", "orchestrator": "titan", "controller": "titan",
    "main": "titan", "big": "titan", "boss": "titan",
    "atlas": "atlas", "worker": "atlas", "workers": "atlas", "mid": "atlas",
    "nano": "nano", "tiny": "nano", "little": "nano", "small": "nano",
    "sensor": "nano",
}

PERIPH_SYNONYMS = {
    "serial": "uart", "console": "uart",
    "pins": "gpio", "leds": "gpio",
    # "general purpose io" and "gpio pins" are how people write it; `io` alone
    # is safe because the word boundary does not match inside "gpio".
    "io": "gpio",
    "clock": "timer", "watchdog": "timer",
    "flash": "spi",
}

# Words consumed by non-core rules so they don't show up as unrecognized.
_STOPWORDS = {
    "a", "an", "and", "the", "with", "of", "for", "to", "in", "on", "soc",
    "system", "chip", "make", "build", "generate", "me", "please", "one",
    "two", "three", "four", "core", "cores", "cpu", "cpus", "using", "plus",
    "that", "it", "i", "want", "need", "at", "as", "by", "from", "into",
    "verify", "verified", "verification", "scheduling", "existing", "run",
    "test", "testbench", "without", "no", "do", "not", "regenerate",
    # Framing words for a specific part: describing the target does not change
    # the design, so they must not read as unrecognized intent.
    "chipathon", "block", "slot", "part", "macro", "candidate",
    "only", "just", "its", "own",
    "update", "write", "author", "create", "execute", "simulation", "rtl",
    "only", "but", "just", "include", "includes", "including", "also",
    "full", "full-soc", "full_soc", "fullsoc", "heterogeneous", "configure",
    "configured", "configuring",
    "configuration",
    # Ordinary RISC-V vocabulary that the prompt evaluation
    # (harness/prompt_eval.py) caught being surfaced as unrecognized, which
    # refuses the whole request. "four serv harts", "workers woken by the
    # TDU", "on the floonoc NoC": the count, the core, the TDU and the bus are
    # each parsed by their own rule, so these words carry no further intent.
    "hart", "harts", "woken", "wakes", "executing", "noc",
    # Quantity words that only ever mean "one of these", which is already the
    # count the core rule defaults to.
    "single", "sole", "lone",
    # Framing words around a peripheral name -- "a serial port", "general
    # purpose io" -- where the peripheral itself is matched by its synonym.
    "port", "ports", "general", "purpose",
    # Where the code runs from when there is no on-chip RAM pool: "xip from
    # external flash". `flash` already means the SPI peripheral and "no sram"
    # already sets the deliberate-zero flag, so these two add nothing.
    "external", "xip",
}
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8}

#: The 0x3000 window is the TDU sentinel area and must stay clear of every
#: worker image.
_SENTINEL_WINDOW = 0x3000
#: Where worker slots stop when the request states no memory size.
_DEFAULT_SLOT_CEILING = 0x8000


def worker_boot_slots(sram_kb: Optional[int] = None) -> List[int]:
    """Boot slots for worker harts: 4 KB apart from 0x1000, up to the memory
    the design actually states, skipping the sentinel window.

    Generated rather than hand-listed. The old list was exactly this rule with
    a fixed 0x8000 ceiling, which silently capped any design at six workers:
    "eight serv workers and one cv32e20" left two harts with nowhere to boot
    and the flow generated them regardless (found by harness/prompt_eval.py).
    """
    top = int(sram_kb) * 1024 if sram_kb else _DEFAULT_SLOT_CEILING
    return [a for a in range(0x1000, top, 0x1000) if a != _SENTINEL_WINDOW]


@dataclass
class ParsedIntent:
    name: str = "mosaic_prompted"
    core_groups: List[Dict[str, Any]] = field(default_factory=list)
    sram_kb: Optional[int] = None
    boot_rom_kb: Optional[int] = None
    bus: Optional[str] = None
    tdu: bool = False
    tdu_explicit: bool = False
    sched_mode: Optional[str] = None
    peripherals: List[str] = field(default_factory=list)
    peripherals_explicit: bool = False
    scratchpad_bytes: Optional[int] = None
    target_clock_mhz: Optional[float] = None
    repair_margin_pct: Optional[int] = None
    target: Optional[str] = None
    # True only when the prompt SAID "no sram". A bare "0 KB sram" is more
    # likely a slip than a design decision, and is still refused.
    sram_zero_by_words: bool = False
    # Selectable platform blocks. Only keys the prompt actually mentioned are
    # populated, so silence leaves the generator's defaults alone rather than
    # asserting a wall of `true`s the user never asked for.
    platform: Dict[str, Any] = field(default_factory=dict)
    preset: Optional[str] = None
    matched: List[str] = field(default_factory=list)       # provenance
    unrecognized: List[str] = field(default_factory=list)  # surfaced, never dropped
    repairs: List[str] = field(default_factory=list)       # deterministic fixes applied


def _core_pattern() -> re.Pattern:
    """Alternation over canonical ips + aliases, longest-first."""
    names = sorted(set(VALID_CORE_IPS) | set(CORE_ALIASES), key=len, reverse=True)
    alt = "|".join(re.escape(n) for n in names)
    return re.compile(rf"\b(\d+|{'|'.join(_NUMBER_WORDS)})?\s*[x×]?\s*({alt})s?\b"
                      rf"(?:\s*[x×]\s*(\d+))?", re.I)


def parse_prompt(text: str) -> ParsedIntent:
    intent = ParsedIntent()
    low = " " + text.lower() + " "
    consumed_spans: List[tuple] = []

    def consume(m: re.Match, note: str):
        consumed_spans.append(m.span())
        intent.matched.append(note)

    # ── cores (with optional count on either side) ──
    core_matches = list(_core_pattern().finditer(low))
    for i, m in enumerate(core_matches):
        raw_count_pre, raw_ip, raw_count_post = m.group(1), m.group(2), m.group(3)
        ip = CORE_ALIASES.get(raw_ip, raw_ip)
        if ip not in VALID_CORE_IPS:
            continue
        count = 1
        if raw_count_pre:
            count = (int(raw_count_pre) if raw_count_pre.isdigit()
                     else _NUMBER_WORDS[raw_count_pre])
        elif raw_count_post:
            count = int(raw_count_post)
        # role: the role word AFTER the core name, bounded by the next core hit
        # ("two picorv32 workers"); fall back to the segment before, bounded by
        # the previous core hit ("worker picorv32"). Bounding by neighboring
        # hits prevents e.g. "cv32e20 controller, two picorv32" from leaking
        # 'controller' into the picorv32 group.
        next_core_start = (
            core_matches[i + 1].start()
            if i + 1 < len(core_matches)
            else len(low)
        )
        after_end = min(next_core_start, m.end() + 40)
        before_start = (core_matches[i - 1].end()
                        if i > 0 else max(0, m.start() - 40))
        # A role word BEFORE the core name belongs to it only when nothing
        # separates them: "worker picorv32" does, "eight serv workers and one
        # cv32e20" does not -- there the word belongs to the serv group, and
        # taking it made the cv32e20 a worker, so the repair added a SECOND
        # cv32e20 as orchestrator: ten harts for a nine-hart request. Found by
        # harness/prompt_eval.py, when the model and the grammar disagreed.
        before = re.split(r",|;|\band\b|\bplus\b|\bwith\b",
                          low[before_start:m.start()])[-1]
        role = None
        for segment in (low[m.end():after_end], before):
            for w, r in ROLE_WORDS.items():
                if re.search(rf"\b{w}\b", segment):
                    role = r
                    break
            if role:
                break
        group = {"ip": ip, "count": count}
        if role:
            group["role"] = role
        # Explicit per-core parameters may be verbose and can occur anywhere
        # before the next named core. Do not apply the role word's short
        # ambiguity bound to these keyed declarations.
        local_segment = low[m.end():next_core_start]
        chunksize = re.search(
            r"\bchunksize\s*(?:(?:of)\s+|[:=]\s*)?(\d+)\b",
            local_segment,
        )
        if chunksize and ip == "fazyrv":
            group["chunksize"] = int(chunksize.group(1))
            consumed_spans.append(
                (m.end() + chunksize.start(), m.end() + chunksize.end())
            )
            intent.matched.append(f"{ip} chunksize {chunksize.group(1)}")
        isa = re.search(r"\b(rv(?:32|64)[a-z0-9_]*)\b", local_segment)
        if isa:
            group["isa"] = isa.group(1)
            consumed_spans.append((m.end() + isa.start(), m.end() + isa.end()))
            intent.matched.append(f"{ip} ISA {isa.group(1)}")
        boot = re.search(
            r"\bboot(?:s|ing)?(?:[\s_-]*(?:address|addr))?(?:\s+at)?"
            r"\s*[:=]?\s*(0x[0-9a-f]+|\d+)\b",
            local_segment,
        )
        if boot:
            group["boot_addr"] = int(boot.group(1), 0)
            consumed_spans.append((m.end() + boot.start(), m.end() + boot.end()))
            intent.matched.append(f"{ip} boot address {boot.group(1)}")
        # SERV/QERV expose a CSR file and a compressed decoder as parameters,
        # and the Block A part uses both: CSRs on the TITAN (the boot ROM needs
        # them), none on the worker. Without these the frozen config is simply
        # not expressible from a prompt.
        csr = re.search(r"\b(no|without|with)\s+csrs?\b", local_segment)
        if csr:
            group["with_csr"] = 0 if csr.group(1) in ("no", "without") else 1
            consumed_spans.append(
                (m.end() + csr.start(), m.end() + csr.end())
            )
            intent.matched.append(f"{ip} with_csr {group['with_csr']}")
        comp = re.search(r"\b(compressed|c\s+extension)\b", local_segment)
        if comp:
            group["compressed"] = 1
            consumed_spans.append(
                (m.end() + comp.start(), m.end() + comp.end())
            )
            intent.matched.append(f"{ip} compressed")
        # The schema rejects an ISA and a compressed flag that disagree, so an
        # ISA ending in 'c' implies the decoder. Repaired visibly, not silently.
        isa_str = group.get("isa", "")
        if isa_str.startswith("rv32") and "c" in isa_str[4:] and "compressed" not in group:
            group["compressed"] = 1
            intent.repairs.append(f"{ip} ISA {isa_str} implies compressed=1")

        intent.core_groups.append(group)
        consume(m, f"{count}x {ip}" + (f" ({role})" if role else ""))

    # ── memory ──
    # "no sram" is a real design choice here, not an omission: the Block A part
    # has no on-chip RAM pool at all and executes XIP from flash.
    m = re.search(r"\b(?:no|without|zero)\s+(?:on[\s-]*chip\s+)?(?:sram|ram)\b", low)
    if m:
        intent.sram_kb = 0
        intent.sram_zero_by_words = True
        consume(m, "sram 0 KB (no on-chip RAM pool)")
    m = re.search(r"(\d+)\s*([km])i?b?\s*(?:of\s+)?(?:sram|ram|memory)", low)
    if m:
        kb = int(m.group(1)) * (1024 if m.group(2) == "m" else 1)
        intent.sram_kb = kb
        consume(m, f"sram {kb} KB")
    m = re.search(r"(\d+)\s*([km])i?b?\s*(?:of\s+)?boot\s*rom\b", low)
    if m:
        kb = int(m.group(1)) * (1024 if m.group(2) == "m" else 1)
        intent.boot_rom_kb = kb
        consume(m, f"boot ROM {kb} KB")

    # ── implementation target ──
    # Asking for "tapeout" is a CLAIM that the design matches the qualified
    # physical matrix (core_registry.TAPEOUT_*). The prompt may make the claim;
    # validation decides whether it holds. That split is the point: a prompt
    # cannot talk its way past the capability gate.
    m = re.search(r"\bfor\s+tape\s*-?out\b|\btape\s*-?out\b", low)
    if m:
        intent.target = "tapeout"
        consume(m, "target tapeout (claim -- validated against the qualified matrix)")

    # ── scratchpad ──
    # A part with sram_kb: 0 still needs somewhere for the shared-control
    # window, so the size is worth saying out loud.
    m = re.search(r"(\d+)\s*(?:b|byte|bytes)\s+scratchpad", low)
    if m:
        intent.scratchpad_bytes = int(m.group(1))
        consume(m, f"scratchpad {intent.scratchpad_bytes} B")

    # ── target clock ──
    # DESIGN INTENT, not a result. Without this the grammar could describe the
    # whole SoC but not the frequency it is meant to run at, so a clock could
    # only be set by hand-editing a hardening config. Matched before any other
    # numeric rule would see it; "mhz" is unambiguous.
    #
    # "clock" on its own means the TIMER PERIPHERAL in this grammar (see the
    # alias table), so this keys on the UNIT rather than the word -- and then
    # swallows an adjacent "clock" into the consumed span. Otherwise "with a
    # 25 MHz clock" sets the frequency AND adds a timer, which on a tapeout
    # config is rejected outright for not being uart-only. Measured before the
    # fix: that exact prompt failed with "requires peripherals uart".
    #
    # The masking pass below is what makes swallowing work -- it blanks claimed
    # spans before the peripheral scan, for exactly this class of collision.
    m = re.search(
        r"(?:\bclock(?:ed)?\s+(?:at\s+)?)?(\d+(?:\.\d+)?)\s*mhz(?:\s+clock)?", low)
    if m:
        intent.target_clock_mhz = float(m.group(1))
        consume(m, f"target clock {intent.target_clock_mhz:g} MHz")

    # ── repair margin ──
    # Same argument as the clock above: without a rule here the margin could
    # only be set by hand-editing a hardening config, and stopping exactly
    # that is why soc.objectives.repair_margin_pct exists. Keyed on the words
    # "repair margin", which nothing else in this grammar claims -- a bare
    # percentage would collide with utilisation and with SRAM sizes.
    m = re.search(
        r"(?:\brepair\s+margin\s+(?:of\s+)?(\d{1,2})\s*%?"
        r"|\b(\d{1,2})\s*%\s+repair\s+margin)", low)
    if m:
        intent.repair_margin_pct = int(m.group(1) or m.group(2))
        consume(m, f"repair margin {intent.repair_margin_pct}%")

    # ── selectable platform blocks ──
    # Each is (regex, key, value). These are what make the Chipathon Block A
    # part expressible from a prompt: it is defined as much by what it REMOVES
    # as by its core list, and before this the grammar could not say "no DMA".
    _PLATFORM_RULES = (
        (r"\b(?:no|without|drop(?:ping)?|omit(?:ting)?)\s+(?:the\s+)?dma\b", "dma", "none"),
        (r"\bdma\s*[:=]?\s*none\b", "dma", "none"),
        (r"\b(?:no|without)\s+(?:the\s+)?(?:debug(?:\s+module)?|jtag)\b", "debug", False),
        (r"\b(?:no|without)\s+(?:the\s+)?(?:plic|interrupt\s+controller)\b", "plic", False),
        (r"\b(?:no|without)\s+(?:the\s+)?(?:multicore|multi-core)\s+timer\b",
         "multicore_timer", False),
        (r"\b(?:no|without)\s+(?:the\s+)?(?:ao\s+)?(?:gpio|gpio[\s-]*ao)\b", "gpio_ao", False),
        (r"\b(?:no|without)\s+(?:the\s+)?(?:ao\s+)?rv[\s_-]*timer\b", "ao_rv_timer", False),
        (r"\b(?:no|without)\s+(?:the\s+)?fast\s+interrupts?\b", "ao_fast_intr", False),
        # XIP: read-only execute-in-place reader instead of a full SPI host.
        (r"\b(?:xip|execute[\s-]*in[\s-]*place|read[\s-]*only\s+(?:spi|flash))\b",
         "spi_mode", "xip_only"),
    )
    for pattern, key, value in _PLATFORM_RULES:
        m = re.search(pattern, low)
        if m:
            intent.platform[key] = value
            consume(m, f"{key} = {value!r}")

    # ── bus ──
    m = re.search(r"\b(floonoc|noc)\b", low)
    if m:
        intent.bus = "floonoc"
        consume(m, "bus floonoc")
    else:
        m = re.search(r"\blog(?:arithmic)?[\s-]*(?:xbar|interconnect|bus)?\b", low)
        if m and "log" in m.group(0):
            intent.bus = "log"
            consume(m, "bus log")

    # ── scheduler ──
    tdu_off = re.search(
        r"\b(?:no|without|disable(?:d)?)\s+(?:the\s+)?"
        r"(tdu|task[\s-]*dispatch(?:er)?|scheduler|wake)\b",
        low,
    )
    if tdu_off:
        intent.tdu = False
        intent.tdu_explicit = True
        consume(tdu_off, "tdu explicitly off")
    else:
        m = re.search(r"\b(tdu|task[\s-]*dispatch(?:er)?|scheduler|wake)\b", low)
        if m:
            intent.tdu = True
            intent.tdu_explicit = True
            consume(m, "tdu on")
    m = re.search(r"\b(power[\s-]*aware|dynamic|static)\b", low)
    if m:
        intent.sched_mode = m.group(1).replace(" ", "-").replace("power-aware",
                                                                 "power-aware")
        intent.sched_mode = "power-aware" if "power" in m.group(1) else m.group(1)
        consume(m, f"sched {intent.sched_mode}")

    # ── peripherals ──
    no_peripherals = re.search(
        r"\b(?:no|without)\s+(?:any\s+)?peripherals?\b", low
    )
    if no_peripherals:
        intent.peripherals_explicit = True
        consume(no_peripherals, "no peripherals")
    denied_peripherals = {
        canonical
        for word, canonical in {**PERIPH_SYNONYMS, **{p: p for p in VALID_PERIPHERALS}}.items()
        if re.search(rf"\b(?:no|without)\s+{re.escape(word)}s?\b", low)
    }
    # With spi_mode: xip_only the flash interface is the ALWAYS-ON spi_subsystem
    # reduced to its XIP reader -- it is not a user peripheral. So "XIP from
    # flash" must not add a `spi` peripheral, which would put back the full
    # OpenTitan host the prompt just asked to remove (0.717 mm2 of it).
    if intent.platform.get("spi_mode") == "xip_only":
        denied_peripherals.add("spi")
        intent.matched.append("spi peripheral excluded (xip_only is not a SPI host)")

    if denied_peripherals:
        intent.peripherals_explicit = True
    # Scan a MASKED copy: spans already claimed by the core, memory and
    # platform rules are blanked out. Without this, "no rv timer" contributes a
    # `timer` peripheral and "XIP from flash" contributes `spi` -- the exact
    # blocks the prompt just asked to remove.
    masked = list(low)
    for start, end in consumed_spans:
        for k in range(start, min(end, len(masked))):
            masked[k] = " "
    masked = "".join(masked)

    for word, canon in list(PERIPH_SYNONYMS.items()):
        m = re.search(rf"\b{word}\b", masked)
        if m and canon not in denied_peripherals and canon not in intent.peripherals:
            intent.peripherals.append(canon)
            intent.peripherals_explicit = True
            consume(m, f"peripheral {canon} (from '{word}')")
    for p in sorted(VALID_PERIPHERALS):
        m = re.search(rf"\b{p}s?\b", masked)
        if m and p not in denied_peripherals and p not in intent.peripherals:
            intent.peripherals.append(p)
            intent.peripherals_explicit = True
            consume(m, f"peripheral {p}")

    # ── preset escape (only when no cores were named) ──
    if not intent.core_groups:
        m = re.search(r"\b(minimal|poc|proof[\s-]*of[\s-]*concept|max[\s_-]*cores?)\b", low)
        if m:
            intent.preset = {"proof of concept": "poc", "proof-of-concept": "poc"}.get(
                m.group(1), m.group(1).replace(" ", "_").replace("-", "_"))
            if intent.preset == "max_core":
                intent.preset = "max_cores"
            consume(m, f"preset {intent.preset}")

    # ── unrecognized content tokens (everything not consumed / stopword) ──
    for tm in re.finditer(r"[a-z0-9_+-]+", low):
        if any(s <= tm.start() and tm.end() <= e for s, e in consumed_spans):
            continue
        tok = tm.group(0)
        if (
            tok in _STOPWORDS
            or tok in ROLE_WORDS
            or tok in VALID_PERIPHERALS
            or tok in PERIPH_SYNONYMS
            or tok.isdigit()
            or tok in _NUMBER_WORDS
        ):
            continue
        if tok in ("kb", "mb", "sram", "ram", "memory", "bus"):
            continue
        intent.unrecognized.append(tok)

    return intent


def _repair(intent: ParsedIntent) -> None:
    """Deterministic, REPORTED repairs — never silent."""
    if intent.preset:
        return
    groups = intent.core_groups
    # role-less: biggest core first becomes titan, others atlas/nano
    if groups and not any(g.get("role") == "titan" for g in groups):
        # prefer an obviously orchestrator-class core if present
        titan_order = ["cv32e20", "cv32e40x", "cv32e40p", "cv32e40px", "ibex",
                       "cva6", "rocket", "boom"]
        chosen = None
        for t in titan_order:
            for g in groups:
                if g["ip"] == t and not g.get("role"):
                    chosen = g
                    break
            if chosen:
                break
        if chosen:
            chosen["role"] = "titan"
            if chosen["count"] > 1:
                # split: 1 titan + rest workers
                groups.append({"ip": chosen["ip"], "count": chosen["count"] - 1,
                               "role": "atlas"})
                chosen["count"] = 1
                intent.repairs.append(
                    f"split {chosen['ip']} group: 1 titan + rest atlas")
            intent.repairs.append(f"assigned titan role to {chosen['ip']}")
        elif (intent.tdu_explicit and not intent.tdu
              and any(not g.get("role") for g in groups)):
            # The TDU is off by request, so there are no workers to orchestrate
            # and the core the request names IS the orchestrator. Adding a
            # cv32e20 here would invent a second core and demote the named one
            # to a worker that the disabled TDU can never wake, which the gate
            # below then refuses -- the request would be rejected for a
            # topology nobody asked for. Found by harness/prompt_eval.py on
            # "a single picorv32 with no TDU". A role the request DID state is
            # left alone, so explicitly asking for workers without the TDU is
            # still refused.
            first = next(g for g in groups if not g.get("role"))
            first["role"] = "titan"
            intent.repairs.append(
                f"TDU off by request: assigned titan role to {first['ip']}")
        else:
            groups.insert(0, {"ip": "cv32e20", "count": 1, "role": "titan"})
            intent.repairs.append("no orchestrator named: added 1x cv32e20 titan")
    # remaining role-less groups: atlas
    for g in groups:
        if not g.get("role"):
            g["role"] = "atlas"
            intent.repairs.append(f"assigned atlas role to {g['ip']}")
    # Berkeley tiles reset through their translated cacheable SRAM window even
    # when they are the simulation controller.  Unlike native TITANs, that
    # translated reset address must be explicit in the public config.
    for g in groups:
        if (
            g.get("role") == "titan"
            and g.get("ip") in {"rocket", "boom"}
            and "boot_addr" not in g
        ):
            g["boot_addr"] = 0x180
            intent.repairs.append(
                f"assigned {g['ip']} TITAN translated boot address 0x180"
            )
    # Workers are dormant until a TDU wake, so a worker topology with no
    # dispatcher builds harts that nothing can ever start -- and the flow
    # below would not even give them boot slots. A request that SAYS "no TDU"
    # is a contradiction and plan() refuses it; a request that simply never
    # mentions one gets it, and is told so. Found by harness/prompt_eval.py:
    # "16 fazyrv nano workers" was accepted with the TDU off.
    if (not intent.tdu and not intent.tdu_explicit
            and any(g.get("role") not in (None, "titan") for g in groups)):
        intent.tdu = True
        intent.repairs.append(
            "workers cannot wake themselves: enabled the TDU")

    # TDU workers need boot addresses. Each worker HART needs its OWN boot
    # slot, so multi-count worker groups
    # are flattened to single-hart groups first (a count-2 group with one
    # boot_addr would run BOTH workers at 0x1000).
    if intent.tdu:
        flat: List[Dict[str, Any]] = []
        for g in groups:
            if g["role"] == "titan" or g["count"] == 1:
                flat.append(g)
            else:
                for _ in range(g["count"]):
                    flat.append({**g, "count": 1})
                intent.repairs.append(
                    f"flattened {g['count']}x {g['ip']} workers to per-hart groups")
        groups[:] = flat
        workers = [g for g in groups if g["role"] != "titan"]
        # Preserve the canonical two-worker role shape while assigning every
        # worker an independent generated image slot.
        if len(workers) == 2:
            workers[0]["role"], workers[1]["role"] = "atlas", "nano"
        boots = iter(worker_boot_slots(intent.sram_kb))
        for g in workers:
            if "boot_addr" not in g:
                try:
                    g["boot_addr"] = next(boots)
                except StopIteration:
                    break
        intent.repairs.append("assigned worker boot addresses (0x1000, 0x2000, ...)")


def _llm_intent(text: str) -> Optional[ParsedIntent]:
    """Translate via the user-configured API driver (setup skill). Returns
    None when no api driver is configured or the call fails — the caller
    falls back to the deterministic grammar. The LLM output feeds the SAME
    repair + validation gates as the grammar (translation only, no trust)."""
    from .setup_wizard import load_user_config
    cfg = load_user_config()
    if cfg.get("driver") != "api" or "api" not in cfg:
        return None
    try:
        from ..llm import translate_intent
        from ..core import VALID_PERIPHERALS
        raw = translate_intent(text, cfg["api"], VALID_CORE_IPS,
                               VALID_PERIPHERALS, CORE_ALIASES)
    except Exception as e:  # noqa: BLE001 — any API failure -> fallback
        log.warning(f"llm intent translation failed ({e}); using the "
                    f"deterministic grammar")
        return None
    return intent_from_json(raw)


def intent_from_json(raw: Dict[str, Any]) -> ParsedIntent:
    """A model's JSON reply -> ParsedIntent, trusting none of it.

    Unregistered cores and peripherals are dropped here rather than refused,
    because the model is asked to put what it did not recognise into
    `unrecognized`, and `plan` fails closed on that. Split out of
    `_llm_intent` so an offline evaluation (harness.prompt_eval) scores
    answers through this exact mapping instead of a copy that would drift.
    """
    intent = ParsedIntent(name=raw.get("name") or "mosaic_prompted")
    for c in raw.get("cores", []):
        if c.get("ip") in VALID_CORE_IPS:
            g = {"ip": c["ip"], "count": int(c.get("count", 1))}
            if c.get("role") in ("titan", "atlas", "nano"):
                g["role"] = c["role"]
            if isinstance(c.get("isa"), str):
                g["isa"] = c["isa"]
            intent.core_groups.append(g)
            intent.matched.append(
                f"{g['count']}x {g['ip']}"
                + (f" ({g.get('role')})" if g.get("role") else "")
                + " [llm]")
    intent.sram_kb = raw.get("sram_kb")
    intent.boot_rom_kb = raw.get("boot_rom_kb")
    intent.scratchpad_bytes = raw.get("scratchpad_bytes")
    # A part with no on-chip RAM pool is a real profile -- it is what Block A
    # ships, running XIP from flash -- but a bare `sram_kb: 0` is refused as a
    # typo, and the grammar can only hear the decision in words. Without a
    # field of its own a model cannot express that design at all.
    if raw.get("no_sram"):
        intent.sram_zero_by_words = True
        if intent.sram_kb is None:
            intent.sram_kb = 0
    clock = raw.get("target_clock_mhz")
    intent.target_clock_mhz = (float(clock)
                               if isinstance(clock, (int, float))
                               and not isinstance(clock, bool) else None)
    intent.bus = raw.get("bus") if raw.get("bus") in ("obi", "log",
                                                      "floonoc") else None
    # null means "the request did not say", exactly as for sram_kb and bus.
    # Treating a null as an explicit `false` would refuse every worker
    # topology whose request never mentioned the TDU: the prompt evaluation
    # measured that costing 2 of 6 requests before the contract said so.
    intent.tdu = bool(raw.get("tdu"))
    intent.tdu_explicit = raw.get("tdu") is not None
    if raw.get("sched_mode") in ("static", "dynamic", "power-aware"):
        intent.sched_mode = raw["sched_mode"]
    intent.peripherals = [p for p in raw.get("peripherals", [])
                          if p in VALID_PERIPHERALS]
    intent.peripherals_explicit = "peripherals" in raw
    intent.unrecognized = list(raw.get("unrecognized", []))
    return intent


class SocFromPrompt:
    """Skill: deterministic prompt → validated config → verified SoC."""

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or REPO_ROOT

    def plan(self, text: str, use_llm: bool = False,
             intent: Optional[ParsedIntent] = None) -> SkillResult:
        """Parse only — show how the request is read. Writes nothing.

        use_llm: translate via the configured api driver (setup skill);
        falls back to the deterministic grammar on any failure. Repairs and
        validation are identical either way.
        """
        # `intent` supplied: score a reply that was obtained elsewhere (see
        # harness.prompt_eval) through exactly these gates.
        intent = (intent or (_llm_intent(text) if use_llm else None)
                  or parse_prompt(text))
        _repair(intent)
        invalid_memory = []
        # sram_kb: 0 is a real profile -- the Chipathon Block A part has NO
        # on-chip RAM pool and executes XIP from external flash -- but only when
        # the prompt SAID so. "0 KB sram" as a quantity is still treated as a
        # slip, because silently building a RAM-less SoC from a typo is worse
        # than asking.
        if intent.sram_kb is not None and intent.sram_kb < 0:
            invalid_memory.append("SRAM size cannot be negative")
        elif intent.sram_kb == 0 and not intent.sram_zero_by_words:
            invalid_memory.append(
                "0 KB SRAM: write 'no sram' if you mean an external-memory-only part"
            )
        if intent.boot_rom_kb is not None and intent.boot_rom_kb <= 0:
            invalid_memory.append("boot ROM must be greater than 0 KB")
        if invalid_memory:
            return SkillResult(
                ok=False,
                skill="soc-from-prompt",
                summary="invalid memory size in prompt",
                errors=invalid_memory,
            )
        if (
            intent.tdu_explicit
            and not intent.tdu
            and any(group.get("role") != "titan" for group in intent.core_groups)
        ):
            return SkillResult(
                ok=False,
                skill="soc-from-prompt",
                summary="worker cores require the TDU; request explicitly disables it",
                errors=["remove 'no/without TDU' or use a TITAN-only topology"],
            )
        sim_only = {g["ip"] for g in intent.core_groups} & SIM_ONLY_CORES
        warnings = []
        if sim_only and re.search(r"\btape\s*-?out\b", text.lower()):
            return SkillResult(
                ok=False, skill="soc-from-prompt",
                summary=f"sim-only cores {sorted(sim_only)} requested for tapeout",
                errors=["cva6/rocket/boom are SIMULATION-ONLY (GF180 tapeout "
                        "exclusion) — drop them or drop 'tapeout'"],
            )
        if sim_only:
            warnings.append(f"note: {sorted(sim_only)} are simulation-only cores")
        if intent.unrecognized:
            return SkillResult(
                ok=False,
                skill="soc-from-prompt",
                summary="material prompt clauses were not understood",
                details={"intent": asdict(intent), "warnings": warnings},
                errors=[
                    "unrecognized: " + ", ".join(intent.unrecognized),
                    "rephrase using supported core/count/role, ISA, boot address, "
                    "chunksize, memory, bus, scheduler, and peripheral fields",
                ],
            )
        # Every worker hart needs its OWN boot slot, and the defaults are a
        # hand-picked list that steps around the 0x3000 sentinel window. When
        # a request asks for more workers than there are slots, the flow used
        # to generate the surplus with no boot address at all -- harts with
        # nowhere to start. Inventing addresses here would need the memory map
        # this stage does not have, so refuse and say what to do instead.
        # Found by harness/prompt_eval.py on "16 fazyrv nano workers".
        unslotted = [g for g in intent.core_groups
                     if g.get("role") != "titan" and "boot_addr" not in g]
        if unslotted:
            return SkillResult(
                ok=False,
                skill="soc-from-prompt",
                summary=(f"{len(unslotted)} worker hart(s) would have no boot "
                         "slot"),
                details={"intent": asdict(intent), "warnings": warnings},
                errors=[
                    f"the stated memory leaves "
                    f"{len(worker_boot_slots(intent.sram_kb))} worker boot "
                    f"slot(s) and each worker hart needs its own; ask for "
                    f"fewer workers, state more SRAM, or give boot addresses "
                    f"per core group in the config"],
            )
        ok = bool(intent.core_groups or intent.preset)
        summary = ("parsed: " + "; ".join(intent.matched) if intent.matched
                   else "nothing recognized in the prompt")
        if intent.repairs:
            summary += f" | repairs: {'; '.join(intent.repairs)}"
        if warnings:
            summary += " | " + "; ".join(warnings)
        return SkillResult(
            ok=ok, skill="soc-from-prompt",
            summary=summary,
            details={"intent": asdict(intent), "warnings": warnings},
            errors=[] if ok else ["no cores or preset recognized — name at "
                                  f"least one of {sorted(VALID_CORE_IPS)}"],
        )

    def run(self, text: str, execute: bool = False,
            name: Optional[str] = None, use_llm: bool = False) -> SkillResult:
        """Write the config (and with execute=True, run the full pipeline)."""
        planned = self.plan(text, use_llm=use_llm)
        if not planned.ok:
            return planned
        intent = ParsedIntent(**planned.details["intent"])
        if name:
            intent.name = name

        from .config_author import ConfigAuthor
        author = ConfigAuthor(self.repo_root)
        if intent.preset:
            gen = author.generate(name=intent.name, preset=intent.preset)
        else:
            gen = author.generate(
                name=intent.name,
                cores=intent.core_groups,
                sram_kb=(32 if intent.sram_kb is None else intent.sram_kb),
                boot_rom_kb=(
                    2 if intent.boot_rom_kb is None else intent.boot_rom_kb
                ),
                bus=intent.bus or "obi",
                tdu=intent.tdu,
                sched_mode=intent.sched_mode or ("dynamic" if intent.tdu else "static"),
                peripherals=(
                    intent.peripherals
                    if intent.peripherals_explicit
                    else ["uart"]
                ),
                scratchpad_bytes=intent.scratchpad_bytes,
                platform=intent.platform or None,
                target_clock_mhz=intent.target_clock_mhz,
                repair_margin_pct=intent.repair_margin_pct,
                target=intent.target or "rtl",
            )
        stages: Dict[str, Any] = {"plan": planned.details,
                                  "config": {"ok": gen.ok, "summary": gen.summary,
                                             "errors": gen.errors}}
        if not gen.ok:
            return SkillResult(ok=False, skill="soc-from-prompt",
                               summary=f"config generation failed: {gen.summary}",
                               details=stages, errors=gen.errors)
        cfg_path = gen.details["path"]
        stages["config"]["path"] = cfg_path

        # A diagram of what was asked for, before any RTL exists: the view is
        # derived from the generator itself, so it is cheap and cannot drift.
        # Not a gate -- the topo-viz check below is -- so a failure is recorded.
        from .topo_viz import TopoViz
        diagram = TopoViz(self.repo_root).render(
            Path(cfg_path), output=Path(cfg_path).with_suffix(".diagram.html"))
        stages["diagram"] = {"ok": diagram.ok, "summary": diagram.summary,
                             "path": diagram.details.get("output"),
                             "errors": diagram.errors}

        if not execute:
            return SkillResult(
                ok=True, skill="soc-from-prompt",
                summary=f"config written: {cfg_path} (use --run to generate + verify)",
                details=stages,
            )

        # ── gated pipeline ──
        from .flow_runner import FlowRunner
        from .doc_gen import DocGen

        chk = TopoViz().check(Path(cfg_path))
        stages["topo_check"] = {"ok": chk.ok, "summary": chk.summary,
                                "errors": chk.errors}
        if not chk.ok:
            return SkillResult(ok=False, skill="soc-from-prompt",
                               summary=f"GATE topo-viz check failed: {chk.summary}",
                               details=stages, errors=chk.errors)

        runner = FlowRunner(self.repo_root)
        gen_run = runner.run("mosaic-gen-config", config=cfg_path)
        stages["mosaic_gen"] = {"ok": gen_run.ok, "summary": gen_run.summary}
        if not gen_run.ok:
            return SkillResult(ok=False, skill="soc-from-prompt",
                               summary=f"GATE mosaic-gen failed: {gen_run.summary}",
                               details=stages, errors=gen_run.errors)

        generic = runner.run("tb-soc-generic", config=cfg_path)
        stages["generic_liveness"] = {
            "ok": generic.ok,
            "summary": generic.summary,
            "metrics": generic.details.get("metrics", {}),
        }
        if not generic.ok:
            return SkillResult(ok=False, skill="soc-from-prompt",
                               summary=f"GATE generic liveness failed: {generic.summary}",
                               details=stages, errors=generic.errors)

        doc = DocGen().config_summary(Path(cfg_path))
        stages["doc"] = {"ok": doc.ok, "summary": doc.summary}

        return SkillResult(
            ok=True, skill="soc-from-prompt",
            summary=f"SoC '{intent.name}' generated AND verified "
                    f"(all-hart liveness EXIT SUCCESS) from prompt — config {cfg_path}",
            details=stages,
        )
