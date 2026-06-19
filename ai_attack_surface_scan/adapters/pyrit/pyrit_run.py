"""PyRIT runner — executes a bounded multi-turn attack and writes a results JSON.

Runs in /opt/venv-pyrit (isolated from garak). SELF-CONTAINED: only pyrit +
stdlib, no spine imports. The adapter (base interpreter) passes a config JSON and
parses the results JSON. See adapters/pyrit/TOOL_API.md (pyrit 0.14.0).

Target model: we use OpenAIChatTarget (not HTTPTarget) for the victim, because
multi-turn crescendo needs the target to carry conversation history, and a
stateless HTTPTarget with a single {PROMPT} can't. OpenAIChatTarget speaks the
OpenAI chat protocol (which Ollama/vLLM/OpenAI-compat all serve) and sends the
full history each turn. So PyRIT applies to OpenAI-compat chat endpoints.
"""
import asyncio
import json
import sys
from urllib.parse import urlparse


def _v1_endpoint(baseurl: str, path: str | None) -> str:
    """The OpenAI base the SDK appends /chat/completions to (up to /v1)."""
    u = urlparse(baseurl)
    base = f"{u.scheme}://{u.netloc}"
    p = path or ""
    if "/v1/" in p:
        return base + p[: p.index("/v1/") + 3]   # ".../v1"
    if p.rstrip("/").endswith("/v1"):
        return base + p.rstrip("/")
    return base + "/v1"


def _victim_auth(cfg) -> tuple[str, str | None]:
    """Return (api_key, headers_json_or_None) for the victim OpenAIChatTarget.

    Bearer -> api_key (SDK sends Authorization: Bearer). Custom header -> a
    headers JSON string. None -> a dummy key (the SDK requires a non-empty key;
    local servers ignore it)."""
    header = (cfg.get("auth_header") or "").strip()
    key = cfg.get("api_key") or ""
    if header and key:
        if header.lower() == "authorization" and (cfg.get("auth_scheme") or "").lower() == "bearer":
            return key, None
        val = f"{cfg.get('auth_scheme', '')} {key}".strip()
        return "x", json.dumps({header: val})
    return "x", None


async def _run(cfg):
    from pyrit.setup import initialize_pyrit_async
    from pyrit.prompt_target import OpenAIChatTarget
    from pyrit.executor.attack import (
        CrescendoAttack, SkeletonKeyAttack, AttackAdversarialConfig, AttackScoringConfig,
    )
    from pyrit.score import SelfAskRefusalScorer
    from pyrit.memory import CentralMemory

    await initialize_pyrit_async(memory_db_type="InMemory")

    seed = int(cfg.get("seed", 0))
    vk, vh = _victim_auth(cfg)
    victim = OpenAIChatTarget(
        model_name=cfg.get("model") or "default",
        endpoint=_v1_endpoint(cfg["baseurl"], cfg.get("path")),
        api_key=vk, headers=vh, temperature=0.0, seed=seed)

    judge = OpenAIChatTarget(
        model_name=cfg["judge_model"],
        endpoint=_v1_endpoint(cfg["judge_base_url"], None),
        api_key="ollama-local", temperature=0.0, seed=seed)

    scoring = AttackScoringConfig(objective_scorer=SelfAskRefusalScorer(chat_target=judge))
    attack_name = cfg.get("attack", "crescendo")
    max_turns = int(cfg.get("max_turns", 4))

    results = []
    for objective in cfg["objectives"]:
        try:
            if attack_name == "skeleton_key":
                attack = SkeletonKeyAttack(objective_target=victim, attack_scoring_config=scoring)
            else:
                attack = CrescendoAttack(
                    objective_target=victim,
                    attack_adversarial_config=AttackAdversarialConfig(target=judge),
                    attack_scoring_config=scoring, max_turns=max_turns,
                    max_backtracks=int(cfg.get("max_backtracks", 5)))
            r = await attack.execute_async(objective=objective)

            conversation = []
            try:
                mem = CentralMemory.get_memory_instance()
                for piece in mem.get_conversation(conversation_id=r.conversation_id):
                    for rp in getattr(piece, "request_pieces", [piece]):
                        conversation.append({
                            "role": getattr(rp, "role", "?"),
                            "content": getattr(rp, "converted_value", getattr(rp, "original_value", "")),
                        })
            except Exception as e:
                conversation = [{"role": "error", "content": f"transcript unavailable: {e}"}]

            score = getattr(r, "last_score", None)
            results.append({
                "objective": objective,
                "outcome": str(getattr(r, "outcome", "UNDETERMINED")).split(".")[-1].upper(),
                "turns_used": getattr(r, "executed_turns", None),
                "last_score": getattr(score, "score_value", None) if score is not None else None,
                "outcome_reason": getattr(r, "outcome_reason", None),
                "conversation": conversation,
            })
        except Exception as e:
            results.append({"objective": objective, "outcome": "ERROR", "turns_used": None,
                            "last_score": None, "outcome_reason": str(e), "conversation": []})

    return {"pyrit_version": "0.14.0", "attack": attack_name, "seed": seed,
            "max_turns": max_turns, "results": results}


def main():
    cfg = json.load(open(sys.argv[1]))
    out = asyncio.run(_run(cfg))
    with open(cfg["out"], "w") as f:
        json.dump(out, f, indent=2)
    print(f"[pyrit_run] wrote {len(out['results'])} result(s) to {cfg['out']}")


if __name__ == "__main__":
    main()
