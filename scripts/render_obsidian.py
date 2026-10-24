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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_os import state  # noqa: E402

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


def render_topic(topic: str, out_dir: Path) -> list[Path]:
    m = state.read_map(topic)
    claims = state.read_claims(topic)
    contra = state.read_contradictions(topic)
    written: list[Path] = []

    # ---------------------------------------------------------------- overview
    hyp = [h for h in (m.get("hypotheses") or []) if str(h).strip()]
    open_q = [q for q in (m.get("open_questions") or []) if str(q).strip()]

    lines = [f"# {topic}", ""]
    if m.get("core_question"):
        lines += ["## 核心问题", "", m["core_question"], ""]
    if hyp:
        lines += ["## 竞争解释", ""]
        lines += [f"- {h}" for h in hyp]
        lines.append("")
    if open_q:
        lines += ["## 尚未解决的问题", ""]
        lines += [f"- {q}" for q in open_q]
        lines.append("")
    items = (contra or {}).get("items") or []
    if items:
        lines += ["## 关键矛盾", ""]
        for it in items:
            title = it.get("title") or it.get("question") or ""
            if title:
                lines.append(f"- {title}")
        lines.append("")
    n_pending = sum(1 for c in claims if (c.get("status") or "") not in ("verified", "derived", "established"))
    lines += [
        "## 当前状态",
        "",
        f"- 账本 {len(claims)} 条，其中 {n_pending} 条待核",
        f"- 竞争解释 {len(hyp)} 个",
        "",
    ]
    p = out_dir / "00-当前认知.md"
    _w(p, "\n".join(lines))
    written.append(p)

    # ---------------------------------------------------------------- ledger
    lrows = ["| ID | 类型 | 强度 | 状态 | 内容 | 前提 | 来源 |", "|---|---|---|---|---|---|---|"]
    for c in claims:
        lrows.append(
            "| {id} | {kind} | {strength} | {status} | {content} | {prem} | {src} |".format(
                id=c.get("id", ""),
                kind=KIND_LABEL.get(c.get("kind", ""), c.get("kind", "")),
                strength=STRENGTH_LABEL.get(c.get("strength", ""), c.get("strength", "")),
                status=STATUS_LABEL.get(c.get("status", ""), c.get("status", "")),
                content=(c.get("content", "") or "").replace("|", "/").replace("\n", " "),
                prem=(c.get("premises", "") or "").replace("|", "/"),
                src=(c.get("source", "") or "").replace("|", "/"),
            )
        )
    p = out_dir / "01-证据账本.md"
    _w(p, f"# 证据账本 — {topic}\n\n" + "\n".join(lrows) + "\n")
    written.append(p)

    # ---------------------------------------------------------------- decisions
    dl = state.topic_dir(topic) / "decision-log.md"
    if dl.exists():
        p = out_dir / "02-决策与转向.md"
        _w(p, dl.read_text(encoding="utf-8"))
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
