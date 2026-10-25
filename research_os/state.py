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
    # The block carries the project's standing, the situation, the thinking
    # moves and what the phase is for. It is long because the alternative is
    # an agent that answers the question in front of it and forgets the one
    # behind it.
    "max_inject_chars": 1600,
    # How many literature searches one topic may run before the agent is asked
    # to record what it has or to say what it will conclude. This is a
    # prompt, not a wall: 0 turns the check off. The default is generous —
    # a broad literature sweep is legitimate — and the point is to catch the
    # run that searches forty times and never answers.
    "searches_before_converge": 20,
}


# How much work can pile up before the agent has to look at the whole
# project again. A small topic needs a review sooner than a large one:
# eight searches is most of a small topic, and nothing on a big one.
DEFAULT_REVIEW_EVERY = {
    "searches": 8,
    "turns": 6,
}


def review_thresholds(topic: str | None = None) -> tuple[int, int]:
    """(searches, turns) that trigger a global review.

    The config sets the default; a topic may override it in its own map,
    because a topic with five claims and a topic with seventy are not
    the same size and the same number of searches means something
    different in each.
    """
    cfg = load_config()
    searches = int(cfg.get("review_every_searches", DEFAULT_REVIEW_EVERY["searches"]) or 0)
    turns = int(cfg.get("review_every_turns", DEFAULT_REVIEW_EVERY["turns"]) or 0)
    if topic:
        m = read_map(topic)
        over = m.get("review_every") or {}
        if isinstance(over, dict):
            if over.get("searches"):
                searches = int(over["searches"])
            if over.get("turns"):
                turns = int(over["turns"])
    return max(1, searches), max(1, turns)


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
    # The registry is trusted here, including entries whose directory is
    # gone. Those are orphans, and they are load-bearing: `on` and
    # `repair` find them through this list and rebuild them. Filtering
    # them out would hide the very thing those two actions exist for.
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
    # A name whose directory is gone is free. An orphan - a registry entry
    # with no directory - is a state to repair, not a name to squat on:
    # refusing to create it meant that once a topic directory was removed
    # by anything other than delete_topic, the name could never be used
    # again.
    if (TOPICS_DIR / topic).is_dir():
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


# The three levels of a topic's state.
#
# A topic is not one question but a project with a direction, and an agent
# working on it for a while needs to know where it stands at three levels.
# The core question is the point of the whole thing and rarely changes. The
# goal is what this project is trying to get at. The current task is what
# is being done right now. Without all three, a session drifts: the agent
# answers the question in front of it and forgets the one behind it.
#
# They are stored apart on purpose, so a review can say "the current task
# is fine but the goal is not what the core question asks" - which is the
# drift worth catching.
MAP_GOAL = "goal"
MAP_PHASE = "phase"
MAP_TASK = "current_task"
MAP_NARRATIVE = "narrative"
MAP_PLAN = "plan"


def read_goal(topic: str) -> str:
    """What this project is trying to get at. Broader than the core question."""
    return str(read_map(topic).get(MAP_GOAL) or "").strip()


def read_phase(topic: str) -> str:
    """Which phase the project is in. See PHASES in protocol.py."""
    return str(read_map(topic).get(MAP_PHASE) or "").strip()


def read_current_task(topic: str) -> str:
    """What is being worked on right now. The narrowest of the three."""
    return str(read_map(topic).get(MAP_TASK) or "").strip()


def read_narrative(topic: str) -> str:
    """The current story of the topic: what we think, and why it changed."""
    return str(read_map(topic).get(MAP_NARRATIVE) or "").strip()


def read_plan(topic: str) -> str:
    """What happens next."""
    return str(read_map(topic).get(MAP_PLAN) or "").strip()


