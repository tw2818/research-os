"""Research OS state layer.

Owns three things and nothing else:
  1. the project registry (which research topics exist)
  2. per-session mode (is this conversation in research mode, and for which topic)
  3. the per-topic state files (research map, claim ledger, contradictions)

Everything is plain JSON / TSV on disk so a human can read it, git can diff it,
and the Obsidian renderer can turn it into prose without a database.

Layout (default ~/.hermes/research-os, override with RESEARCH_OS_HOME):
    config.json
    projects.json
    sessions/<session_id>.json
    topics/<topic>/research-map.json
    topics/<topic>/claims.tsv
    topics/<topic>/contradictions.json
    topics/<topic>/decision-log.md
    .trash/<topic>-<ts>/
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Iterable

HOME = Path(os.environ.get("RESEARCH_OS_HOME", Path.home() / ".hermes" / "research-os"))
CONFIG_PATH = HOME / "config.json"
PROJECTS_PATH = HOME / "projects.json"
SESSIONS_DIR = HOME / "sessions"
TOPICS_DIR = HOME / "topics"
TRASH_DIR = HOME / ".trash"

CLAIM_KINDS = ("observation", "report", "inference", "hypothesis")
INFERENCE_TYPES = ("descriptive", "explanatory", "predictive", "analogy", "abductive")
STRENGTHS = ("descriptive", "suggestive", "strong", "established")
# An inference may never be stated more strongly than its weakest premise.
STRENGTH_RANK = {s: i for i, s in enumerate(STRENGTHS)}

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\- ]{0,63}$")


def now() -> float:
    return time.time()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data: Any) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "obsidian_vault": "",
    "obsidian_subdir": "ResearchOS",
    "obsidian_enabled": True,
    "inject_protocol": True,
    "max_inject_chars": 900,
}


def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(_read_json(CONFIG_PATH, {}))
    return cfg


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    cfg.update(patch)
    _write_json(CONFIG_PATH, cfg)
    return cfg


# --------------------------------------------------------------------------
# topics
# --------------------------------------------------------------------------


def valid_topic_name(name: str) -> bool:
    return bool(name) and bool(_SAFE_NAME.match(name.strip()))


def topic_dir(topic: str) -> Path:
    return TOPICS_DIR / topic


def list_topics() -> list[dict[str, Any]]:
    """Registry first; fall back to disk so a hand-created dir is still visible."""
    registry = _read_json(PROJECTS_PATH, {})
    known: dict[str, dict[str, Any]] = dict(registry.get("topics", {}))
    if TOPICS_DIR.is_dir():
        for d in sorted(TOPICS_DIR.iterdir()):
            if d.is_dir() and d.name not in known:
                known[d.name] = {
                    "core_question": "",
                    "created_at": d.stat().st_mtime,
                    "discovered": True,
                }
    return [{"name": n, **meta} for n, meta in sorted(known.items())]


def topic_exists(topic: str) -> bool:
    return topic in {t["name"] for t in list_topics()}


def create_topic(
    topic: str,
    core_question: str,
    hypotheses: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
) -> dict[str, Any]:
    if not valid_topic_name(topic):
        raise ValueError(
            f"invalid topic name {topic!r}: use letters, digits, dot, dash, underscore, space (max 64)"
        )
    if not core_question.strip():
        raise ValueError("core_question is required: a topic without a question is not a research topic")
    if topic_exists(topic):
        raise ValueError(f"topic already exists: {topic}")

    d = topic_dir(topic)
    d.mkdir(parents=True, exist_ok=True)

    hyp = [h.strip() for h in (hypotheses or []) if h and h.strip()]
    _write_json(
        d / "research-map.json",
        {
            "topic": topic,
            "core_question": core_question.strip(),
            "hypotheses": hyp,
            "tags": sorted({t.strip() for t in (tags or []) if t and t.strip()}),
            "open_questions": [],
            "updated_at": now(),
        },
    )
    write_claims(topic, [])
    _write_json(d / "contradictions.json", {"topic": topic, "items": [], "updated_at": now()})
    _atomic_write(
        d / "decision-log.md",
        f"# Decision log — {topic}\n\nCore question: {core_question.strip()}\n\n",
    )

    registry = _read_json(PROJECTS_PATH, {"topics": {}})
    registry.setdefault("topics", {})[topic] = {
        "core_question": core_question.strip(),
        "created_at": now(),
        "hypotheses": hyp,
        "tags": sorted({t.strip() for t in (tags or []) if t and t.strip()}),
    }
    _write_json(PROJECTS_PATH, registry)
    return registry["topics"][topic]


def delete_topic(topic: str, permanent: bool = False) -> str:
    if not topic_exists(topic):
        raise ValueError(f"no such topic: {topic}")
    if permanent:
        shutil.rmtree(topic_dir(topic), ignore_errors=True)
        dest = ""
    else:
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        dest = TRASH_DIR / f"{topic}-{int(now())}"
        shutil.move(str(topic_dir(topic)), str(dest))
    registry = _read_json(PROJECTS_PATH, {"topics": {}})
    registry.get("topics", {}).pop(topic, None)
    _write_json(PROJECTS_PATH, registry)
    return str(dest)


def read_map(topic: str) -> dict[str, Any]:
    return _read_json(topic_dir(topic) / "research-map.json", {})


def write_map(topic: str, data: dict[str, Any]) -> None:
    data["updated_at"] = now()
    _write_json(topic_dir(topic) / "research-map.json", data)


def read_contradictions(topic: str) -> dict[str, Any]:
    return _read_json(topic_dir(topic) / "contradictions.json", {"topic": topic, "items": []})


def write_contradictions(topic: str, data: dict[str, Any]) -> None:
    data["updated_at"] = now()
    _write_json(topic_dir(topic) / "contradictions.json", data)


# --------------------------------------------------------------------------
# claim ledger (claims.tsv)
# --------------------------------------------------------------------------

CLAIM_COLUMNS = (
    "id",
    "kind",
    "inference_type",
    "strength",
    "content",
    "premises",
    "source",
    "status",
    "note",
)


def _split_tsv(line: str) -> list[str]:
    return line.rstrip("\n").split("\t")


def read_claims(topic: str) -> list[dict[str, str]]:
    path = topic_dir(topic) / "claims.tsv"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return []
    header = _split_tsv(lines[0])
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = _split_tsv(line)
        parts += [""] * (len(header) - len(parts))
        rows.append({header[i]: parts[i] for i in range(len(header))})
    return rows


def write_claims(topic: str, claims: list[dict[str, str]]) -> None:
    out = ["\t".join(CLAIM_COLUMNS)]
    for c in claims:
        out.append("\t".join(str(c.get(col, "")).replace("\t", " ").replace("\n", " ") for col in CLAIM_COLUMNS))
    _atomic_write(topic_dir(topic) / "claims.tsv", "\n".join(out) + "\n")


def next_claim_id(topic: str) -> str:
    """L1, L2, ... — stable, sortable, human-quotable."""
    rows = read_claims(topic)
    n = 0
    for r in rows:
        m = re.fullmatch(r"L(\d+)", (r.get("id") or "").strip())
        if m:
            n = max(n, int(m.group(1)))
    return f"L{n + 1}"


def append_claim(topic: str, claim: dict[str, str]) -> dict[str, str]:
    kind = (claim.get("kind") or "").strip()
    if kind not in CLAIM_KINDS:
        raise ValueError(f"kind must be one of {CLAIM_KINDS}, got {kind!r}")
    itype = (claim.get("inference_type") or "").strip()
    if kind == "inference" and itype not in INFERENCE_TYPES:
        raise ValueError(f"inference_type must be one of {INFERENCE_TYPES}, got {itype!r}")
    if itype and itype not in INFERENCE_TYPES:
        raise ValueError(f"inference_type must be one of {INFERENCE_TYPES}, got {itype!r}")
    strength = (claim.get("strength") or "").strip()
    if strength and strength not in STRENGTHS:
        raise ValueError(f"strength must be one of {STRENGTHS}, got {strength!r}")

    row = {c: (claim.get(c) or "").strip() for c in CLAIM_COLUMNS}
    row["kind"] = kind
    row["id"] = (claim.get("id") or "").strip() or next_claim_id(topic)
    if kind == "inference":
        row["inference_type"] = itype
    if kind == "hypothesis":
        row["strength"] = strength or "suggestive"
        row["status"] = row.get("status") or "open"
    if kind in ("observation", "report"):
        row["strength"] = strength or "strong"
        row["status"] = row.get("status") or "pending"
    if kind == "inference":
        row["strength"] = strength or "suggestive"
        row["status"] = row.get("status") or "derived"

    claims = read_claims(topic)
    claims = [c for c in claims if c.get("id") != row["id"]]
    claims.append(row)
    write_claims(topic, claims)
    return row


def set_claim_status(topic: str, claim_id: str, status: str) -> dict[str, str]:
    claims = read_claims(topic)
    hit = None
    for c in claims:
        if c.get("id") == claim_id:
            c["status"] = status
            hit = c
            break
    if hit is None:
        raise ValueError(f"no such claim: {claim_id}")
    write_claims(topic, claims)
    return hit


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------


def session_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    return SESSIONS_DIR / f"{safe}.json"


def read_session(session_id: str) -> dict[str, Any]:
    return _read_json(session_path(session_id), {"mode": "idle", "topic": ""})


def write_session(session_id: str, data: dict[str, Any]) -> None:
    data["updated_at"] = now()
    _write_json(session_path(session_id), data)


def set_session_mode(session_id: str, mode: str, topic: str = "") -> dict[str, Any]:
    s = read_session(session_id)
    s["mode"] = mode
    if topic:
        s["topic"] = topic
    s["updated_at"] = now()
    write_session(session_id, s)
    return s
