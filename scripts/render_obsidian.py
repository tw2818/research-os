"""Render the Research OS state into Obsidian-ready markdown.

The state tree is machine-readable and meant to be diffed. The vault is meant
to be read. This script is the one-way bridge between them:

    state (JSON/TSV)  ->  vault (prose + tables)

It never writes into the state tree, so a bad render can never corrupt the
ledger. Run it after meaningful work:

    python3 scripts/render_obsidian.py --topic Sleep-Memory
    python3 scripts/render_obsidian.py --all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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


def _vault_dir(cfg: dict) -> Path:
    vault = Path(cfg.get("obsidian_vault", "")).expanduser()
    sub = cfg.get("obsidian_subdir", "ResearchOS")
    return vault / sub if sub else vault


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _title(topic: str, m: dict) -> str:
    """A heading a person would want to read, not a slug."""
    q = str(m.get("core_question") or "").strip()
    if q:
        first = q.split("？")[0].split("?")[0].strip()
        if 8 <= len(first) <= 60:
            return first
    return topic


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


def render_topic(topic: str, out_dir: Path) -> list[Path]:
    m = state.read_map(topic)
    claims = state.read_claims(topic)
    contra = state.read_contradictions(topic)
    written: list[Path] = []
    title = _title(topic, m)
    by_kind = _by_kind(claims)

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
            f"还没记。上面有 {len(hyp)} 个竞争解释，但没有写明什么证据能区分它们——"
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

    p = out_dir / "00-当前认知.md"
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
    p = out_dir / "01-证据账本.md"
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
    p = out_dir / "02-决策与转向.md"
    _w(p, body)
    written.append(p)

    return written


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render Research OS state into the Obsidian vault")
    p.add_argument("--topic", help="topic to render")
    p.add_argument("--all", action="store_true", help="render every topic")
    p.add_argument("--out", help="output directory (default: <vault>/<subdir>/<topic>)")
    args = p.parse_args(argv)

    cfg = state.load_config()
    if not cfg.get("obsidian_vault"):
        print("no obsidian_vault configured; run: osctl.py init --vault <path>")
        return 1
    if not cfg.get("obsidian_enabled", True):
        print("obsidian rendering is disabled in config")
        return 0

    base = Path(args.out).expanduser() if args.out else _vault_dir(cfg)
    topics = (
        [t["name"] for t in state.list_topics()]
        if args.all or not args.topic
        else [args.topic]
    )
    if not topics:
        print("no topics")
        return 0

    for topic in topics:
        out_dir = base / topic if not args.out else base
        files = render_topic(topic, out_dir)
        print(f"{topic}: " + ", ".join(str(f) for f in files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
