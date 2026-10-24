"""osctl — command line for the Research OS state.

    python3 scripts/osctl.py init
    python3 scripts/osctl.py topics
    python3 scripts/osctl.py create "Sleep-Memory" --question "..."
    python3 scripts/osctl.py map "Sleep-Memory"
    python3 scripts/osctl.py claim "Sleep-Memory" --kind observation --content "..." --source "PMID 12345"
    python3 scripts/osctl.py validate "Sleep-Memory"
    python3 scripts/osctl.py status
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The CLI runs from three places: the repo (research_os/ is one level up),
# and the installed state tree (the modules sit flat in the plugin
# directory). Find the modules either way, or the CLI is useless the moment
# it is installed.
_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE.parent, _HERE.parent.parent):
    if (_candidate / "research_os" / "__init__.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
else:
    for _candidate in (_HERE.parent.parent, _HERE.parent):
        if (_candidate / "state.py").is_file():
            sys.path.insert(0, str(_candidate))
            break

try:
    from research_os import state  # noqa: E402
    from research_os.validate import format_report, validate_claims  # noqa: E402
except ImportError:  # installed layout: the modules are flat
    import state  # type: ignore[no-redef]  # noqa: E402
    from validate import format_report, validate_claims  # type: ignore[no-redef]  # noqa: E402


def cmd_init(args: argparse.Namespace) -> int:
    for p in (state.HOME, state.SESSIONS_DIR, state.TOPICS_DIR):
        p.mkdir(parents=True, exist_ok=True)
    cfg = state.load_config()
    if args.vault:
        cfg["obsidian_vault"] = args.vault
    if args.subdir:
        cfg["obsidian_subdir"] = args.subdir
    state.save_config(cfg)
    print(f"Research OS home: {state.HOME}")
    print(f"config: {json.dumps(cfg, ensure_ascii=False)}")
    return 0


def cmd_topics(args: argparse.Namespace) -> int:
    topics = state.list_topics()
    if not topics:
        print("(no topics yet)")
        return 0
    for t in topics:
        print(f"\n{t['name']}")
        if t.get("core_question"):
            print(f"  Q: {t['core_question']}")
        for h in t.get("hypotheses") or []:
            print(f"  H: {h}")
        if t.get("tags"):
            print(f"  tags: {', '.join(t['tags'])}")
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    meta = state.create_topic(args.name, args.question, args.hypothesis or [], args.tag or [])
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


def cmd_map(args: argparse.Namespace) -> int:
    print(json.dumps(state.read_map(args.topic), ensure_ascii=False, indent=2))
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    row = state.append_claim(
        args.topic,
        {
            "kind": args.kind,
            "content": args.content,
            "inference_type": args.inference_type or "",
            "strength": args.strength or "",
            "premises": " ".join(args.premise or []),
            "source": args.source or "",
            "status": args.status or "",
            "note": args.note or "",
        },
    )
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0


def cmd_claims(args: argparse.Namespace) -> int:
    rows = state.read_claims(args.topic)
    if not rows:
        print("(empty ledger)")
        return 0
    for r in rows:
        prem = r.get("premises") or ""
        print(f"{r.get('id',''):<6} {r.get('kind',''):<12} {r.get('status',''):<10} {r.get('content','')[:70]}")
        if prem:
            print(f"       premises: {prem}  type={r.get('inference_type','')} strength={r.get('strength','')}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    report = validate_claims(args.topic)
    print(format_report(report))
    return 0 if report["ok"] else 1


def cmd_delete(args: argparse.Namespace) -> int:
    dest = state.delete_topic(args.name, permanent=args.permanent)
    print(f"deleted {args.name}" + (f" -> {dest}" if dest else " (permanent)"))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print(f"home: {state.HOME}")
    print(f"topics: {[t['name'] for t in state.list_topics()]}")
    sess = state.SESSIONS_DIR
    if sess.is_dir():
        for f in sorted(sess.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            print(f"  session {f.stem}: mode={d.get('mode')} topic={d.get('topic','')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="osctl", description="Research OS state CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create the state tree")
    s.add_argument("--vault", help="Obsidian vault path")
    s.add_argument("--subdir", help="subdirectory inside the vault")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("topics", help="list topics")
    s.set_defaults(fn=cmd_topics)

    s = sub.add_parser("create", help="create a topic")
    s.add_argument("name")
    s.add_argument("--question", required=True)
    s.add_argument("--hypothesis", action="append")
    s.add_argument("--tag", action="append")
    s.set_defaults(fn=cmd_create)

    s = sub.add_parser("map", help="show a topic's research map")
    s.add_argument("topic")
    s.set_defaults(fn=cmd_map)

    s = sub.add_parser("claim", help="add a claim to the ledger")
    s.add_argument("topic")
    s.add_argument("--kind", required=True,
                   choices=["observation", "report", "inference", "hypothesis"])
    s.add_argument("--content", required=True)
    s.add_argument("--inference-type",
                   choices=["descriptive", "explanatory", "predictive", "analogy", "abductive"])
    s.add_argument("--strength",
                   choices=["descriptive", "suggestive", "strong", "established"])
    s.add_argument("--premise", action="append")
    s.add_argument("--source")
    s.add_argument("--status")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_claim)

    s = sub.add_parser("claims", help="show a topic's ledger")
    s.add_argument("topic")
    s.set_defaults(fn=cmd_claims)

    s = sub.add_parser("validate", help="check a topic's ledger")
    s.add_argument("topic")
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("delete", help="remove a topic")
    s.add_argument("name")
    s.add_argument("--permanent", action="store_true")
    s.set_defaults(fn=cmd_delete)

    s = sub.add_parser("status", help="show overall state")
    s.set_defaults(fn=cmd_status)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
