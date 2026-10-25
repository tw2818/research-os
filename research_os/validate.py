"""Claim ledger validation.

The rule this file enforces: an inference is legitimate when its premises are
legitimate and it is not stated more strongly than its weakest premise.

A hard "every sentence needs a citation" rule would forbid reasoning itself.
Instead each claim is typed, and each type carries a different obligation:

    observation  needs a source (a paper) and must be read in the original
    report       needs a source; abstract-level verification is enough
    inference    needs premises that exist in the ledger, and may not be
                 stronger than its weakest premise
    hypothesis   needs no source, but stays marked as untested

Violations are split into `errors` (a claim is malformed or overstated) and
`warnings` (a claim is legal but shaky).
"""

from __future__ import annotations

import re
from typing import Any

try:
    # Private namespace first: the plugin loads its modules under
    # `research_os_impl.*` so they cannot collide with another plugin's
    # `state.py` and cannot go stale behind a reloaded entry module.
    from research_os_impl.state import (
        INFERENCE_TYPES,
        STRENGTHS,
        STRENGTH_RANK,
        list_branches,
        read_claims,
        read_map,
        read_siblings,
        topic_exists,
    )
except ImportError:
    try:
        from .state import (
            INFERENCE_TYPES,
            STRENGTHS,
            STRENGTH_RANK,
            list_branches,
            read_claims,
            read_map,
            read_siblings,
            topic_exists,
        )
    except ImportError:  # loaded flat inside the Hermes plugin directory
        from state import (  # type: ignore[no-redef]
            INFERENCE_TYPES,
            STRENGTHS,
            STRENGTH_RANK,
            list_branches,
            read_claims,
            read_map,
            read_siblings,
            topic_exists,
        )

# Wording that presents a hypothesis or an inference as settled fact.
OVERCLAIM_PATTERNS = (
    (re.compile(r"(?<!未)(已|均|皆)证实"), "已证实"),
    (re.compile(r"(?<!尚未)(确凿|明确)证明"), "确凿证明"),
    # "Proves X is separate from Y" - the claim that a distinction was
    # established when the evidence only left it open. The verb varies
    # by field, so the pattern is the verb plus a separation word.
    (
        re.compile(
            r"(证明|证实|确证)[了过]?[^。；;]{0,12}"
            r"(解耦|分离|独立|无关|因果上无关)"
        ),
        "证明了…的分离",
    ),
    (re.compile(r"充分(说明|表明)"), "充分说明"),
    (re.compile(r"无可争议"), "无可争议"),
    (re.compile(r"彻底解决"), "彻底解决"),
)

# Markers that a single claim bundles an observation with a conclusion drawn
# from it. A claim like "X was found in 3 studies, therefore Y" is two claims
# wearing one coat, and collapsing them is exactly the failure the ledger
# exists to catch.
_CONCLUSION_MARKERS = (
    "因此", "所以", "由此", "故而", "可见", "也就是说", "换言之", "据此",
    "这提示", "这表明", "这说明", "这意味着", "说明", "表明",
    "therefore", "thus", "hence", "consequently", "as a result",
    "implying", "meaning that", "so that", ", so ",
    "cannot be", "can not be", "cannot", "is not", "isn't", "must be",
    "implies", "suggests that", "indicates that", "demonstrates that",
    "points to", "reflects",
)

# A source field that looks like a real citation rather than a placeholder.
_SOURCE_OK = re.compile(r"(PMID|DOI|10\.\d{4,}|arxiv|et al\.?|等,|卷|页|\[\d+\])", re.I)

# A literature-library reference: S1, S12, or S3:some/free text. The
# ledger's own library is a legitimate source, and it is the only one
# that works when there is no bibliography service at all.
_LIB_SOURCE_OK = re.compile(r"^S\d+\b")

_PLACEHOLDER = re.compile(r"^(待补|tbd|todo|n/?a|unknown|xxx|-|\.\.\.|pending)$", re.I)


