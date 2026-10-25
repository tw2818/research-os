"""Render the Research OS state into Obsidian-ready markdown.

The state tree is machine-readable and meant to be diffed. The notes are
meant to be read. This script is the one-way bridge between them:

    state (JSON/TSV)  ->  notes (prose + tables)

It never writes into the state tree, so a bad render can never corrupt the
ledger. Run it after meaningful work:

    python3 scripts/render_obsidian.py --topic Sleep-Memory
    python3 scripts/render_obsidian.py --all
    python3 scripts/render_obsidian.py --all --plain
    python3 scripts/render_obsidian.py --topic Sleep-Memory --map-only

Three things this script does, and the reasons for them.

**A topic map.** The ledger page answers "what has been recorded"; it does
not answer "what does this project know". The map (`00-课题地图.md`) is one
page per topic that says what we are studying, where we stand, what is
left, and what the judgment rests on - in prose, because a person opening a
topic wants the state of the research, not a count of the rows.

**One content, two syntaxes.** The Obsidian version links notes with
[[...]] and uses callouts; the plain version is ordinary markdown that
reads the same in any editor. Both come out of the same function, so they
cannot drift apart - only the link syntax differs.

**The vault is optional.** A reader without Obsidian gets the same notes in
a local directory instead of an error. A renderer that refuses to render is
a renderer nobody uses. Set `"require_vault": true` in config.json to get
the old strict behaviour back.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Same three layouts as osctl.py: the repo, and the installed state tree
# where the modules sit flat in the plugin directory.
_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE.parent, _HERE.parent.parent):
    if (_candidate / "research_os" / "__init__.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
else:
    for _candidate in (_HERE, _HERE.parent, _HERE.parent.parent):
        if (_candidate / "state.py").is_file():
            sys.path.insert(0, str(_candidate))
            break

try:
    from research_os import state  # noqa: E402
except ImportError:  # installed layout: the modules are flat
    import state  # type: ignore[no-redef]  # noqa: E402

# The protocol module is optional here. protocol.py is edited on its own
# schedule, and a renderer that dies because it is mid-edit is a renderer
# nobody uses. The phase names are a label, not a dependency: a phase the
# renderer has never heard of falls back to its own key.
try:
    from research_os import protocol as _protocol  # noqa: E402
except Exception:  # pragma: no cover - flat layout, or a broken import
    try:
        import protocol as _protocol  # type: ignore[no-redef]  # noqa: E402
    except Exception:
        _protocol = None

KIND_LABEL = {
    "observation": "观察",
    "report": "转述",
    "inference": "推论",
    "hypothesis": "假设",
}
STRENGTH_LABEL = {
    "descriptive": "描述",
    "suggestive": "提示",
    "strong": "较强",
    "established": "确立",
}
STATUS_LABEL = {
    "pending": "待核",
    "verified": "已核",
    "derived": "已推导",
    "open": "待检验",
    "contradicted": "有反证",
    "rejected": "已否定",
}
# The competing explanations carry a status of their own, separate from the
# claim statuses. An unlabelled status shows its own key rather than
# disappearing.
CONTRA_STATUS_LABEL = {
    "open": "还在竞争",
    "leading": "暂时领先",
    "best": "当前最佳",
    "contested": "受质疑",
    "weakening": "在走弱",
    "challenged": "被挑战",
    "refuted": "已被证伪",
    "rejected": "已被否定",
    "killed": "已被排除",
    "retired": "已搁置",
    "resolved": "已收口",
    "settled": "已收口",
}
# The phases this renderer knows about. protocol.py is the authority, but a
# copy here means an unknown phase name still renders as something a reader
# can act on.
_FALLBACK_PHASE_NAMES = {
    "scoping": "界定问题",
    "evidence": "搜集证据",
    "weighing": "权衡解释",
    "closing": "收束结论",
    "writing": "成文表达",
}
_STRENGTH_LADDER = ("descriptive", "suggestive", "strong", "established")
_SETTLED_STATUSES = ("verified", "derived", "established")

# The file names. The map comes first because it is the page a person
# opens; the other three are the record it points at.
MAP_FILE = "00-课题地图.md"
VIEW_FILE = "00-当前认知.md"
LEDGER_FILE = "01-证据账本.md"
DECISIONS_FILE = "02-决策与转向.md"
NOTES_DIRNAME = "notes"

_CLAIM_ID_RE = re.compile(r"\bL\d+\b")


def _cited_order(*texts: str) -> list[str]:
    """Claim ids in the order the text mentions them.

    The order a judgment tells its evidence in is the order of importance
    the writer put in it. Sorting the key claims by id instead would put
    the crux at the bottom of the list, under the rows it rests on.
    """
    out: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for m in _CLAIM_ID_RE.finditer(text or ""):
            cid = m.group(0)
            if cid not in seen:
                seen.add(cid)
                out.append(cid)
    return out


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _vault_dir(cfg: dict) -> Path:
    vault = Path(cfg.get("obsidian_vault", "")).expanduser()
    sub = cfg.get("obsidian_subdir", "ResearchOS")
    return vault / sub if sub else vault


def _notes_dir(cfg: dict) -> Path:
    """Where the notes go when there is no vault to put them in.

    The default lives beside the state tree rather than inside it: the
    notes are a rendering, and a rendering that sits next to the record it
    came from is easy to find and impossible to mistake for the record.
    """
    configured = str(cfg.get("notes_dir") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(getattr(state, "HOME", Path.home() / ".hermes" / "research-os")) / NOTES_DIRNAME


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _safe(call: Any, default: Any) -> Any:
    """A read that must not take the render down with it.

    The state module is edited independently of this script. A missing
    field is a smaller problem than a crash after the work is done.
    """
    try:
        return call()
    except Exception:
        return default


def _sent(text: Any) -> str:
    """A paragraph, ended properly."""
    t = " ".join(str(text or "").split())
    if not t:
        return ""
    if t[-1] in "。！？!?…":
        return t
    return t + "。"


def _fmt_ts(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _phase_names() -> dict[str, str]:
    names = dict(_FALLBACK_PHASE_NAMES)
    phases = getattr(_protocol, "PHASES", None) if _protocol is not None else None
    if isinstance(phases, dict):
        for key, entry in phases.items():
            if isinstance(entry, dict):
                name = str(entry.get("name") or "").strip()
                if name:
                    names[str(key)] = name
    return names


def _phase_label(phase: Any) -> str:
    """The Chinese name of a phase, with the key for traceability.

    An unknown phase shows its own key. The phase list grows - a project
    that has to diverge gets its own phase - and a KeyError here would take
    the whole render down over a label.
    """
    key = str(phase or "").strip()
    if not key:
        return ""
    name = _phase_names().get(key)
    if name:
        return f"{name}（{key}）"
    return f"{key}（未收录的阶段名）"


def _note_link(obsidian: bool, stem: str) -> str:
    """A link to a sibling note.

    Obsidian gets [[stem]]; a plain reader gets a relative markdown link,
    which every editor understands. Same destination, different syntax.
    """
    return f"[[{stem}]]" if obsidian else f"[{stem}]({stem}.md)"


def _callout(obsidian: bool, kind: str, title: str, body: list[str]) -> list[str]:
    """A quoted aside.

    Obsidian renders this as a callout; a plain reader gets a blockquote
    with a bold title. The words are the same either way.
    """
    head = f"> [!{kind}] {title}" if obsidian else f"> **{title}**"
    out = [head, ">"]
    for b in body:
        for ln in str(b).splitlines() or [""]:
            out.append(f"> {ln}" if ln.strip() else ">")
    return out


def _by_kind(claims: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in claims:
        out.setdefault(c.get("kind") or "other", []).append(c)
    return out


def _source_line(c: dict) -> str:
    bits = []
    src = (c.get("source") or "").strip()
    if src:
        bits.append(src)
    prem = (c.get("premises") or "").strip()
    if prem:
        bits.append(f"前提 {prem}")
    if not bits:
        return ""
    return " · ".join(bits)


def _library_index(library: list[dict]) -> dict[str, dict]:
    """The library, by id and by locator."""
    idx: dict[str, dict] = {}
    for it in library or []:
        if not isinstance(it, dict):
            continue
        for key in (it.get("id"), it.get("locator")):
            k = str(key or "").strip()
            if k and k not in idx:
                idx[k] = it
    return idx


def _cite(idx: dict[str, dict], src: str) -> str:
    """A source, spelled out when the library knows it.

    A claim whose source is a bare library id ("S3") is unreadable on its
    own. The library entry behind it carries the title and where it lives,
    and that is what the reader needs to follow the claim back.
    """
    s = str(src or "").strip()
    if not s:
        return ""
    hit = idx.get(s)
    if not hit:
        return s
    bits = [str(hit.get("id") or s)]
    for field in ("title", "doi"):
        val = str(hit.get(field) or "").strip()
        if val:
            bits.append(val)
    kind = str(hit.get("kind") or "").strip()
    locator = str(hit.get("locator") or "").strip()
    if kind and kind != "ref" and locator:
        bits.append(f"[{kind}:{locator}]")
    return " ".join(b for b in bits if b)


def _bigrams(text: Any) -> set[str]:
    t = "".join(ch for ch in str(text or "") if not ch.isspace())
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _chars(text: Any) -> set[str]:
    """The letters of a string, punctuation dropped."""
    return {
        ch for ch in str(text or "")
        if ch.isalnum() or "一" <= ch <= "鿿"
    }


def _match_hypotheses(hypotheses: list[str], items: list[dict]) -> dict[str, dict | None]:
    """Which competing explanation has a recorded way to tell it apart.

    The hypotheses in the research map and the competing explanations in
    contradictions.json are written by hand and rarely use the same words:
    the map says "黏膜血流/微循环障碍（休克、脏器低灌注）主导" and the
    contradictions file says "the perfusion hypothesis is the dominant one".
    Equality would find nothing, and a map that claims a separator is
    missing when one is on file is worse than no map.

    So the match is by character overlap - two-character shingles first,
    single characters as a second signal for the pairs that word their
    hypothesis differently - and the best pairings are taken globally
    rather than one hypothesis at a time, so a confident match cannot be
    stolen by a weaker one.

    A hypothesis with no match is one nobody has worked out how to test.
    That is exactly what a reader of a research map most needs to know.
    """
    scored: list[tuple[float, int, int]] = []
    for hi, h in enumerate(hypotheses):
        hb, hc = _bigrams(h), _chars(h)
        if not hb or not hc:
            continue
        for ii, it in enumerate(items):
            title = str(it.get("title") or "")
            tb, tc = _bigrams(title), _chars(title)
            if not tb or not tc:
                continue
            shingle = len(hb & tb) / len(hb)
            letter = len(hc & tc) / len(hc)
            score = max(shingle, 0.9 * letter)
            if score >= 0.35:
                scored.append((score, hi, ii))
    scored.sort(reverse=True)
    out: dict[str, dict | None] = {h: None for h in hypotheses}
    taken_h: set[int] = set()
    taken_i: set[int] = set()
    for _score, hi, ii in scored:
        if hi in taken_h or ii in taken_i:
            continue
        taken_h.add(hi)
        taken_i.add(ii)
        out[hypotheses[hi]] = items[ii]
    return out


def _title(topic: str, m: dict) -> str:
    """A heading a person would want to read, not a slug."""
    q = str(m.get("core_question") or "").strip()
    if q:
        first = q.split("？")[0].split("?")[0].strip()
        if 8 <= len(first) <= 60:
            return first
    return topic


def _question_text(q: Any) -> str:
    if isinstance(q, dict):
        return str(q.get("question") or q.get("q") or q.get("text") or "").strip()
    return str(q or "").strip()


# --------------------------------------------------------------------------
# the topic map
# --------------------------------------------------------------------------
def _render_map(topic: str, data: dict, *, obsidian: bool) -> str:
    """The one page that says what this topic knows.

    Four questions, in the order a reader asks them: what are we studying,
    where do we stand, what is left, and what does the judgment rest on.
    The answers are prose. A person opening a topic is not asking for a
    count of the ledger rows; they are asking what the project has come to
    believe. Every fact in here still carries the claim id it came from, so
    nothing here is unfalsifiable - it can be walked back to the record.

    `obsidian` decides the link syntax and nothing else.
    """
    m = data.get("map") or {}
    claims = data.get("claims") or []
    contra = data.get("contradictions") or {}
    review = data.get("review") or {}
    library = data.get("library") or []

    title = _title(topic, m)
    core = str(m.get("core_question") or "").strip()
    goal = str(m.get("goal") or "").strip()
    phase = str(m.get("phase") or "").strip()
    task = str(m.get("current_task") or "").strip()
    narrative = str(m.get("narrative") or "").strip()
    plan = str(m.get("plan") or "").strip()
    lib_idx = _library_index(library)
    by_kind = _by_kind(claims)

    lines: list[str] = [f"# {title}", ""]
    if core and core != title and not core.startswith(title):
        lines += [f"> {core}", ""]
    tags = [str(t).strip() for t in (m.get("tags") or []) if str(t).strip()]
    meta = [f"课题标识 `{topic}`"]
    if tags:
        meta.append("标签 " + "、".join(tags))
    updated = _fmt_ts(m.get("updated_at"))
    if updated:
        meta.append(f"状态截至 {updated}")
    lines += [" · ".join(meta), ""]

    # ------------------------------------------------- 1. what we study
    lines += ["## 我们在研究什么", ""]
    if core:
        lines += [f"这个课题要回答的是：{core}", ""]
    else:
        lines += [
            "这个课题还没有写下核心问题。一个没有问题的课题只能攒材料，"
            "攒不出结论——先把它问准。",
            "",
        ]

    q_hist = [h for h in (m.get("question_history") or []) if isinstance(h, dict)]
    if q_hist:
        lines += ["### 这个问题被改过", ""]
        for h in q_hist:
            was = str(h.get("was") or "").strip()
            now = str(h.get("now") or "").strip()
            why = str(h.get("why") or "").strip()
            at = _fmt_ts(h.get("at"))
            parts = []
            if was:
                parts.append(f"最初的问法是「{was}」")
            if now:
                parts.append(f"现在的问法是「{now}」")
            if why:
                parts.append(f"改的理由是：{why}")
            if at:
                parts.append(f"改写于 {at}。")
            lines.append("".join(_sent(p) for p in parts) or "（有一条改写记录，但没有内容。）")
            lines.append("")
        lines += [
            "问题被改过之后，在旧问法下收集的材料不能算作对新问法的答案——"
            "它们回答的是另一个问题。",
            "",
        ]

    # --------------------------------------------------- 2. where we are
    lines += ["## 现在站在哪", ""]
    standing: list[str] = []
    plabel = _phase_label(phase)
    if plabel:
        standing.append(f"课题处在「{plabel}」阶段")
    if goal:
        standing.append(f"目标是：{goal}")
    if task:
        standing.append(f"眼下正在做的是：{task}")
    if standing:
        lines += ["".join(_sent(s) for s in standing), ""]

    ph = [p for p in (m.get("phase_history") or []) if isinstance(p, dict)]
    if ph:
        chain = []
        for p in ph:
            to = str(p.get("to") or "").strip()
            if to:
                chain.append(_phase_names().get(to, to))
        if chain:
            lines += ["这个课题走过的阶段：" + " → ".join(chain) + "。", ""]

    explorations = [e for e in (m.get("explorations") or []) if isinstance(e, dict)]
    if explorations:
        bits = [str(e.get("what") or "").strip() for e in explorations]
        bits = [b for b in bits if b]
        if bits:
            lines += [
                "分出去看过、但还没走深的方向：" + "；".join(bits[-3:]) + "。",
                "",
            ]

    lines += ["### 当前的判断", ""]
    if narrative:
        for para in narrative.split("\n\n"):
            p = para.strip()
            if p:
                lines += [p, ""]
    else:
        lines += [
            "这个课题还没有写下当前的判断。账本里可能已经有材料，"
            "但还没有人把它们合成一个说法——下一次读这份记录的人会以为"
            "材料本身就是结论。",
            "",
        ]

    reviews = int(review.get("reviews") or 0)
    verdict = str(review.get("last_verdict") or "").strip()
    reason = str(review.get("last_reason") or "").strip()
    paused = bool(review.get("paused"))
    map_reviews = [r for r in (m.get("reviews") or []) if isinstance(r, dict)]
    if reviews or verdict or map_reviews:
        lines += ["### 全局审查", ""]
        if paused:
            lines += [
                f"这个课题现在是被审查按住的状态：工作被判定离开了核心问题，"
                f"检索是停着的。已做过 {reviews} 次审查，最近一次判定 "
                f"`{verdict}`。",
                "",
            ]
        elif verdict:
            lines += [
                f"已做过 {reviews} 次全局审查，最近一次判定是 `{verdict}`。",
                "",
            ]
        if map_reviews:
            chain = " → ".join(
                f"{str(r.get('verdict') or '?').strip()}"
                + (f"（{str(r.get('phase') or '').strip()}）" if str(r.get("phase") or "").strip() else "")
                for r in map_reviews
            )
            lines += [f"审查记录：{chain}。", ""]
        if reason:
            lines += _callout(obsidian, "note", "上次审查说", [reason])
            lines.append("")

    # ---------------------------------------------------- 3. what is left
    lines += ["## 还剩什么没做", ""]
    if plan:
        lines += [f"下一步的计划是：{_sent(plan)}", ""]

    resolved: dict[str, str] = {}
    for q in (m.get("resolved_questions") or []):
        t = _question_text(q)
        if not t:
            continue
        ans = str(q.get("answer") or "").strip() if isinstance(q, dict) else ""
        resolved[t] = ans
    open_list: list[str] = []
    misfiled = 0
    resolved_answers = {a for a in resolved.values() if a}
    for q in (m.get("open_questions") or []):
        t = _question_text(q)
        if not t or t in resolved:
            continue
        if t in resolved_answers:
            # The answer to a closed question was filed as an open
            # question. It is not a piece of work left to do, and leaving
            # it in the to-do list tells the reader it is.
            misfiled += 1
            continue
        open_list.append(t)

    if open_list:
        lines += [f"还没回答的问题有 {len(open_list)} 个：", ""]
        for q in open_list:
            lines += [f"- {q}", ""]
    else:
        lines += ["没有挂着未解的问题。", ""]

    if resolved:
        lines += ["已经关掉的问题（这些不用再管，答案记在这里）：", ""]
        for t, ans in resolved.items():
            lines += [f"- {t}"]
            if ans:
                lines += [f"  {ans}"]
            lines.append("")
        if misfiled:
            lines += [
                f"（有 {misfiled} 条挂在未解问题里的其实是上面这个答案，"
                f"已经归到已解，不再算作待办。）",
                "",
            ]

    hyp_items = [str(h).strip() for h in (m.get("hypotheses") or []) if str(h).strip()]
    contra_items = [i for i in (contra.get("items") or []) if isinstance(i, dict)]
    if hyp_items:
        matched = _match_hypotheses(hyp_items, contra_items)
        lines += [f"桌上摆着 {len(hyp_items)} 个竞争解释：", ""]
        for h in hyp_items:
            if matched.get(h) is not None:
                lines += [f"- {h}——已经写明了什么证据能分开它（见下）。", ""]
            else:
                lines += [
                    f"- {h}——**还没有写明什么证据能分开它**，"
                    f"这是这个课题欠着的一件工作。",
                    "",
                ]

    hyp_claims = [
        c for c in claims
        if (c.get("kind") or "") == "hypothesis"
        and (c.get("status") or "") not in _SETTLED_STATUSES
    ]
    if hyp_claims:
        lines += ["账本里还留着没被检验的假设：", ""]
        for c in hyp_claims:
            content = (c.get("content") or "").strip()
            note = (c.get("note") or "").strip()
            lines += [f"- **{c.get('id', '')}**（{STATUS_LABEL.get(c.get('status', ''), c.get('status', ''))}）{content}"]
            if note:
                lines += [f"  {note}"]
            lines.append("")
    if not open_list and not resolved and not hyp_items and not hyp_claims:
        lines += [
            "还没有记下任何待办：没有未解的问题，没有关掉的问题，"
            "也没有列出的竞争解释。",
            "",
        ]

    # --------------------------------------------- 4. what it rests on
    lines += ["## 凭什么这么判断", ""]
    if claims:
        parts = []
        for kind in ("observation", "report", "inference", "hypothesis"):
            n = len(by_kind.get(kind, []))
            if n:
                parts.append(f"{KIND_LABEL.get(kind, kind)} {n}")
        lines += [f"账本里一共 {len(claims)} 条断言：" + "、".join(parts) + "。"]
        sdist: dict[str, int] = {}
        for c in claims:
            s = (c.get("strength") or "").strip()
            if s:
                sdist[s] = sdist.get(s, 0) + 1
        ladder = [k for k in _STRENGTH_LADDER if sdist.get(k)]
        ladder += [k for k in sorted(sdist) if k not in _STRENGTH_LADDER]
        sbits = [f"{STRENGTH_LABEL.get(k, k)} {sdist[k]}" for k in ladder]
        if sbits:
            strength_sentence = "强度上，" + "、".join(sbits) + "。"
            if not sdist.get("established"):
                strength_sentence += "没有一条记为「确立」——这个课题的结论都还带着保留。"
            lines += [strength_sentence]
        pending = [c for c in claims if (c.get("status") or "") not in _SETTLED_STATUSES]
        if pending:
            pids = "、".join(str(c.get("id", "")) for c in pending)
            lines += [
                f"其中 {len(pending)} 条还只是线索，没核到原文或没被推导："
                f"{pids}。",
                "",
            ]
        else:
            lines += ["每一条都已经核到原文或被推导过。", ""]
    else:
        lines += [
            "账本还是空的。下面这段判断目前没有账本依据——"
            "不是「证据弱」，是「没有证据」。",
            "",
        ]

    # The claims the current judgment stands on. The narrative names them
    # (L72, L73, ...), so those are the ones, in the order the narrative
    # mentions them; when the narrative names none, the inferences are the
    # fallback - they are the agent's own reasoning, and they are what a
    # judgment rests on.
    order = _cited_order(narrative, task, plan, goal)
    cited = set(order)
    key = [c for c in claims if str(c.get("id") or "") in cited]
    key.sort(key=lambda c: order.index(str(c.get("id") or "")))
    if not key:
        rank = {s: i for i, s in enumerate(_STRENGTH_LADDER)}
        inferences = [c for c in claims if (c.get("kind") or "") == "inference"]
        inferences.sort(key=lambda c: rank.get((c.get("strength") or "").strip(), -1), reverse=True)
        key = inferences
    if key:
        lines += ["### 撑着当前判断的几条", ""]
        shown = key[:8]
        for c in shown:
            kind_label = KIND_LABEL.get(c.get("kind") or "", c.get("kind") or "")
            strength = STRENGTH_LABEL.get((c.get("strength") or "").strip(), "")
            tag = " · ".join(x for x in (kind_label, strength) if x)
            content = (c.get("content") or "").strip()
            lines += [f"- **{c.get('id', '')}**" + (f"（{tag}）" if tag else "") + f" {content}"]
            src = _cite(lib_idx, (c.get("source") or "").strip())
            if src:
                lines += [f"  来源：{src}"]
            prem = (c.get("premises") or "").strip()
            if prem:
                lines += [f"  前提：{prem}"]
            lines.append("")
        if len(key) > len(shown):
            rest = "、".join(str(c.get("id", "")) for c in key[len(shown):])
            lines += [f"另有 {len(key) - len(shown)} 条在判断里被提到：{rest}。", ""]
    else:
        lines += [
            "判断没有落到具体的条目上——要么账本是空的，"
            "要么当前的判断是凭空说的。",
            "",
        ]

    if contra_items:
        lines += ["### 竞争解释：现在各自站在哪", ""]
        for it in contra_items:
            ct = str(it.get("title") or it.get("question") or "").strip()
            sep = str(it.get("separator") or "").strip()
            st = str(it.get("status") or "").strip()
            label = CONTRA_STATUS_LABEL.get(st, st)
            head = f"- **{ct}**" if ct else "- （没有写标题的解释）"
            if label:
                head += f"（{label}）"
            lines += [head]
            if sep:
                lines += [f"  分开它需要：{sep}"]
            else:
                lines += [
                    "  **还没写明什么证据能分开它**——"
                    "两个答案都能解释同一批材料时，缺的是一个判据，不是更多材料。",
                ]
            lines.append("")
    else:
        lines += [
            "### 竞争解释",
            "",
            "还没有记下竞争解释。一个课题如果只有一个说法，"
            "通常意味着还没去找那一个。",
            "",
        ]

    # ------------------------------------------------------------- footer
    lines += [
        "---",
        "",
        "配套："
        + " · ".join(
            [
                _note_link(obsidian, LEDGER_FILE[:-3]),
                _note_link(obsidian, DECISIONS_FILE[:-3]),
                _note_link(obsidian, VIEW_FILE[:-3]),
            ]
        ),
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# the ledger pages
# --------------------------------------------------------------------------
def render_topic(
    topic: str,
    out_dir: Path,
    *,
    obsidian: bool = True,
    map_only: bool = False,
) -> list[Path]:
    m = state.read_map(topic)
    claims = state.read_claims(topic)
    contra = state.read_contradictions(topic)
    review = _safe(lambda: state.read_review(topic), {})
    library = _safe(lambda: state.read_library(topic), [])
    written: list[Path] = []
    title = _title(topic, m)
    by_kind = _by_kind(claims)

    # The map comes first: it is the page a person opens.
    p = out_dir / MAP_FILE
    _w(
        p,
        _render_map(
            topic,
            {
                "map": m,
                "claims": claims,
                "contradictions": contra,
                "review": review,
                "library": library,
            },
            obsidian=obsidian,
        ),
    )
    written.append(p)
    if map_only:
        return written

    # ------------------------------------------------------- 00 current view
    # The reader's first question is "what do we currently think", not "what
    # is in the table". So the inferences — the agent's own reasoning — come
    # first, in prose, and the evidence they rest on is pointed at rather
    # than pasted.
    lines = [f"# {title}", ""]
    core = str(m.get("core_question") or "").strip()
    # The title is often the question itself, cut at the question mark. Do not
    # print it twice.
    if core and core != title and not core.startswith(title):
        lines += [f"> {core}", ""]
    if topic != title:
        lines += [f"课题标识：`{topic}`", ""]
    # A pointer to the map, so the reader who opened the ledger knows there
    # is a page that says what the project knows.
    lines += [f"课题地图：{_note_link(obsidian, MAP_FILE[:-3])}", ""]

    hyp = [h for h in (m.get("hypotheses") or []) if str(h).strip()]
    open_q = [q for q in (m.get("open_questions") or []) if str(q).strip()]

    inferences = by_kind.get("inference", [])
    if inferences:
        lines += ["## 目前的判断", ""]
        for c in inferences:
            body = (c.get("content") or "").strip()
            strength = STRENGTH_LABEL.get(c.get("strength", ""), "")
            prem = (c.get("premises") or "").strip()
            itype = (c.get("inference_type") or "").strip()
            tag = " · ".join(x for x in (itype, strength) if x)
            lines.append(f"**{c.get('id','')}** {body}" + (f"  <sub>{tag}</sub>" if tag else ""))
            if prem:
                lines.append(f"  <sub>依据 {prem}</sub>")
            lines.append("")
    elif claims:
        lines += ["## 目前的判断", "", "还没有推论——只有证据，没有从中得出的判断。", ""]

    n_pending = sum(
        1 for c in claims
        if (c.get("status") or "") not in ("verified", "derived", "established")
    )
    counts = []
    for kind, label in (("observation", "观察"), ("report", "转述"),
                        ("inference", "推论"), ("hypothesis", "假设")):
        n = len(by_kind.get(kind, []))
        if n:
            counts.append(f"{label} {n}")
    lines += ["## 证据现状", ""]
    lines.append("、".join(counts) + "。" if counts else "账本还是空的。")
    if n_pending:
        lines.append(f"其中 {n_pending} 条待核——在核到原文之前，它们只是线索。")
    else:
        lines.append("全部已核到原文。")
    lines.append("")

    if hyp:
        lines += ["## 竞争解释", ""]
        lines += [f"- {h}" for h in hyp]
        lines.append("")

    items = (contra or {}).get("items") or []
    if items:
        lines += ["## 关键矛盾", ""]
        for it in items:
            t = it.get("title") or it.get("question") or ""
            if t:
                lines.append(f"- {t}")
        lines.append("")
    elif hyp:
        # Two or more competing explanations with nothing separating them is
        # the state the research map is supposed to prevent. Say it instead
        # of leaving the section out — a missing heading reads as "nothing to
        # report here", which is not the same thing.
        lines += [
            "## 关键矛盾",
            "",
            f"还没记。上面有 {len(hyp)} 个可能答案，但没有写明什么能区分它们——"
            f"这正是这个课题该先回答的问题。",
            "",
        ]

    if open_q:
        lines += ["## 尚未解决的问题", ""]
        lines += [f"- {q}" for q in open_q]
        lines.append("")
    elif inferences:
        lines += [
            "## 尚未解决的问题",
            "",
            "还没记。已经有判断了，但没写下来哪里还悬着——"
            "下一个读这份记录的人会以为这就是全部。",
            "",
        ]

    p = out_dir / VIEW_FILE
    _w(p, "\n".join(lines))
    written.append(p)

    # ---------------------------------------------------------------- ledger
    # Grouped by type instead of one long table. A table of eighteen long
    # sentences is not readable; grouped prose is.
    llines = [f"# 证据账本 — {title}", ""]
    if not claims:
        llines += ["账本还是空的。", ""]
    for kind in ("observation", "report", "inference", "hypothesis"):
        group = by_kind.get(kind, [])
        if not group:
            continue
        llines += [f"## {KIND_LABEL.get(kind, kind)}（{len(group)}）", ""]
        for c in group:
            ident = c.get("id", "")
            strength = STRENGTH_LABEL.get(c.get("strength", ""), "")
            status = STATUS_LABEL.get(c.get("status", ""), "")
            tags = " · ".join(x for x in (strength, status) if x)
            content = (c.get("content") or "").strip()
            llines.append(f"- **{ident}** {content}")
            src = _source_line(c)
            if src:
                llines.append(f"  <sub>{src}</sub>")
            llines.append("")
    p = out_dir / LEDGER_FILE
    _w(p, "\n".join(llines))
    written.append(p)

    # -------------------------------------------------------------- decisions
    dl = state.topic_dir(topic) / "decision-log.md"
    body = dl.read_text(encoding="utf-8") if dl.exists() else ""
    if not body.strip():
        body = (
            f"# 决策与转向 — {title}\n\n"
            "还没有记录决策。\n\n"
            "这一页记的是：做了什么判断、放弃了哪条路、什么情况下会回头。\n"
        )
    p = out_dir / DECISIONS_FILE
    _w(p, body)
    written.append(p)

    return written


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Render Research OS state into the Obsidian vault, or into plain markdown"
    )
    p.add_argument("--topic", help="topic to render")
    p.add_argument("--all", action="store_true", help="render every topic")
    p.add_argument(
        "--out",
        help="output directory (default: <vault>/<subdir>/<topic>, or <home>/notes/<topic> without a vault)",
    )
    p.add_argument(
        "--plain",
        action="store_true",
        help="plain markdown only: no [[links]], no callouts (the destination is unchanged)",
    )
    p.add_argument(
        "--map-only",
        action="store_true",
        help="render only the topic map, not the ledger or the decision log",
    )
    args = p.parse_args(argv)

    cfg = state.load_config()
    vault = str(cfg.get("obsidian_vault") or "").strip()
    enabled = bool(cfg.get("obsidian_enabled", True))
    require_vault = bool(cfg.get("require_vault", False))

    if enabled and vault:
        base = Path(args.out).expanduser() if args.out else _vault_dir(cfg)
        obsidian = not args.plain
        note = f"输出到 vault：{base}"
    else:
        # No vault, or the vault is switched off. Neither is an error: the
        # notes still get written, into a plain directory next to the state
        # tree, and the reader is told where they went.
        if require_vault:
            if not enabled:
                print(
                    "obsidian_enabled is false and require_vault is true; "
                    "run: osctl.py init --vault <path>"
                )
            else:
                print("no obsidian_vault configured; run: osctl.py init --vault <path>")
            return 1
        base = Path(args.out).expanduser() if args.out else _notes_dir(cfg)
        obsidian = False
        if not enabled:
            note = f"obsidian_enabled=false，不写 vault，输出到本地目录：{base}"
        else:
            note = f"没有配 vault，输出到本地目录：{base}"

    topics = (
        [t["name"] for t in state.list_topics()]
        if args.all or not args.topic
        else [args.topic]
    )
    if not topics:
        print("no topics")
        return 0

    print(note)
    for topic in topics:
        out_dir = base / topic if not args.out else base
        files = render_topic(topic, out_dir, obsidian=obsidian, map_only=args.map_only)
        print(f"{topic}: " + ", ".join(str(f) for f in files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
