"""promptfoo plugin id -> OWASP-LLM id + attack chip, plus the chip -> (plugins,
strategies) selection map (TOOL_API.md §0, §5, §8).

promptfoo splits PLUGINS (the vulnerability tested) from STRATEGIES (the delivery
/ mutation). Findings are keyed on the *plugin* (the vulnerability); the strategy
that achieved the worst ASR is recorded in the finding's evidence.

EMPIRICAL CONSTRAINTS (TOOL_API.md §8, verified live against Ollama):
1. promptfoo's generation-based plugins (pii, harmful, indirect-prompt-injection)
   need promptfoo's hosted service (now email-gated) or a capable local model;
   with a small local model they emit EMPTY payloads. Off by default.
2. DATASET-based plugins pull static payloads from HuggingFace and grade locally.
   But not all are single-turn: some (cyberseceval, donotanswer) emit `prompt` as
   a JSON *messages array* (a conversation), which the single-turn HTTP body
   template can't carry -- the request errors and the rows drop. The VERIFIED
   single-turn dataset plugins (beavertails, harmbench, pliny) emit a plain-string
   prompt and produce findings end-to-end. **Those are our defaults / card probes.**
   Conversation-style plugins stay mapped but are excluded from defaults pending an
   OpenAI-native-provider path (TOOL_API.md §8.7).

Plugin ids are matched longest-prefix-first, so `pii:direct` resolves before the
`pii` collection and `harmful:hate` before `harmful`.
"""
from __future__ import annotations

# plugin id (or id prefix) -> (owasp_llm_id, chip). Ordered longest-first in map_plugin.
# Toxicity uses the "safety" pseudo-OWASP id, matching the garak adapter convention.
PLUGIN_MAP: dict[str, tuple[str, str]] = {
    # --- dataset-based (offline, zero-egress, our defaults) ---
    "beavertails":  ("safety", "toxicity"),
    "harmbench":    ("safety", "toxicity"),
    "donotanswer":  ("safety", "toxicity"),
    "cyberseceval": ("LLM01", "prompt-injection"),
    "pliny":        ("LLM01", "jailbreak"),
    # --- generation-based (need a capable judge/generation model; off by default) ---
    "indirect-prompt-injection": ("LLM01", "prompt-injection"),
    "prompt-extraction":         ("LLM07", "system-prompt-leak"),
    "harmful:hate":              ("safety", "toxicity"),
    "harmful":                   ("safety", "toxicity"),
    "hijacking":                 ("LLM01", "prompt-injection"),
    "pii:direct":                ("LLM02", "data-disclosure"),
    "pii:api-db":                ("LLM02", "data-disclosure"),
    "pii:session":               ("LLM02", "data-disclosure"),
    "pii":                       ("LLM02", "data-disclosure"),
}

_DEFAULT = ("LLM01", "prompt-injection")

# Chip -> selection. Plugins drive findings; strategies amplify the attack.
# Only VERIFIED single-turn dataset plugins are mapped here (cyberseceval is a
# conversation plugin -> excluded; see module docstring). The `jailbreak` STRATEGY
# may need remote generation, so the default strategy is `basic` for reliability.
CHIP_SELECTION: dict[str, dict] = {
    "jailbreak":  {"plugins": ["pliny"], "strategies": ["basic"]},
    "toxicity":   {"plugins": ["beavertails", "harmbench"], "strategies": ["basic"]},
}

# Single-turn dataset plugins verified to produce findings end-to-end. Used to flag
# a degraded selection (anything else may emit empty / conversation-shaped payloads).
OFFLINE_PLUGINS = {"beavertails", "harmbench", "pliny"}

# promptfoo STRATEGIES that are pure static text transforms — deterministic,
# local, ZERO egress (they wrap each dataset payload in an encoding). Verified
# against the promptfoo dist. `basic` = no transform. The adaptive strategies
# (jailbreak, crescendo, prompt-injection, citation, math-prompt, multilingual)
# require promptfoo's remote inference service, so they are NOT offered here.
LOCAL_STRATEGIES = {"basic", "base64", "rot13", "leetspeak", "morse", "piglatin"}

# Conservative default run: toxicity + jailbreak, both verified single-turn.
DEFAULT_PLUGINS = ["beavertails", "pliny"]
DEFAULT_STRATEGIES = ["basic"]


def map_plugin(plugin_id: str) -> tuple[str, str]:
    """Return (owasp_llm_id, chip) for a promptfoo plugin id (longest-prefix)."""
    pid = plugin_id or ""
    for prefix in sorted(PLUGIN_MAP, key=len, reverse=True):
        if pid == prefix or pid.startswith(prefix + ":"):
            return PLUGIN_MAP[prefix]
    return _DEFAULT


def resolve_selection(chips: list[str] | None) -> tuple[list[str], list[str]]:
    """Chips -> (plugins, strategies), de-duped, order-stable. Falls back to the
    conservative offline default when no chips are given."""
    if not chips:
        return list(DEFAULT_PLUGINS), list(DEFAULT_STRATEGIES)
    plugins: list[str] = []
    strategies: list[str] = []
    for chip in chips:
        sel = CHIP_SELECTION.get(chip)
        if not sel:
            continue
        for p in sel["plugins"]:
            if p not in plugins:
                plugins.append(p)
        for s in sel["strategies"]:
            if s not in strategies:
                strategies.append(s)
    return (plugins or list(DEFAULT_PLUGINS),
            strategies or list(DEFAULT_STRATEGIES))