def _norm_premises(raw: str) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[,;\s]+", raw.strip())
    return [p for p in parts if p]


# --------------------------------------------------------------------------
# premises that live in another topic's ledger
# --------------------------------------------------------------------------
#
# A parent topic's judgement rests on what its branches found, and until now
# it had no way to say so: every premise had to exist in the citing topic's
# own ledger, so the honest move was to copy the branch's observation up -
# which is how a review ends up with three copies of one finding and no
# record of which route produced it.
#
# A reference is `topic:L12` (or `topic/L12`). Topic names cannot contain
# either separator, so the parse is unambiguous. Only topics in the same
# family may be cited: the citing topic's parent, its children, its siblings.
# Citing an unrelated topic is refused rather than merely noted, because a
# review that imports a stranger's conclusion looks exactly like a review
# that reasoned to it.
_PREMISE_TOPIC_SEP = re.compile(r"[:/]")


def _split_ref(ref: str) -> tuple[str, str]:
    """('other-topic:L3') -> ('other-topic', 'L3'); ('L3') -> ('', 'L3')."""
    text = str(ref or "").strip()
    hit = _PREMISE_TOPIC_SEP.search(text)
    if not hit:
        return "", text
    return text[: hit.start()].strip(), text[hit.end():].strip()


def _family(topic: str) -> set[str]:
    """The topics this one is allowed to cite a premise from."""
    out: set[str] = {topic}
    parent = str(read_map(topic).get("parent") or "").strip()
    if parent:
        out.add(parent)
    for row in list_branches(topic):
        child = str(row.get("child") or "").strip()
        if child:
            out.add(child)
    for row in read_siblings(topic):
        name = str(row.get("name") or "").strip()
        if name:
            out.add(name)
    return out


def resolve_premise(
    topic: str,
    ref: str,
    *,
    cache: dict[str, list[dict[str, str]]] | None = None,
    family: set[str] | None = None,
) -> tuple[dict[str, str] | None, str, str]:
    """Resolve one premise reference.

    Returns (claim, source_topic, problem). `problem` is "" when the premise
    resolved, and a short code otherwise:

        foreign_not_related   cites a topic outside this topic's family
        unknown_topic         the referenced topic does not exist
        missing               the id is not in the referenced ledger

    The success case returns "" and not "missing": the code is about what
    went wrong, and a caller that asks "did it resolve" must be able to tell
    "resolved" from "failed" without inspecting the claim.
    """
    cache = cache if cache is not None else {}
    src, cid = _split_ref(ref)
    if not cid:
        return None, "", "missing"
    if not src:
        rows = cache.get(topic)
        if rows is None:
            rows = read_claims(topic)
            cache[topic] = rows
        hit = next((c for c in rows if str(c.get("id") or "") == cid), None)
        return hit, topic, ("" if hit is not None else "missing")
    if family is None:
        family = _family(topic)
    if src not in family:
        return None, src, "foreign_not_related"
    if src != topic and not topic_exists(src):
        return None, src, "unknown_topic"
    rows = cache.get(src)
    if rows is None:
        rows = read_claims(src)
        cache[src] = rows
    hit = next((c for c in rows if str(c.get("id") or "") == cid), None)
    return hit, src, ("" if hit is not None else "missing")


def _find_bundled_conclusion(content: str) -> tuple[str, str] | None:
    """Return (observation_part, conclusion_part) if one claim carries both.

    The split point is a clause boundary followed by a conclusion marker. The
    observation part must be substantial (20+ chars) and the conclusion part
    must be substantial (10+ chars) — otherwise this is a single claim that
    merely happens to contain the word "shows".
    """
    low = content.lower()
    best: tuple[int, str, str] | None = None
    for marker in _CONCLUSION_MARKERS:
        start = 0
        while True:
            i = low.find(marker, start)
            if i < 0:
                break
            start = i + 1
            # Prefer a marker right after a clause boundary.
            head = content[:i].rstrip(" ,;:，、")
            tail = content[i:].strip()
            # A marker at position 0 has no observation part.
            if len(head) < 20 or len(tail) < 10:
                continue
            # The head must end like a finding, not mid-phrase.
            if not re.search(r"[a-zA-Z0-9\)\u4e00-\u9fff][\)\.\u4e00-\u9fff]?$", head):
                continue
            if best is None or i < best[0]:
                best = (i, head, tail)
    if best is None:
        return None
    return best[1], best[2]


