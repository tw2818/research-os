"""Cross-branch synthesis: what the parent learns from its children.

A parent topic with branches under it already knows the branches exist. That
is not the same as understanding them: knowing there is a cognition-domain
branch, a course branch and a circuit branch says nothing about where they
agree, where they contradict each other, and which of them changes what the
parent's story claims.

The deterministic part is here, and it is deterministic on purpose. Finding
shared sources, near-duplicate claims across branches, unresolved conflicts
and branches that closed after the parent last updated its narrative is
counting, not judgement. The judgement — what the overlap *means*, which
conclusion gives way — is the agent's, and the gate reviews it.

Keeping the two apart matters: a model asked to "synthesise the branches"
will produce a plausible paragraph about their relationship whether or not
the material supports one. A digest of what is actually there removes the
space for that paragraph to be invented, and leaves the agent the part it is
actually better at.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

# Tokens too common to say anything about two claims being the same claim.
_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "were", "was", "are",
    "not", "but", "its", "their", "than", "then", "which", "when", "what",
    "have", "has", "had", "into", "over", "under", "does", "did", "can", "may",
    "vs", "versus", "effect", "effects", "study", "studies", "group", "groups",
    # Negation is deliberately absent from this set. It is not a word that
    # carries no meaning in a comparison - it is the word that decides
    # whether two nearly identical claims agree or contradict. Dropping it
    # would make "improved" and "did not improve" the same token bag and
    # report a contradiction as a duplicate.
    "的", "了", "和", "与", "在", "是", "对", "有", "个", "这", "那", "也",
    "研究", "结果", "患者", "分析", "显著", "差异", "相关",
}

_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")

# Phrases that flip a claim's polarity. Multi-character forms only: a bare
# 不 turns up inside 不同 / 不仅 / 不断, which are not negations of the claim
# at all, and a false negation flag is worse than none - it would turn every
# ordinary pair into a reported conflict.
_NEGATION = re.compile(
    r"(未(?:见|能|发现|达到|显著|改善|出现)|没有(?:显著)?|无(?:显著)?(?:差异|改善|变化)"
    r"|不(?:显著|支持|成立|改善|存在|一致)|并不|未能|not\b|no\b|never\b"
    r"|without\b|absence\s+of|failed\s+to|did\s+not|does\s+not|was\s+not"
    r"|were\s+not|is\s+not|are\s+not|didn't|doesn't|wasn't|weren't|isn't|aren't)",
    re.I,
)


def _negated(text: str) -> bool:
    """Whether the claim asserts an absence rather than a presence."""
    return bool(_NEGATION.search(str(text or "")))


def _tokens(text: str) -> set[str]:
    """Words for Latin text, character bigrams for Chinese.

    Chinese has no spaces, so a whole run is one token and two sentences that
    differ by three characters share nothing. Bigrams are the smallest unit
    that still means something: they make "八周安慰剂对照未见额外认知增益" and
    "八周安慰剂对照未见额外认知增益的重复报告" overlap the way a reader would
    say they overlap.
    """
    out: set[str] = set()
    lowered = str(text or "").lower()
    for word in _WORD.findall(lowered):
        if word not in _STOP:
            out.add(word)
    for run in _CJK_RUN.findall(lowered):
        if len(run) < 2:
            continue
        for i in range(len(run) - 1):
            bigram = run[i:i + 2]
            if bigram not in _STOP:
                out.add(bigram)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _clip(text: Any, limit: int = 150) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def build_digest(
    parent: str,
    *,
    parent_map: Dict[str, Any],
    branches: Sequence[Dict[str, Any]],
    branch_state: Dict[str, Dict[str, Any]],
    threshold: float = 0.55,
) -> Dict[str, Any]:
    """What the parent can learn from its branches, as facts.

    `branch_state` maps a branch's child-topic name to what that topic holds:
    {"phase", "narrative", "claims": [...], "contradictions": [...],
     "protocol_locked"}.

    Nothing here decides anything. It reports: which sources more than one
    branch relies on, which pairs of claims say nearly the same thing, which
    conflicts are still open, and which branches finished after the parent's
    story was last revised.
    """
    detail: List[Dict[str, Any]] = []
    source_map: Dict[str, List[str]] = {}
    claim_rows: List[Dict[str, Any]] = []
    open_conflicts: List[Dict[str, Any]] = []

    for branch in branches:
        branch_id = str(branch.get("id") or "")
        child = str(branch.get("child") or "").strip()
        state = branch_state.get(child) or {}
        claims = [c for c in (state.get("claims") or []) if isinstance(c, dict)]
        sources = sorted({str(c.get("source") or "").strip() for c in claims if str(c.get("source") or "").strip()})
        for src in sources:
            source_map.setdefault(src, []).append(branch_id)
        for c in claims:
            claim_rows.append({
                "branch": branch_id,
                "kind": str(c.get("kind") or ""),
                "strength": str(c.get("strength") or ""),
                "content": str(c.get("content") or ""),
                "source": str(c.get("source") or ""),
            })
        for item in state.get("contradictions") or []:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "open")
            if status in ("resolved", "settled", "killed"):
                continue
            open_conflicts.append({
                "branch": branch_id,
                "title": _clip(item.get("title"), 90),
                "separator": _clip(item.get("separator"), 120),
            })
        detail.append({
            "branch": branch_id,
            "child": child,
            "route": _clip(branch.get("route"), 120),
            "status": str(branch.get("status") or "open"),
            "contribution": _clip(branch.get("contribution"), 220),
            "phase": str(state.get("phase") or ""),
            "judgment": _clip(state.get("narrative"), 240),
            "claims": len(claims),
            "sources": len(sources),
            "closed_at": branch.get("closed_at"),
        })

    shared_sources = [
        {"source": src, "branches": ids}
        for src, ids in sorted(source_map.items(), key=lambda kv: -len(kv[1]))
        if len(ids) > 1
    ]

    # Pairs that share most of their wording. What the page cannot say is
    # whether they agree: "干预显著改善认知" and "干预未显著改善认知" share
    # nearly every token and mean opposite things. So the pair is labelled
    # with what is checkable - the two claims, their sources, how much
    # wording they share, and whether their polarity differs - and the
    # relation is left as a question for the judgement step. A program that
    # called this pair "near-duplicate" would be handing the reader a
    # conclusion it is not entitled to.
    overlaps: List[Dict[str, Any]] = []
    for i in range(len(claim_rows)):
        for j in range(i + 1, len(claim_rows)):
            a, b = claim_rows[i], claim_rows[j]
            if a["branch"] == b["branch"]:
                continue
            score = _jaccard(_tokens(a["content"]), _tokens(b["content"]))
            if score < threshold:
                continue
            a_neg, b_neg = _negated(a["content"]), _negated(b["content"])
            if a_neg != b_neg:
                hint = "polarity_differs"
            elif a["source"] and a["source"] == b["source"]:
                hint = "same_source"
            else:
                hint = "wording_overlap"
            overlaps.append({
                "score": round(score, 2),
                "branches": [a["branch"], b["branch"]],
                "a": _clip(a["content"], 120),
                "b": _clip(b["content"], 120),
                "a_source": a["source"],
                "b_source": b["source"],
                "hint": hint,
                "relation": "",
            })
    # Polarity-flipped pairs first: if two routes reached opposite
    # conclusions about the same thing, that is the finding, and it must not
    # be buried under a list of pairs that merely repeat each other.
    overlaps.sort(key=lambda row: (row["hint"] != "polarity_differs", -row["score"]))

    narrative_at = 0.0
    try:
        narrative_at = float(parent_map.get("narrative_at") or 0.0)
    except (TypeError, ValueError):
        narrative_at = 0.0
    stale_branches = [
        d for d in detail
        if d["closed_at"] and float(d["closed_at"] or 0) > narrative_at
    ]

    concluded = [d for d in detail if d["status"] in ("concluded", "merged")]
    digest: Dict[str, Any] = {
        "parent": parent,
        "branches": detail,
        "branch_count": len(detail),
        "concluded": len(concluded),
        "shared_sources": shared_sources[:12],
        "overlapping_claims": overlaps[:10],
        "open_conflicts": open_conflicts[:12],
        "unsynthesised": [d["branch"] for d in stale_branches],
    }
    digest["demands"] = _demands(digest)
    return digest


def _demands(digest: Dict[str, Any]) -> List[str]:
    """What the synthesis has to have said, given what the digest found.

    Each demand names a thing that exists in the material, so a narrative that
    answers all of them cannot be a general paragraph about how the branches
    relate.
    """
    out: List[str] = []
    shared = digest.get("shared_sources") or []
    if shared:
        out.append(
            f"{len(shared)} 条文献被两条以上路线共用（如 {shared[0]['source'][:40]} 在 "
            + "、".join(shared[0]["branches"]) + "），要说明是同一份证据支撑两个结论，"
            "还是两条路线读了同一篇的不同部分"
        )
    overlaps = digest.get("overlapping_claims") or []
    flipped = [row for row in overlaps if row.get("hint") == "polarity_differs"]
    if flipped:
        top = flipped[0]
        out.append(
            f"{len(flipped)} 对主张用词几乎一样但极性相反（如「{top['a']}」与「{top['b']}」），"
            "程序分不出这是真冲突还是各自测的不同条件——这一条要你来判，"
            "判完在 relation 里写清是冲突、互补还是不可比较"
        )
    plain = [row for row in overlaps if row.get("hint") != "polarity_differs"]
    if plain:
        top = plain[0]
        out.append(
            f"{len(plain)} 对主张用词高度重合（最强一对相似 {top['score']}），"
            "程序只给候选：是重复、是同一份证据支撑了两个结论，还是措辞巧合，"
            "要逐对写成支持/重复/互补/不可比较"
        )
    conflicts = digest.get("open_conflicts") or []
    if conflicts:
        out.append(
            f"{len(conflicts)} 个冲突还开着，至少挑最影响父课题叙事的那个说："
            f"「{conflicts[0]['title']}」用什么才能分开"
        )
    if digest.get("unsynthesised"):
        out.append(
            "这些路线收掉之后父课题叙事还没动过：" + "、".join(digest["unsynthesised"])
            + "——它们的贡献要么改父课题的判断，要么说明为什么不改"
        )
    if digest.get("concluded", 0) >= 2:
        out.append(
            "两条以上路线都收尾了，父课题要给出跨路线的判断："
            "这些分支合起来支持哪一个答案，哪条线的结果最可能推翻它"
        )
    return out


def summarize(digest: Dict[str, Any]) -> str:
    """The digest as the few lines a caller reads first."""
    return (
        f"{digest.get('parent')}: {digest.get('branch_count')} 条路线，"
        f"{digest.get('concluded')} 条已收；共用文献 {len(digest.get('shared_sources') or [])} 条，"
        f"近似主张 {len(digest.get('overlapping_claims') or [])} 对，"
        f"未解冲突 {len(digest.get('open_conflicts') or [])} 个，"
        f"收掉未综合 {len(digest.get('unsynthesised') or [])} 条"
    )