def write_state(
    topic: str,
    goal: str | None = None,
    phase: str | None = None,
    current_task: str | None = None,
    narrative: str | None = None,
    plan: str | None = None,
    review_every: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Update the topic's standing, keeping the fields not being changed.

    The narrative and the plan are here because a direction change has to
    be written down somewhere, not just felt. When a review finds that the
    work has drifted, the narrative says what we now think and the plan
    says what follows; without them the next session reads the ledger and
    infers a direction that was never chosen.
    """
    m = read_map(topic)
    if goal is not None:
        m[MAP_GOAL] = str(goal).strip()
    if phase is not None:
        m[MAP_PHASE] = str(phase).strip()
    if current_task is not None:
        m[MAP_TASK] = str(current_task).strip()
    if narrative is not None:
        m[MAP_NARRATIVE] = str(narrative).strip()
    if plan is not None:
        m[MAP_PLAN] = str(plan).strip()
    if review_every is not None:
        over = m.get("review_every") or {}
        if not isinstance(over, dict):
            over = {}
        for k in ("searches", "turns"):
            v = review_every.get(k)
            if v:
                over[k] = max(1, int(v))
        m["review_every"] = over
    write_map(topic, m)
    return m


# --------------------------------------------------------------------------
# the review loop
# --------------------------------------------------------------------------
#
# A topic is reviewed: the agent looks at where the work has got to, asks
# whether it is still serving the core question, and decides whether to
# continue, redirect, or stop. The decision has to be able to bite, or it
# is a suggestion - so a review that says "pause" sets a flag that blocks
# further literature searching until the state is updated.
#
# The counters are per topic, not per turn. A drift that takes three turns
# to appear is not visible in any one of them.

REVIEW_FILE = "review.json"

_REVIEW_DEFAULTS: dict[str, Any] = {
    "reviews": 0,
    "searches_since_review": 0,
    "turns_since_review": 0,
    "paused": False,
    "last_verdict": "",
    "last_reason": "",
    "last_at": 0.0,
    # How many times a review has been demanded without one being made. A
    # demand that is ignored is a nag; after a couple of them the demand
    # stops blocking and becomes a note, because a hook that keeps
    # refusing the same tool is a stall and a stall is worse than a long
    # search.
    "demanded": 0,
}


def read_review(topic: str) -> dict[str, Any]:
    d = topic_dir(topic)
    if not d.is_dir():
        return dict(_REVIEW_DEFAULTS)
    data = _read_json(d / REVIEW_FILE, {})
    out = dict(_REVIEW_DEFAULTS)
    if isinstance(data, dict):
        out.update(data)
    return out


def write_review(topic: str, data: dict[str, Any]) -> None:
    d = topic_dir(topic)
    d.mkdir(parents=True, exist_ok=True)
    data = {**_REVIEW_DEFAULTS, **(data or {})}
    data["updated_at"] = now()
    _write_json(d / REVIEW_FILE, data)


def note_search_since_review(topic: str) -> int:
    """Count a literature search against the pending review."""
    d = read_review(topic)
    d["searches_since_review"] = int(d.get("searches_since_review") or 0) + 1
    write_review(topic, d)
    return int(d["searches_since_review"])


def note_turn_since_review(topic: str) -> int:
    d = read_review(topic)
    d["turns_since_review"] = int(d.get("turns_since_review") or 0) + 1
    write_review(topic, d)
    return int(d["turns_since_review"])


def note_review_demand(topic: str) -> int:
    """Count a review that was asked for and not made."""
    d = read_review(topic)
    d["demanded"] = int(d.get("demanded") or 0) + 1
    write_review(topic, d)
    return int(d["demanded"])


def review_demands(topic: str) -> int:
    return int(read_review(topic).get("demanded") or 0)


def topic_is_paused(topic: str) -> bool:
    return bool(read_review(topic).get("paused"))


def record_review(
    topic: str,
    verdict: str,
    reason: str = "",
    direction_changed: bool = False,
) -> dict[str, Any]:
    """Record a review and act on its verdict.

    A verdict of "pause" or "redirect" stops the literature searching. Not
    as a warning - as a block - until the state is updated to say where the
    work is going. A review that cannot stop anything is a note in a
    margin.
    """
    d = read_review(topic)
    v = str(verdict or "").strip().lower()
    d["reviews"] = int(d.get("reviews") or 0) + 1
    d["last_verdict"] = v
    d["last_reason"] = str(reason or "")
    d["last_at"] = now()
    d["searches_since_review"] = 0
    d["turns_since_review"] = 0
    d["demanded"] = 0
    # Only a verdict that found the work off the core question pauses it.
    # "continue" and "adjust" both keep the work going; "adjust" means the
    # direction is being changed and the state says so.
    d["paused"] = v in ("pause", "off_track")
    d["direction_changed"] = bool(direction_changed)
    write_review(topic, d)
    return d


def clear_pause(topic: str) -> dict[str, Any]:
    d = read_review(topic)
    d["paused"] = False
    write_review(topic, d)
    return d


def record_phase_change(topic: str, from_phase: str, to_phase: str, note: str = "") -> None:
    """Keep the phase history. A project that changes direction should be
    able to show when it did and why."""
    m = read_map(topic)
    hist = list(m.get("phase_history") or [])
    hist.append(
        {
            "from": from_phase,
            "to": to_phase,
            "note": str(note or ""),
            "at": now(),
        }
    )
    m["phase_history"] = hist[-20:]
    write_map(topic, m)


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
    # The amendment history. A row that was revised has to say so, and
    # say what it said before - otherwise a ledger can be rewritten and
    # nobody can tell that it happened. The column is part of the row, so
    # it survives every write, and the old rows simply leave it empty.
    "amended_from",
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
    # Ids are L1, L2, ... L10 — a string sort puts L10 before L2. Sort by the
    # number so the ledger reads in the order it was written.
    rows.sort(key=_claim_id_number)
    return rows


def _claim_id_number(claim: dict[str, str]) -> int:
    match = re.search(r"(\d+)", str(claim.get("id") or ""))
    return int(match.group(1)) if match else 0


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


def _content_key(content: str) -> str:
    """A comparison key for a claim's content.

    Two claims are the same finding when they say the same thing. Whitespace,
    case and trailing punctuation differ between two attempts to record the
    same observation, so they are normalised away before comparison.
    """
    text = " ".join(str(content or "").split()).strip().lower()
    return text.rstrip("。．.!！?？;；,，")


def find_claim(topic: str, content: str) -> dict[str, str] | None:
    """The claim that already says this, if there is one.

    The same finding, recorded twice, is not two pieces of evidence. It
    makes the ledger look bigger than the evidence is, and every inference
    that cites it has to be checked against both copies.
    """
    key = _content_key(content)
    if not key:
        return None
    for claim in read_claims(topic):
        if _content_key(claim.get("content") or "") == key:
            return claim
    return None


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

    # The same finding, recorded twice, is not two pieces of evidence. It
    # makes the ledger look bigger than the evidence is, and every inference
    # that cites it has to be checked against both copies. Return the
    # existing row instead of adding a second one.
    content_key = _content_key(row.get("content") or "")
    if content_key:
        for existing in claims:
            if _content_key(existing.get("content") or "") == content_key:
                return existing

    claims.append(row)
    write_claims(topic, claims)
    return row


def amend_claim(
    topic: str,
    claim_id: str,
    content: str | None = None,
    strength: str | None = None,
    kind: str | None = None,
    source: str | None = None,
    status: str | None = None,
    note: str | None = None,
) -> dict[str, str]:
    """Amend a claim in place, keeping its id.

    A ledger row gets revised: a strength was overstated, an observation
    had a conclusion mixed into it, a source turned out to be wrong. The
    ways to do that without this function are all bad - rewriting the
    file bypasses every check, and deleting and re-adding the row gives
    it a new id, which breaks every other row that cites it.

    The id stays, so the premises pointing at it keep pointing at it. The
    amendment is recorded, so the change is traceable: a ledger that
    silently rewrites itself is not one anybody can check.

    Returns the amended row. Raises ValueError when the id is not there.
    """
    claims = read_claims(topic)
    row = None
    for c in claims:
        if str(c.get("id") or "") == str(claim_id):
            row = c
            break
    if row is None:
        raise ValueError(f"no such claim: {claim_id}")

    amended = dict(row)
    changes: list[str] = []

    if content is not None and str(content) != str(row.get("content") or ""):
        amended["content"] = str(content)
        changes.append("content")
    if strength is not None and str(strength) != str(row.get("strength") or ""):
        amended["strength"] = str(strength)
        changes.append("strength")
    if kind is not None and str(kind) != str(row.get("kind") or ""):
        amended["kind"] = str(kind)
        changes.append("kind")
    if source is not None and str(source) != str(row.get("source") or ""):
        amended["source"] = str(source)
        changes.append("source")
    if status is not None and str(status) != str(row.get("status") or ""):
        amended["status"] = str(status)
        changes.append("status")
    if note is not None and str(note) != str(row.get("note") or ""):
        amended["note"] = str(note)
        changes.append("note")

    if not changes:
        return dict(row)

    # The amendment history travels with the row. A row that was revised
    # should say so, and by whom, and from what.
    history = amended.get("amended_from")
    if history:
        try:
            history = json.loads(history)
        except Exception:
            history = []
    else:
        history = []
    history.append({
        "at": now(),
        "changes": changes,
        "previous": {k: row.get(k) for k in changes},
    })
    amended["amended_from"] = json.dumps(history, ensure_ascii=False)

    out = []
    for c in claims:
        out.append(amended if str(c.get("id") or "") == str(claim_id) else c)
    write_claims(topic, out)
    return amended


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
    data = _read_json(session_path(session_id), {"mode": "idle", "topic": ""})
    if not isinstance(data, dict):
        return {"mode": "idle", "topic": ""}
    return data


def write_session(session_id: str, data: dict[str, Any]) -> None:
    data["updated_at"] = now()
    _write_json(session_path(session_id), data)


def set_session_mode(
    session_id: str, mode: str, topic: str = "", clear_topic: bool = False
) -> dict[str, Any]:
    """Set the session's mode and topic.

    `topic` is written when given, and cleared when `clear_topic` is set. A
    caller that only wants to change the mode must not accidentally drop the
    topic, and a caller that wants to leave research mode must not leave a
    stale topic behind — the next "on" would otherwise report a switch that
    never happened.
    """
    s = read_session(session_id)
    s["mode"] = mode
    if topic:
        s["topic"] = topic
    elif clear_topic:
        s["topic"] = ""
    s["updated_at"] = now()
    write_session(session_id, s)
    return s


# --------------------------------------------------------------------------
# per-turn verdicts
# --------------------------------------------------------------------------
# pre_tool_call cannot see the user message or the conversation, so a verdict
# that depends on them has to be recorded by an earlier hook in the same turn
# and read back. The turn id scopes the record: a verdict from a previous turn
# is stale and must not be applied to this one.


def mark_turn(session_id: str, turn_id: str, synthetic: bool) -> None:
    s = read_session(session_id)
    s["turn_id"] = turn_id
    s["turn_synthetic"] = bool(synthetic)
    s["updated_at"] = now()
    write_session(session_id, s)


def turn_is_synthetic(session_id: str, turn_id: str) -> bool:
    """True only when *this* turn was recorded as synthetic.

    An empty turn id means the caller cannot scope the verdict, so the
    recorded one is used; the record is written by the same process moments
    earlier in the same turn.
    """
    s = read_session(session_id)
    recorded = str(s.get("turn_id") or "")
    if turn_id and recorded and recorded != turn_id:
        return False
    return bool(s.get("turn_synthetic"))


# --------------------------------------------------------------------------
# per-turn tool activity
# --------------------------------------------------------------------------
# pre_llm_call fires once, at the start of a turn, so the conversation history
# it sees holds the *previous* turn's tool results. To know what happened
# during the current turn — which evidence tools it used, whether it recorded
# anything — the tool activity has to be accumulated as it happens.
#
# Activity is stored per turn id, not as a single running counter: "the turn
# that just ended left evidence unaccounted for" and "this turn has recorded
# nothing yet" are different questions, and mixing them makes a busy turn
# look like a clean one.

# How many turns of activity to keep. Only the previous turn is ever needed.
_ACTIVITY_TURNS_KEPT = 3


def _activity_key(turn_id: str) -> str:
    return turn_id or "no-turn"


def note_tool_activity(
    session_id: str,
    turn_id: str,
    tool_name: str,
    is_evidence: bool,
    is_ledger: bool,
    is_move: bool = False,
) -> None:
    s = read_session(session_id)
    turns = s.get("turn_activities")
    if not isinstance(turns, dict):
        turns = {}
    key = _activity_key(turn_id)
    activity = turns.get(key)
    if not isinstance(activity, dict):
        activity = {"evidence": 0, "ledger": 0, "moves": 0, "tools": []}
        turns[key] = activity
    if is_evidence:
        activity["evidence"] = int(activity.get("evidence") or 0) + 1
    if is_ledger:
        activity["ledger"] = int(activity.get("ledger") or 0) + 1
    # A research move is a write that moves the question forward, as opposed
    # to a write that only records what was already known. It is the
    # difference between "the agent wrote down the ledger" and "the agent
    # did research".
    if is_move:
        activity["moves"] = int(activity.get("moves") or 0) + 1
    tools = activity.setdefault("tools", [])
    if len(tools) < 40:
        tools.append(tool_name)
    # Keep only the most recent turns.
    if len(turns) > _ACTIVITY_TURNS_KEPT:
        for old in list(turns)[:-_ACTIVITY_TURNS_KEPT]:
            turns.pop(old, None)
    s["turn_activities"] = turns
    s["updated_at"] = now()
    write_session(session_id, s)


def note_topic_search(topic: str, tool_name: str = "") -> int:
    """Count a literature search against the topic. Returns the new count.

    The count is per topic, not per turn: a run that searches fifty times
    across three turns and never answers is the failure this catches, and a
    per-turn counter would reset exactly when it matters.
    """
    if not topic:
        return 0
    d = topic_dir(topic)
    if not d.is_dir():
        return 0
    path = d / "search-count.json"
    data = _read_json(path, {"count": 0, "tools": [], "reminded": 0})
    data["count"] = int(data.get("count") or 0) + 1
    tools = data.setdefault("tools", [])
    if tool_name and len(tools) < 60:
        tools.append(tool_name)
    data["updated_at"] = now()
    _write_json(path, data)
    return int(data["count"])


def topic_search_count(topic: str) -> int:
    if not topic:
        return 0
    path = topic_dir(topic) / "search-count.json"
    return int(_read_json(path, {}).get("count") or 0)


def topic_search_reminded(topic: str) -> int:
    """How many times the convergence check has already interrupted this topic."""
    if not topic:
        return 0
    path = topic_dir(topic) / "search-count.json"
    return int(_read_json(path, {}).get("reminded") or 0)


def note_topic_reminder(topic: str) -> int:
    if not topic:
        return 0
    path = topic_dir(topic) / "search-count.json"
    data = _read_json(path, {"count": 0, "tools": []})
    data["reminded"] = int(data.get("reminded") or 0) + 1
    _write_json(path, data)
    return int(data["reminded"])


def reset_topic_searches(topic: str) -> None:
    """Reset the search counter, e.g. when a new question opens on the topic."""
    if not topic:
        return
    d = topic_dir(topic)
    if not d.is_dir():
        return
    _write_json(
        d / "search-count.json",
        {"count": 0, "tools": [], "reminded": 0, "updated_at": now()},
    )


def turn_activity(session_id: str, turn_id: str) -> dict[str, Any]:
    """What one specific turn did."""
    s = read_session(session_id)
    turns = s.get("turn_activities")
    if not isinstance(turns, dict):
        return {"evidence": 0, "ledger": 0, "tools": []}
    activity = turns.get(_activity_key(turn_id))
    if not isinstance(activity, dict):
        return {"evidence": 0, "ledger": 0, "tools": []}
    return {
        "evidence": int(activity.get("evidence") or 0),
        "ledger": int(activity.get("ledger") or 0),
        "tools": list(activity.get("tools") or []),
    }


def previous_turn_activity(session_id: str, current_turn_id: str = "") -> dict[str, Any]:
    """What the turn before the current one did.

    With no turn id, the most recently active turn is described. With one, the
    most recently active turn that is not the current one is described — the
    turn that just ended, which is the one a gap report is about.
    """
    s = read_session(session_id)
    turns = s.get("turn_activities")
    if not isinstance(turns, dict) or not turns:
        return {"evidence": 0, "ledger": 0, "tools": []}
    current = _activity_key(current_turn_id)
    best_key = ""
    best_value: Any = None
    for key, value in turns.items():
        if not isinstance(value, dict):
            continue
        if current_turn_id and key == current:
            continue
        if key > best_key:
            best_key = key
            best_value = value
    if not isinstance(best_value, dict):
        return {"evidence": 0, "ledger": 0, "tools": []}
    return {
        "evidence": int(best_value.get("evidence") or 0),
        "ledger": int(best_value.get("ledger") or 0),
        "tools": list(best_value.get("tools") or []),
    }