def validate_claims(topic: str) -> dict[str, Any]:
    """Check one topic's ledger. Returns errors, warnings, and a summary."""
    claims = read_claims(topic)
    by_id = {c.get("id", ""): c for c in claims}
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    # Ledgers of other topics, read at most once per validation, and the
    # family those topics have to belong to.
    _premise_cache: dict[str, list[dict[str, str]]] = {topic: claims}
    _family_topics: set[str] = _family(topic)
    cross: list[dict[str, str]] = []

    def err(cid: str, code: str, msg: str) -> None:
        errors.append({"id": cid, "code": code, "message": msg})

    def warn(cid: str, code: str, msg: str) -> None:
        warnings.append({"id": cid, "code": code, "message": msg})

    seen_ids: set[str] = set()
    for c in claims:
        cid = c.get("id", "")
        kind = (c.get("kind") or "").strip()
        content = (c.get("content") or "").strip()
        premises = _norm_premises(c.get("premises", ""))
        source = (c.get("source") or "").strip()
        strength = (c.get("strength") or "").strip()
        itype = (c.get("inference_type") or "").strip()

        if not cid:
            err(cid, "missing_id", "claim has no id")
        if cid in seen_ids:
            err(cid, "duplicate_id", f"duplicate claim id {cid}")
        seen_ids.add(cid)
        if not content:
            err(cid, "empty_content", "claim has no content")
        if kind not in ("observation", "report", "inference", "hypothesis"):
            err(cid, "bad_kind", f"unknown kind {kind!r}")
            continue

        # --- source obligation -------------------------------------------
        if kind in ("observation", "report"):
            if not source:
                err(cid, "missing_source", f"{kind} requires a source (PMID/DOI/paper)")
            elif _PLACEHOLDER.match(source):
                err(cid, "placeholder_source", f"{kind} source is a placeholder: {source!r}")
            elif not _SOURCE_OK.search(source) and not _LIB_SOURCE_OK.search(source):
                warn(cid, "weak_source", f"source does not look like a citation: {source!r}")

        # --- inference obligations ---------------------------------------
        if kind == "inference":
            if itype not in INFERENCE_TYPES:
                err(cid, "bad_inference_type", f"inference_type must be one of {INFERENCE_TYPES}")
            if not premises:
                err(cid, "no_premises", "inference has no premises: it is an unsupported assertion")
            weakest = None
            for p in premises:
                # `other-topic:L3` is read from that ledger. The row keeps
                # the reference, not a copy of the finding: a copy loses
                # which route produced it, and it stops tracking the
                # branch's own later amendments.
                src, prem_id = _split_ref(p)
                prem, from_topic, problem = resolve_premise(
                    topic, p, cache=_premise_cache, family=_family_topics
                )
                if problem == "foreign_not_related":
                    err(
                        cid,
                        "foreign_premise",
                        f"premise {p} cites 「{from_topic}」, which is not this "
                        f"topic's parent, child or sibling. A judgement may rest "
                        f"on the routes under it and the work around it; a "
                        f"conclusion imported from an unrelated topic is not "
                        f"evidence for this one.",
                    )
                    continue
                if problem == "unknown_topic":
                    err(
                        cid,
                        "unknown_premise_topic",
                        f"premise {p} names 「{from_topic}」, which does not exist",
                    )
                    continue
                if prem is None:
                    where = f"in 「{from_topic}」's ledger" if src else "in this ledger"
                    err(cid, "dangling_premise", f"premise {p} does not exist {where}")
                    continue
                if src:
                    # Make the borrowed premise visible in the report: its
                    # status, its strength and where it came from decide
                    # whether the inference is allowed, and none of those are
                    # in the citing ledger.
                    cross.append({
                        "claim": cid,
                        "premise": p,
                        "topic": from_topic,
                        "status": (prem.get("status") or "").strip(),
                        "strength": (prem.get("strength") or "").strip(),
                        "source": (prem.get("source") or "").strip(),
                    })
                pstatus = (prem.get("status") or "").strip()
                if pstatus not in ("verified", "derived", "established"):
                    err(
                        cid,
                        "unverified_premise",
                        f"premise {p} is {pstatus or 'unknown'!r}: an inference cannot rest on it",
                    )
                pkind = (prem.get("kind") or "").strip()
                if pkind == "hypothesis":
                    warn(
                        cid,
                        "hypothesis_premise",
                        f"premise {p} is a hypothesis, so this inference is itself speculative",
                    )
                pstr = (prem.get("strength") or "").strip()
                if pstr in STRENGTH_RANK:
                    rank = STRENGTH_RANK[pstr]
                    weakest = rank if weakest is None else min(weakest, rank)
            if strength in STRENGTH_RANK and weakest is not None:
                if STRENGTH_RANK[strength] > weakest:
                    err(
                        cid,
                        "overstated",
                        f"strength {strength!r} exceeds weakest premise "
                        f"({[k for k, v in STRENGTH_RANK.items() if v == weakest][0]!r})",
                    )
            if itype == "analogy" and not (c.get("note") or "").strip():
                warn(cid, "unmarked_analogy", "cross-domain analogy should be marked in note")
            if itype == "abductive":
                rivals = [
                    p for p in premises
                    if (
                        resolve_premise(
                            topic, p, cache=_premise_cache, family=_family_topics
                        )[0] or {}
                    ).get("kind") == "hypothesis"
                ]
                if len(rivals) < 2:
                    warn(
                        cid,
                        "single_explanation",
                        "abductive inference should list at least two competing explanations",
                    )

        # --- hypothesis obligations --------------------------------------
        if kind == "hypothesis":
            if (c.get("status") or "").strip() in ("", "verified"):
                err(cid, "hypothesis_status", "hypothesis must stay open, not 'verified'")
            for pat, label in OVERCLAIM_PATTERNS:
                if pat.search(content):
                    err(cid, "overclaim_phrasing", f"hypothesis stated as fact: {label}")
                    break

        # --- generic overclaim check -------------------------------------
        if kind == "inference":
            for pat, label in OVERCLAIM_PATTERNS:
                if pat.search(content):
                    warn(cid, "overclaim_phrasing", f"inference wording claims too much: {label}")
                    break

        # --- bundled observation + conclusion -----------------------------
        if kind in ("observation", "report"):
            split = _find_bundled_conclusion(content)
            if split:
                head, tail = split
                err(
                    cid,
                    "bundled_claim",
                    "this claim bundles an observation with a conclusion. Split it: "
                    f"keep the observation as '{head[:60]}...' and record the conclusion "
                    f"as a separate inference whose premises point at this row. "
                    f"bundled tail: '{tail[:60]}...'",
                )

        # --- generic overclaim check -------------------------------------

    return {
        "topic": topic,
        "total": len(claims),
        "errors": errors,
        "warnings": warnings,
        "cross_premises": cross,
        "ok": not errors,
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"topic={report['topic']}  claims={report['total']}  "
        f"errors={len(report['errors'])}  warnings={len(report['warnings'])}"
    ]
    for e in report["errors"]:
        lines.append(f"  ERROR   {e['id'] or '?':<8} [{e['code']}] {e['message']}")
    for w in report["warnings"]:
        lines.append(f"  WARNING {w['id'] or '?':<8} [{w['code']}] {w['message']}")
    return "\n".join(lines)
