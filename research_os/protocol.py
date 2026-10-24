"""The protocol block injected into the user's message while in research mode.

Design constraints this file exists to satisfy:

1. The user never sees this text. It rides the pre_llm_call context channel,
   which Hermes appends to the user message as agent-facing context.
2. It must stay small. Every injected character breaks the prompt cache for
   the rest of the turn, so the block is capped by config `max_inject_chars`.
3. It must not turn the agent into a lecturer. A chore request inside a
   research session must still get a chore answer.
4. It must separate what a source reports from what the agent concludes.
"""

from __future__ import annotations

import re
from typing import Any

try:
    # Private namespace first: the plugin loads its modules under
    # `research_os_impl.*` so they cannot collide with another plugin's
    # `state.py` and cannot go stale behind a reloaded entry module.
    from research_os_impl.state import read_claims, read_map
except ImportError:
    try:
        from .state import read_claims, read_map
    except ImportError:  # loaded flat inside the Hermes plugin directory
        from state import read_claims, read_map  # type: ignore[no-redef]

# The four claim types, stated as the agent's own working vocabulary.
CLAIM_RULES = """写作时区分四类断言，并且每一条都要用
research_mode(action='claim', project='<课题>', claim={...}) 写进账本：
- observation 观察：事实。必须有文献（PMID/DOI），且回原文核过。source 填文献。
- report 转述：他人结论。必须有文献，核到摘要层即可。source 填文献。
- inference 推论：你自己的推理。不需要文献，但 premises 填它依赖的账本行号（如 'L1 L2'），
  且强度不得超过最弱前提（descriptive < suggestive < strong < established）。
  inference_type 填 descriptive / explanatory / predictive / analogy / abductive。
- hypothesis 假设：待检验的想法。不需要文献，但语气必须留作假设，不能写成事实。
claim 参数形如：{'kind':'observation','content':'...','source':'PMID 12345','status':'verified'}"""

# What to do with a claim that has no source yet.
PENDING_RULE = """没有来源的 observation 不要写成事实。改写成 hypothesis，
或写 claim 时 status 留 'pending'，正文里用 REF_PENDING:<作者> 标记待核。"""

# The core discipline: find the evidence that would separate explanations.
DISCIPLINE_RULES = """提问先想清楚：这个问题值不值得做？现有解释有哪些？什么证据能区分它们？
不要为了快速给答案就把不确定性压成一个结论。允许说「不知道」。"""

SCOPE_RULE = """如果用户这轮要的是操作（转格式、导入、装东西、改代码），直接做，不要讨论研究意义。
只有用户问研究问题时才按上面的四类断言来写。"""


def _claims_digest(topic: str, limit: int = 6) -> str:
    """A short view of the ledger: how many claims, and what is still pending."""
    claims = read_claims(topic)
    if not claims:
        return "账本：空"
    by_kind: dict[str, int] = {}
    pending: list[str] = []
    for c in claims:
        k = c.get("kind", "?")
        by_kind[k] = by_kind.get(k, 0) + 1
        if (c.get("status") or "") not in ("verified", "derived", "established"):
            pending.append(c.get("id", "?"))
    bits = ", ".join(f"{k}×{v}" for k, v in sorted(by_kind.items()))
    tail = f"；待核 {len(pending)} 条" if pending else "；无待核"
    return f"账本：{len(claims)} 条（{bits}）{tail}"


def build_research_block(topic: str, config: dict[str, Any] | None = None) -> str:
    """The text injected while a session is in research mode for `topic`."""
    m = read_map(topic)
    if not m:
        return f"[Research OS · {topic}]\n该课题还没有 research-map.json，先读它再回答。"

    hyp = [h for h in (m.get("hypotheses") or []) if str(h).strip()]
    open_q = [q for q in (m.get("open_questions") or []) if str(q).strip()]

    lines = [f"[Research OS · {topic}]"]
    lines.append(f"核心问题：{m.get('core_question', '')}")
    if hyp:
        lines.append("竞争解释：" + " ｜ ".join(hyp))
    if open_q:
        lines.append("待解问题：" + " ｜ ".join(open_q[:3]))
    lines.append(_claims_digest(topic))
    lines.append(CLAIM_RULES)
    lines.append(PENDING_RULE)
    lines.append(DISCIPLINE_RULES)
    lines.append(SCOPE_RULE)
    lines.append(f"（以上仅属于 {topic} 课题，不要套用到别的话题。）")

    block = "\n".join(lines)
    cap = int((config or {}).get("max_inject_chars", 900))
    if len(block) > cap:
        block = _truncate(block, cap)
    return block


def _truncate(block: str, cap: int) -> str:
    """Drop the least load-bearing lines first.

    Order: cap each long line (a 3000-character core question must not eat
    the whole budget), then keep only the priority lines, then hard-cut.
    """
    per_line = max(120, cap // 5)
    lines: list[str] = []
    for line in block.split("\n"):
        if len(line) > per_line:
            line = line[: per_line - 1] + "…"
        lines.append(line)

    keep_priority = (
        r"^\[Research OS",
        r"^核心问题：",
        r"^竞争解释：",
        r"^账本：",
        r"^写作时区分四类断言",
        r"^research_mode\(action='claim'",
        r"^- observation",
        r"^- report",
        r"^- inference",
        r"^- hypothesis",
        r"^  inference_type 填",
        r"^claim 参数形如",
        r"^没有来源的",
        r"^提问先想清楚",
        r"^如果用户这轮",
        r"^（以上仅属于",
    )
    kept = [ln for ln in lines if any(re.match(p, ln) for p in keep_priority)]
    out = "\n".join(kept)
    if len(out) > cap:
        out = out[: cap - 1] + "…"
    return out


def build_idle_block() -> str:
    """Injected in idle mode: almost nothing. Just enough that the agent knows
    the capability exists if a research question shows up mid-chore."""
    return (
        "[Research OS · idle]\n"
        "当前不在科研模式（无需四类断言纪律）。"
        "若用户这轮是研究问题（求解释、比较、查证、假设、证据），"
        "先调 research_mode(action='on', project=...) 进入对应课题，再按四类断言回答。"
    )
