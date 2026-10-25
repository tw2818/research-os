"""Research OS — a research-thinking controller for Hermes.

Two moving parts:

  research_mode  (tool)  the agent opens and closes research mode itself, and
                           manages topics (create / list / switch / delete).
  pre_llm_call   (hook)  while a session is in research mode, injects the
                           topic's core question, competing explanations, the
                           claim ledger digest, and the four-claim-type
                           discipline. The user never sees this text.

The tool is the switch; the hook is the reflex. The agent decides to enter
research mode (it has context the hook lacks: what the user actually wants),
and the hook then changes how the agent behaves for the rest of the session.

Everything fails open. A broken Research OS must degrade to "a normal agent",
never to "a stalled agent".
"""

from __future__ import annotations

import re
import json
import sys
import traceback
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _load_impl_modules() -> tuple[Any, Any, Any, Any, Any]:
    """Load the implementation modules under a private namespace.

    Two problems this avoids.

    *Name collision.* Hermes imports a plugin's modules flat, so `state` and
    `classify` land in `sys.modules` as top-level names shared with the whole
    process. A different plugin that ships a `state.py` silently wins.

    *Stale code.* `hermes plugins enable` re-imports the plugin's entry module
    but does not evict its flat siblings, so a new `__init__.py` can call a
    new function in an old `state.py` and get an AttributeError — which the
    hook then swallows, and the guard stops firing.

    Loading the modules by path under a private namespace makes both
    impossible: the names cannot collide, and the entry module controls the
    lifetime of its own submodules.
    """
    import importlib.util

    ns = "research_os_impl"
    # Drop the previous generation so a reload always picks up the new code.
    for name in [n for n in sys.modules if n == ns or n.startswith(ns + ".")]:
        sys.modules.pop(name, None)

    mods: dict[str, Any] = {}
    # `state` first: `protocol` and `validate` import it at module level, so
    # it must exist in sys.modules before they are executed.
    for short in ("state", "audit", "classify", "protocol", "validate"):
        full = f"{ns}.{short}"
        path = _HERE / f"{short}.py"
        # Drop any cached bytecode for this file first. A .pyc is validated
        # against the source's mtime and size, and an edit that lands inside
        # the same second and leaves the size unchanged passes that check —
        # the old bytecode then runs against the new source, silently, and the
        # symptom is a function that "does not exist" on a file that plainly
        # defines it.
        _drop_bytecode(path)
        spec = importlib.util.spec_from_file_location(full, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {short}.py from {_HERE}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
        mods[short] = module
    return (
        mods["audit"],
        mods["classify"],
        mods["protocol"],
        mods["state"],
        mods["validate"],
    )


def _drop_bytecode(path: Path) -> None:
    """Remove the .pyc files a source file could have produced."""
    import importlib.util

    try:
        cache = Path(importlib.util.cache_from_source(str(path)))
    except Exception:
        return
    for candidate in (cache, cache.with_suffix(".opt-1.pyc"), cache.with_suffix(".opt-2.pyc")):
        try:
            candidate.unlink()
        except OSError:
            pass


# The package is imported both as a plugin (the loader drops the cached
# bytecode for each file) and as a package by the tests and the scripts, and
# the second path is not covered. A .pyc is validated against the source's
# mtime and size, and an edit that lands in the same second and leaves the
# size unchanged passes that check — the old bytecode then runs against the
# new source, silently. Drop it here, before anything is imported.
for _cached in Path(__file__).resolve().parent.glob("__pycache__/*.pyc"):
    try:
        _cached.unlink()
    except OSError:
        pass

try:
    _audit, _classify, _protocol, _state, _validate = _load_impl_modules()
except Exception:  # pragma: no cover - package layout (repo / tests)
    from . import audit as _audit  # type: ignore[no-redef]
    from . import classify as _classify  # type: ignore[no-redef]
    from . import protocol as _protocol  # type: ignore[no-redef]
    from . import state as _state  # type: ignore[no-redef]
    from . import validate as _validate  # type: ignore[no-redef]

# The implementation modules under their public names, so the repo's own
# tests and scripts address the same modules the plugin runs. Without this,
# `research_os.state` and the plugin's `research_os_impl.state` are two
# different modules with two different sets of state.
audit = _audit
classify = _classify
protocol = _protocol
state = _state
validate = _validate


# ---------------------------------------------------------------------------
# tool implementation
# ---------------------------------------------------------------------------

TOOL_SCHEMA: dict[str, Any] = {
    "name": "research_mode",
    "description": (
        "Research mode switch and topic manager. CALL THIS BEFORE answering a "
        "research question or before running a literature search. "
        "ACTION 'on'/'switch' enter or switch topic (needs an existing topic name). "
        "ACTION 'off' leaves research mode. "
        "ACTION 'list' lists topics. "
        "ACTION 'create' makes a new topic (needs project + core_question — a topic "
        "without a scientific question is not a research topic). "
        "ACTION 'delete' removes a topic (soft delete by default). "
        "ACTION 'repair' rebuilds missing topic state. "
        "ACTION 'claim' records a claim in the topic ledger (kind: observation/report/"
        "inference/hypothesis) and ACTION 'validate' checks the ledger. "
        "ACTION 'contradict' records a competing explanation and what evidence would "
        "separate it (title + separator — a contradiction with no way to resolve it is "
        "just a worry). ACTION 'question' records an open question, and closes one when "
        "given 'closed' (the question being closed) plus 'question' (what answered it). "
        "ACTION 'hypothesis' puts an untested idea on the table — proposing one is "
        "research, so it has its own action rather than being bent into a claim. "
        "ACTION 'map' updates the research map (core_question, hypotheses, tags, open_questions). "
        "ACTION 'set_state' says where the project stands: 'goal' (what it is trying to get at), "
        "'phase' (scoping | evidence | weighing | closing | writing - each phase calls for a "
        "different way of thinking, and the phase changes how the next turn is worked on), and "
        "'current_task' (what is being done right now). ACTION 'review' records a global review - "
        "whether the work is still serving the core question, with a verdict "
        "(continue | redirect | pause) and the reason. A project can drift without any single "
        "claim being wrong, so the alignment is checked explicitly; a 'pause' verdict stops "
        "the literature searching until ACTION 'set_state' says where the work is going. "
        "Call 'on' when the user asks why something happens, whether evidence can "
        "separate explanations, for a literature review, for a hypothesis, or when a "
        "claim needs verification. Do NOT call for chores: file conversion, imports, "
        "formatting, installs, unrelated debugging, or short factual lookups."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "on", "off", "status", "list", "create", "switch",
                    "contradict", "question", "hypothesis", "map",
                    "set_state", "review",
                    "delete", "claim", "amend", "validate", "repair",
                ],
                "description": "What to do.",
            },
            "project": {
                "type": "string",
                "description": "Topic name. Required for on/switch/create/delete/claim/validate.",
            },
            "core_question": {
                "type": "string",
                "description": "The scientific question this topic addresses. Required for create.",
            },
            "review_every": {
                "type": "object",
                "description": (
                    "How much work may pile up before a global review is "
                    "demanded, for this topic: {'searches': n, 'turns': n}. "
                    "A small topic wants a review sooner than a large one."
                ),
            },

            "hypotheses": {
                "type": "string",
                "description": (
                    "Initial competing explanations for create, separated by ' | ' "
                    "(a single string — Hermes mangles array arguments)."
                ),
            },
            "tags": {
                "type": "string",
                "description": "Domain tags for create, separated by ', '.",
            },
            "permanent": {
                "type": "boolean",
                "description": "For delete: skip the trash and remove outright. Default false.",
            },
            "title": {
                "type": "string",
                "description": (
                    "For action 'contradict': the competing explanation, stated as a "
                    "claim about the world that could be wrong."
                ),
            },
            "separator": {
                "type": "string",
                "description": (
                    "For action 'contradict': what evidence would tell the competing "
                    "explanations apart. A contradiction with no way to resolve it is "
                    "just a worry, so this is required."
                ),
            },
            "question": {
                "type": "string",
                "description": (
                    "For action 'question': an open question that is still unresolved. "
                    "With 'closed' set, this is instead what answered the closed question."
                ),
            },
            "closed": {
                "type": "string",
                "description": (
                    "For action 'question': the open question being closed. Closing a "
                    "question is a research act, not housekeeping — the answer goes in "
                    "'question' and the closed one stops being open."
                ),
            },
            "goal": {
                "type": "string",
                "description": (
                    "For action 'set_state': what this project is trying to get at. "
                    "Broader than the core question - the core question is the point, "
                    "the goal is the aim."
                ),
            },
            "phase": {
                "type": "string",
                "description": (
                    "For action 'set_state': which phase the project is in. One of "
                    "scoping | evidence | weighing | closing | writing. The phase "
                    "decides how the next turn thinks: scoping frames the question, "
                    "evidence gathers against a stated expectation, weighing pits the "
                    "explanations against each other, closing calibrates the strength "
                    "of the conclusion, writing shapes it for a reader."
                ),
            },
            "current_task": {
                "type": "string",
                "description": (
                    "For action 'set_state': what is being worked on right now. The "
                    "narrowest of the three levels - and the one that most easily "
                    "drifts away from the core question without anyone noticing."
                ),
            },
            "claim_id": {
                "type": "string",
                "description": (
                    "For action 'amend': the id of the row to revise, e.g. 'L44'. "
                    "The row keeps its id when amended, so every other row that "
                    "cites it still points at the right thing."
                ),
            },
            "strength": {
                "type": "string",
                "description": (
                    "For action 'amend': the corrected strength of the row."
                ),
            },
            "kind": {
                "type": "string",
                "description": (
                    "For action 'amend': the corrected kind of the row."
                ),
            },
            "note": {
                "type": "string",
                "description": (
                    "For action 'amend': a note on what changed and why."
                ),
            },
            "narrative": {
                "type": "string",
                "description": (
                    "For action 'set_state': what we currently believe, and why it "
                    "changed. This is the topic's story - update it when the "
                    "evidence moves the picture, not only when the ledger grows."
                ),
            },
            "plan": {
                "type": "string",
                "description": (
                    "For action 'set_state': what happens next, in one line. Setting "
                    "it lifts a pause: a review that stopped the work is answered "
                    "by saying where the work is going."
                ),
            },
            "verdict": {
                "type": "string",
                "description": (
                    "For action 'review': continue | redirect | pause. Whether the "
                    "work is still serving the core question, and what to do about it. "
                    "'pause' and 'off_track' stop the literature searching until "
                    "set_state says where the work is going - so a review that finds "
                    "drift has to be answered, not just recorded."
                ),
            },
            "direction_changed": {
                "type": "boolean",
                "description": (
                    "For action 'review': true when this round's evidence changed the "
                    "direction - the interpretation, not just the amount of material."
                ),
            },
            "review_note": {
                "type": "string",
                "description": (
                    "For action 'review': what the review found. A verdict without a "
                    "reason cannot be acted on."
                ),
            },
            "hypothesis": {
                "type": "string",
                "description": (
                    "For action 'hypothesis': the untested idea being proposed. It needs "
                    "no source — that is what makes it a hypothesis — but it does need to "
                    "be stated as a claim about the world that could turn out to be wrong."
                ),
            },
            "open_questions": {
                "type": "string",
                "description": "For action 'map': open questions, separated by ' | '.",
            },
            "claims": {
                "type": "array",
                "description": (
                    "For action 'claim': several claims at once, each the same "
                    "shape as the single 'claim' object. Use this when a round "
                    "of searching turned up several findings worth recording - "
                    "one call with the whole batch, rather than one call per "
                    "finding. Rows come back with their assigned ids in the "
                    "same order."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string"},
                        "content": {"type": "string"},
                        "source": {"type": "string"},
                        "premises": {"type": "array", "items": {"type": "string"}},
                        "inference_type": {"type": "string"},
                        "strength": {"type": "string"},
                        "status": {"type": "string"},
                        "note": {"type": "string"},
                    },
                },
            },
            "claim": {
                "type": "object",
                "description": "For action 'claim': the claim to record.",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["observation", "report", "inference", "hypothesis"],
                    },
                    "content": {"type": "string"},
                    "inference_type": {
                        "type": "string",
                        "enum": ["descriptive", "explanatory", "predictive", "analogy", "abductive"],
                    },
                    "strength": {
                        "type": "string",
                        "enum": ["descriptive", "suggestive", "strong", "established"],
                    },
                    "premises": {
                        "type": "string",
                        "description": (
                            "Claim ids this rests on, separated by ' ' (inference only). "
                            "Example: 'L1 L2'."
                        ),
                    },
                    "source": {
                        "type": "string",
                        "description": (
                            "PMID/DOI/paper. Required for observation (read in the "
                            "original) and report (abstract-level check is enough)."
                        ),
                    },
                    "status": {
                        "type": "string",
                        "description": (
                            "verified | derived | open | pending. A claim whose "
                            "source has not been checked yet is 'pending', not "
                            "'verified'."
                        ),
                    },
                    "note": {"type": "string"},
                },
                "required": ["kind", "content"],
            },
        },
        "required": ["action"],
    },
}


def _split_list(value: Any, sep: str = "|") -> list[str]:
    """Normalize a list-ish argument into a list of non-empty strings.

    Hermes' argument coercion can hand a plugin several shapes for the same
    field: the declared list, a bare dict wrapped in a list, or a single string.
    All of them are accepted here so a model that guesses wrong still lands.
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(sep)
    elif isinstance(value, dict):
        inner = value.get("item", value)
        if isinstance(inner, str):
            parts = inner.split(sep)
        elif isinstance(inner, list):
            parts = [str(x) for x in inner]
        else:
            parts = [str(inner)]
    elif isinstance(value, list):
        flat: list[str] = []
        for item in value:
            if isinstance(item, dict):
                inner = item.get("item", item)
                if isinstance(inner, list):
                    flat.extend(str(x) for x in inner)
                elif isinstance(inner, str):
                    flat.extend(inner.split(sep))
                else:
                    flat.append(str(inner))
            elif isinstance(item, str):
                flat.extend(item.split(sep))
            else:
                flat.append(str(item))
        parts = flat
    else:
        parts = [str(value)]
    return [p.strip() for p in parts if p and str(p).strip()]


def _tool_handler(args: dict[str, Any], **kwargs: Any) -> str:
    action = str(args.get("action") or "status").strip().lower()
    project = str(args.get("project") or "").strip()
    session_id = str(kwargs.get("session_id") or args.get("session_id") or "unknown")

    def ok(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False)

    def fail(msg: str) -> str:
        return json.dumps({"error": msg}, ensure_ascii=False)

    try:
        if action == "status":
            s = _state.read_session(session_id)
            topics = _state.list_topics()
            out: dict[str, Any] = {
                "mode": s.get("mode", "idle"),
                "topic": s.get("topic", ""),
                "topics": [t["name"] for t in topics],
            }
            # With a project, the question is "what state is this topic
            # in", not "what mode is this session in". Without this the
            # answer was the session's, which is idle for any caller that
            # is not in a session - so an agent asking about a topic got
            # an answer about nothing, and went to read the files
            # instead.
            if project:
                if not _state.topic_exists(project):
                    return fail(f"no such topic: {project}")
                m = _state.read_map(project)
                out["project"] = {
                    "name": project,
                    "core_question": str(m.get("core_question") or ""),
                    "goal": str(m.get("goal") or ""),
                    "phase": str(m.get("phase") or ""),
                    "current_task": str(m.get("current_task") or ""),
                    "narrative": str(m.get("narrative") or ""),
                    "plan": str(m.get("plan") or ""),
                    "claims": len(_state.read_claims(project)),
                    "contradictions": len(
                        (_state.read_contradictions(project) or {}).get("items") or []
                    ),
                    "reviews": _state.read_review(project).get("reviews", 0),
                    "paused": _state.topic_is_paused(project),
                }
            return ok(out)

        if action == "list":
            topics = _state.list_topics()
            return ok({
                "topics": [
                    {
                        "name": t["name"],
                        "core_question": t.get("core_question", ""),
                        "tags": t.get("tags", []),
                        "state_ok": bool(_state.read_map(t["name"])),
                    }
                    for t in topics
                ]
            })

        if action == "repair":
            # Rebuild state directories that went missing while the registry
            # still lists the topic.
            fixed: list[str] = []
            for t in _state.list_topics():
                if _state.read_map(t["name"]):
                    continue
                _state.write_map(
                    t["name"],
                    {
                        "topic": t["name"],
                        "core_question": t.get("core_question", ""),
                        "hypotheses": t.get("hypotheses", []),
                        "tags": t.get("tags", []),
                        "open_questions": [],
                    },
                )
                fixed.append(t["name"])
            return ok({"repaired": fixed})

        if action == "create":
            if not project:
                return fail("project is required")
            if not args.get("core_question"):
                return fail(
                    "core_question is required: a topic without a scientific question "
                    "is not a research topic — state the question first"
                )
            hyp = _split_list(args.get("hypotheses"), sep="|")
            tags = _split_list(args.get("tags"), sep=",")
            meta = _state.create_topic(
                project,
                str(args.get("core_question")),
                hyp,
                tags,
            )
            return ok({"created": project, **meta})

        if action == "delete":
            if not project:
                return fail("project is required")
            dest = _state.delete_topic(project, permanent=bool(args.get("permanent")))
            s = _state.read_session(session_id)
            if s.get("topic") == project:
                _state.set_session_mode(session_id, "idle")
            return ok({"deleted": project, "trashed_to": dest, "permanent": bool(args.get("permanent"))})

        if action in ("on", "switch"):
            if not project:
                return fail("project is required: name the topic to work on")
            if not _state.topic_exists(project):
                return fail(
                    f"no such topic: {project!r}. Call action='list' to see topics, "
                    "or action='create' to make one (it needs a core_question)"
                )
            m = _state.read_map(project)
            if not m:
                # The registry knows the topic but its state directory is gone.
                # Rebuild it from the registry rather than entering an empty topic.
                reg = next(
                    (t for t in _state.list_topics() if t["name"] == project), {}
                )
                m = {
                    "topic": project,
                    "core_question": reg.get("core_question", ""),
                    "hypotheses": reg.get("hypotheses", []),
                    "tags": reg.get("tags", []),
                    "open_questions": [],
                }
                _state.write_map(project, m)
            s = _state.read_session(session_id)
            previous = s.get("topic", "")
            # A new question on the topic starts a new count: the search run
            # that triggered the convergence check belonged to the previous
            # question, not to this one.
            if previous != project:
                _state.reset_topic_searches(project)
            _state.set_session_mode(session_id, "research", project)
            m = _state.read_map(project)
            hint = ""
            if previous and previous != project:
                hint = (
                    f" Switched from {previous} to {project}. Do not carry hypotheses "
                    f"or claims over from {previous}."
                )
            # Tell the agent what this topic is still missing, and which
            # actions record it. The tool response is where the agent looks;
            # the injected protocol is capped and cannot carry this.
            missing = _map_gaps(project)
            available = (
                "可用动作：claim（记证据）、contradict（记竞争解释和区分它们的证据）、"
                "question（记未解决的问题）、map（更新研究地图）、validate（检查账本）。"
            )
            return ok({
                "mode": "research",
                "topic": project,
                "core_question": m.get("core_question", ""),
                "hypotheses": m.get("hypotheses", []),
                "open_questions": m.get("open_questions", []),
                "contradictions": len(
                    (_state.read_contradictions(project) or {}).get("items") or []
                ),
                "claims": len(_state.read_claims(project)),
                "available_actions": available,
                "missing": missing,
                "note": (hint.strip() + ("\n" if hint.strip() and missing else "")
                         + ("\n".join(missing) if missing else "")).strip(),
            })

        if action == "off":
            # Clear the topic too: a stale topic makes the next "on" report a
            # switch that never happened.
            _state.set_session_mode(session_id, "idle", clear_topic=True)
            return ok({"mode": "idle", "topic": ""})

        if action == "claim":
            if not project:
                return fail("project is required")
            # A batch. One round of searching turns up several findings at
            # once, and one call that takes the whole batch is the honest
            # way to record them.
            #
            # The reason this matters: an agent that has no batch entry
            # point will look for one, and the one it finds is the state
            # module's own API - which writes the ledger outside the tool
            # and outside every check the tool performs. A live run did
            # exactly that.
            #
            # Each claim in the batch goes through the same checks as a
            # single one. A batch is not a way around the validation; it
            # is the same validation, once per row.
            batch = args.get("claims")
            if isinstance(batch, list) and batch:
                recorded = []
                duplicates = []
                rejected = []
                for raw in batch:
                    if not isinstance(raw, dict):
                        rejected.append({"claim": raw, "why": "not an object"})
                        continue
                    one = dict(raw)
                    if not one.get("content"):
                        rejected.append({"claim": one, "why": "content is required"})
                        continue
                    one["premises"] = " ".join(
                        _split_list(one.get("premises"), sep=" ")
                    )
                    existing = _state.find_claim(project, one.get("content") or "")
                    if existing is not None:
                        duplicates.append(existing.get("id"))
                        continue
                    # One bad row must not eat the rest. The first version
                    # of this loop had no guard, so a single inference with
                    # a missing inference_type raised and every row after
                    # it was silently dropped - the batch reported fewer
                    # rows than it was given and said nothing about why.
                    try:
                        recorded.append(_state.append_claim(project, one))
                    except Exception as exc:
                        rejected.append({
                            "claim": {
                                "content": one.get("content"),
                                "kind": one.get("kind"),
                            },
                            "why": str(exc),
                        })
                msg = {
                    "recorded": recorded,
                    "duplicates": duplicates,
                    "rejected": rejected,
                    "count": len(recorded),
                }
                if rejected:
                    msg["note"] = (
                        f"{len(rejected)} 条没进去，原因在 rejected 里。"
                        "其余的已经记好了——改完再补记那几条就行。"
                    )
                return ok(msg)
            claim = dict(args.get("claim") or {})
            if not claim.get("content"):
                return fail("claim.content is required")
            claim["premises"] = " ".join(_split_list(claim.get("premises"), sep=" "))
            existing = _state.find_claim(project, claim.get("content") or "")
            if existing is not None:
                # The same finding, recorded twice, is not two pieces of
                # evidence. Say so plainly so the agent stops repeating
                # itself instead of re-recording what is already there.
                return ok({
                    "recorded": existing,
                    "duplicate": True,
                    "note": (
                        f"这条已经在账本里了（{existing.get('id')}），没有重复记录。"
                        f"如果你想记的是它的一个新推论，用 inference 并把 premises "
                        f"指向 {existing.get('id')}。"
                    ),
                })
            row = _state.append_claim(project, claim)
            return ok({"recorded": row})

        if action == "amend":
            if not project:
                return fail("project is required")
            claim_id = str(args.get("claim_id") or "").strip()
            if not claim_id:
                return fail(
                    "claim_id is required: the id of the row to amend, e.g. L44"
                )
            patch = {
                k: args.get(k)
                for k in ("content", "strength", "kind", "source", "status", "note")
                if args.get(k) is not None
            }
            if not patch:
                return fail(
                    "give at least one of: content, strength, kind, source, "
                    "status, note - what is being changed"
                )
            before = _state.read_claims(project)
            old_row = next(
                (c for c in before if str(c.get("id") or "") == claim_id), None
            )
            if old_row is None:
                return fail(f"no such claim: {claim_id}")
            try:
                amended = _state.amend_claim(project, claim_id, **patch)
            except ValueError as exc:
                return fail(str(exc))
            return ok({
                "amended": amended,
                "previous": {
                    k: old_row.get(k)
                    for k in ("content", "strength", "kind", "source", "status")
                    if old_row.get(k) is not None
                },
                "note": (
                    "同一行的内容改了，id 没变——别的断言指向它的引用仍然有效。"
                    "改动留了痕，在 amended_from 里。"
                ),
            })

        if action == "contradict":
            # The competing explanations and what separates them. Without it
            # the research map is a list of hypotheses with no way to tell
            # them apart, which is the state the whole design is against.
            if not project:
                return fail("project is required")
            title = str(args.get("title") or "").strip()
            if not title:
                return fail("title is required: what the contradiction is about")
            separator = str(args.get("separator") or "").strip()
            if not separator:
                return fail(
                    "separator is required: what evidence would tell the "
                    "explanations apart. A contradiction with no way to "
                    "resolve it is just a worry."
                )
            data = _state.read_contradictions(project) or {"items": []}
            items = list(data.get("items") or [])
            items.append(
                {
                    "title": title,
                    "separator": separator,
                    "hypotheses": _split_list(args.get("hypotheses"), sep="|"),
                    "status": str(args.get("status") or "open"),
                    "recorded_at": _state.now(),
                }
            )
            data["items"] = items
            data["updated_at"] = _state.now()
            _state.write_contradictions(project, data)
            return ok({"contradiction": items[-1], "count": len(items)})

        if action == "question":
            # An open question: what is still unresolved. Recording it is the
            # only way the next session can pick it up. Closing one is
            # equally a research act - a question that was answered stops
            # being open, and leaving it open forever makes the map read as
            # permanently unsure.
            if not project:
                return fail("project is required")
            text = str(args.get("question") or "").strip()
            if not text:
                return fail("question is required")
            m = _state.read_map(project)
            questions = list(m.get("open_questions") or [])
            if text not in questions:
                questions.append(text)
            m["open_questions"] = questions
            closed = str(args.get("closed") or "").strip()
            if closed:
                # Close a question by giving the answer that closed it.
                m["open_questions"] = [q for q in questions if q != closed]
                resolved = list(m.get("resolved_questions") or [])
                entry = {"question": closed, "answer": text}
                if entry not in resolved:
                    resolved.append(entry)
                m["resolved_questions"] = resolved
            m["updated_at"] = _state.now()
            _state.write_map(project, m)
            return ok(
                {
                    "open_question": text,
                    "count": len(m["open_questions"]),
                    "closed": closed or None,
                }
            )

        if action == "set_state":
            # Where the project stands: the goal, the phase, the current
            # task. The agent sets these itself - it is the one that knows
            # whether the work has moved on to a different question.
            if not project:
                return fail("project is required")
            goal = args.get("goal")
            phase = args.get("phase")
            task = args.get("current_task")
            narrative = args.get("narrative")
            plan = args.get("plan")
            every = args.get("review_every")
            if every is not None and not isinstance(every, dict):
                return fail("review_every must be an object")
            if all(
                x is None for x in (goal, phase, task, narrative, plan, every)
            ):
                return fail(
                    "give at least one of: goal (what this project is trying "
                    "to get at), phase (scoping/evidence/weighing/closing/"
                    "writing), current_task (what is being done right now), "
                    "narrative (what we currently believe and why it changed), "
                    "plan (what happens next), review_every "
                    "({'searches': n, 'turns': n} - how much work may pile "
                    "up before a global review)"
                )
            if phase is not None:
                key = str(phase).strip()
                if key not in _protocol.PHASES:
                    return fail(
                        f"unknown phase {key!r}. Phases: "
                        + ", ".join(sorted(_protocol.PHASES))
                    )
            before = _state.read_phase(project)
            m = _state.write_state(
                project,
                goal=goal,
                phase=phase,
                current_task=task,
                narrative=narrative,
                plan=plan,
                review_every=every,
            )
            # Updating the standing is what lifts a pause: the review said
            # the work had drifted, and the agent has now said where it is
            # going. That closes the loop, and the searching can resume.
            was_paused = _state.topic_is_paused(project)
            if was_paused:
                _state.clear_pause(project)
            if phase is not None and str(phase).strip() != before:
                _state.record_phase_change(
                    project, before, str(phase).strip(), str(args.get("note") or "")
                )
            return ok(
                {
                    "goal": m.get("goal"),
                    "phase": m.get("phase"),
                    "current_task": m.get("current_task"),
                    "narrative": m.get("narrative"),
                    "plan": m.get("plan"),
                    "review_every": m.get("review_every") or {},
                    "resumed": was_paused,
                }
            )

        if action == "review":
            # A recorded global review: did the work stay on the core
            # question? The drift that no per-claim check can catch is
            # recorded here, in one place, so it can be read back.
            if not project:
                return fail("project is required")
            verdict = str(args.get("verdict") or "").strip()
            if not verdict:
                return fail(
                    "verdict is required: continue | redirect | pause"
                )
            note = str(args.get("review_note") or args.get("note") or "").strip()
            if not note:
                return fail(
                    "review_note is required: what the review found. A verdict "
                    "without a reason cannot be acted on."
                )
            m = _state.read_map(project)
            reviews = list(m.get("reviews") or [])
            entry = {
                "verdict": verdict,
                "note": note,
                "at": _state.now(),
                "phase": m.get("phase") or "",
                "current_task": m.get("current_task") or "",
            }
            reviews.append(entry)
            m["reviews"] = reviews[-20:]
            _state.write_map(project, m)
            # The verdict has to be able to stop the work. A "pause" or
            # "off_track" blocks further searching until the state says
            # where the work is going.
            rv = _state.record_review(
                project,
                verdict,
                reason=note,
                direction_changed=bool(args.get("direction_changed")),
            )
            return ok(
                {
                    "review": entry,
                    "reviews": len(m["reviews"]),
                    "paused": bool(rv.get("paused")),
                    "searches_since_review": int(rv.get("searches_since_review") or 0),
                }
            )

        if action == "hypothesis":
            # A hypothesis is not a claim: it has no source and is not yet
            # true, but proposing one is the thing research does. It gets
            # its own action so the agent does not have to bend it into a
            # claim with a missing source, and so the ledger can show what
            # was speculated separately from what was found.
            if not project:
                return fail("project is required")
            text = str(args.get("hypothesis") or args.get("content") or "").strip()
            if not text:
                return fail("hypothesis is required: what is the untested idea")
            m = _state.read_map(project)
            hyps = list(m.get("hypotheses") or [])
            if text not in hyps:
                hyps.append(text)
            m["hypotheses"] = hyps
            m["updated_at"] = _state.now()
            _state.write_map(project, m)
            return ok({"hypothesis": text, "count": len(hyps)})

        if action == "map":
            # Update the research map: the hypotheses, the tags, the core
            # question. The map is the current state of understanding and it
            # is supposed to change as evidence accumulates.
            if not project:
                return fail("project is required")
            m = _state.read_map(project)
            if "core_question" in args and args.get("core_question"):
                m["core_question"] = str(args["core_question"]).strip()
            if "hypotheses" in args:
                m["hypotheses"] = _split_list(args.get("hypotheses"), sep="|")
            if "tags" in args:
                m["tags"] = _split_list(args.get("tags"), sep=",")
            if "open_questions" in args:
                m["open_questions"] = _split_list(args.get("open_questions"), sep="|")
            m["updated_at"] = _state.now()
            _state.write_map(project, m)
            return ok({"map": m})

        if action == "validate":
            if not project:
                return fail("project is required")
            try:
                from .validate import format_report, validate_claims  # type: ignore
            except ImportError:
                from validate import format_report, validate_claims  # type: ignore

            report = validate_claims(project)
            return ok({"report": format_report(report), "detail": report})

        return fail(f"unknown action: {action!r}")
    except Exception as exc:  # fail open — never break the agent's turn
        return fail(f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# hook: pre_llm_call
# ---------------------------------------------------------------------------


def _pre_llm_call(**kwargs: Any) -> dict[str, Any] | None:
    try:
        cfg = _state.load_config()
        if not cfg.get("inject_protocol", True):
            return None

        session_id = str(kwargs.get("session_id") or "unknown")
        message = kwargs.get("user_message") or ""
        if isinstance(message, (list, dict)):
            message = json.dumps(message, ensure_ascii=False)
        message = str(message)

        # System-generated turns carry quoted commands, so their bodies contain
        # whatever research words the original command had. They are never
        # research requests, and injecting a protocol for one is noise.
        #
        # pre_tool_call cannot see the user message, so the verdict is recorded
        # here, scoped to this turn, and read back by the search guard.
        turn_id = str(kwargs.get("turn_id") or "")
        synthetic = _classify.is_synthetic(message)

        # Read the previous turn's activity before the turn id moves on: the
        # tool activity of the turn that just ended is what says whether it
        # left evidence unaccounted for.
        previous_activity = _state.previous_turn_activity(session_id, turn_id)

        _state.mark_turn(session_id, turn_id=turn_id, synthetic=synthetic)
        if synthetic:
            return None
        # Count the turn against the pending review. A drift that takes
        # several turns to appear is not visible in any one of them.
        _prev = _state.read_session(session_id)
        _topic = str(_prev.get("topic") or "")
        if _topic and _prev.get("mode") == "research":
            _state.note_turn_since_review(_topic)

        s = _state.read_session(session_id)
        mode = s.get("mode", "idle")
        topic = s.get("topic", "")

        # The user asked to leave research mode. Turn it off here — the agent
        # cannot be relied on to call the tool for its own exit — and inject
        # nothing: leaving should be silent.
        if mode == "research" and _classify.is_exit_request(message):
            _state.set_session_mode(session_id, "idle", clear_topic=True)
            return None

        # Re-classify every turn: a chore inside a research session must not
        # carry the protocol, and a research question in an idle session should
        # still get a nudge (never an automatic switch — the agent decides).
        detected = _classify.classify(message, s)

        if detected == "chore" and mode == "research":
            _state.set_session_mode(session_id, "chore", topic)
            mode, topic = "chore", topic

        ctx: str | None = None
        if mode == "research" and topic and _state.topic_exists(topic):
            ctx = _protocol.build_research_block(topic, cfg)
            # The last turn gathered evidence and recorded nothing. Say so
            # before the agent builds the next answer on top of it.
            # The turn that just ended may have moved, or may have drifted.
            # A note that says "you did not do X" pushes the agent into
            # completing the form, which is the failure this project is
            # against. What helps is a direction: where the project is, and
            # what this phase is for.
            directed = _direction_note(topic)
            if directed:
                ctx = f"{ctx}\n\n{directed}"
            # Close the loop: did the last turn advance the question, and
            # is more input still worth it.
            cycle = _cycle_check(topic, previous_activity)
            if cycle:
                ctx = f"{ctx}\n\n{cycle}"
            # A paused topic says so, and says what to do instead.
            demand = _protocol.review_demand(topic)
            if demand:
                ctx = f"{ctx}\n\n" + "\n".join(demand)
            # The map has competing explanations but nothing separating them,
            # or has judgments but no record of what is still open. The
            # protocol block is capped, so this goes in only when there is
            # actually a gap to point at.
            missing = _map_gaps(topic)
            if missing:
                ctx = (
                    f"{ctx}\n\n[Research OS · 研究地图的缺口]\n"
                    + "\n".join(missing)
                )
        elif detected == "research":
            # A research question arrived while idle. The model will reach for
            # PubMed / web_search if nothing says otherwise, so the nudge has
            # to be explicit about the order of operations: open the topic
            # first, then retrieve. It still does not switch the mode itself —
            # the agent calls the tool, so the decision stays with the agent.
            topics = _state.list_topics()
            if topics:
                names = ", ".join(t["name"] for t in topics)
                ctx = (
                    "[Research OS · 需要先进入课题]\n"
                    "这轮是研究问题。检索前先调 research_mode(action='on', project='<课题>') "
                    "打开对应课题——这样才能带上该课题的核心问题、竞争解释和证据账本，"
                    "回答时按四类断言（observation/report/inference/hypothesis）标注，"
                    "并把证据记进账本。\n"
                    f"可用课题：{names}"
                )
            else:
                ctx = (
                    "[Research OS · 需要先建立课题]\n"
                    "这轮是研究问题。先调 research_mode(action='create', project='<名字>', "
                    "core_question='<这个课题要解决什么科学问题>') 建立课题——"
                    "说不出核心问题就别开始检索。建好后调 action='on' 进入，"
                    "再按四类断言（observation/report/inference/hypothesis）回答并记账。"
                )

        if not ctx:
            return None
        return {"context": ctx}
    except Exception:
        return None


def _map_gaps(topic: str) -> list[str]:
    """What the research map is missing, if anything.

    A topic with competing explanations but no recorded contradiction has not
    said what would tell them apart — which is the question the whole design
    is about. A topic with judgments but no open questions has not said where
    it is still unsure, and the next session will read the record as if it
    were complete.
    """
    try:
        m = _state.read_map(topic)
        hyp = [h for h in (m.get("hypotheses") or []) if str(h).strip()]
        open_q = [q for q in (m.get("open_questions") or []) if str(q).strip()]
        claims = _state.read_claims(topic)
        has_judgment = any(c.get("kind") == "inference" for c in claims)
        items = (_state.read_contradictions(topic) or {}).get("items") or []

        wants = []
        # A topic with no standing has no direction. The agent is asked to
        # say where it is going, not told to fill in a form: the question is
        # the thing that makes it think, and the answer is what the tool
        # then records.
        if not str(m.get("goal") or "").strip() or not str(m.get("current_task") or "").strip():
            wants.append(
                f"这个课题还没有方向：说清它要弄清什么（goal）、"
                f"现在正在弄的那一块是什么（current_task）、"
                f"以及现在处在哪一步（phase：{'/'.join(_protocol.PHASES)}）。"
                f"用 research_mode(action='set_state', project='{topic}', ...) 记下来。"
                f"没定方向就往下做，是这个课题最后变成一堆材料的唯一原因。"
            )
        if len(hyp) >= 2 and not items:
            wants.append(
                f"有 {len(hyp)} 个竞争解释，但没记下什么证据能区分它们。"
                f"这不是记账问题——是这几个解释现在没法互相淘汰，"
                f"所以没有下一步可做。说出哪一个观察能分开它们。"
            )
        if has_judgment and not open_q:
            wants.append(
                f"已经下了判断，但没记下哪里还悬着——"
                f"说清这个判断在什么地方会不成立。"
            )
        return wants
    except Exception:
        return []


# The cycle check: what this turn actually did, against what the topic is
# for.
#
# The question it answers is the one a researcher asks at the end of a
# stretch of work: did this advance the thing we are trying to find out, or
# did it accumulate material around a question we have moved past? A
# session can search forty times, produce a full ledger, and have gone
# nowhere - the evidence is there and the direction is gone.
#
# The signals are countable, not felt:
#   - the work added claims but no explanation and no separating evidence
#   - the searches went to a different sub-question than the current task
#   - a lot came in and nothing was decided
#
# What it produces is a verdict the agent has to answer to, not a note it
# can nod at.
def _review_due(topic: str) -> str | None:
    """Say that a review is due, at the point where it can still change
    what happens.

    The counter is per topic, not per turn: a drift that takes three turns
    to appear is not visible in any one of them, so the review has to look
    at the topic rather than at the turn. The check sits at the search
    boundary because that is where "stop and look at where you are" has
    teeth - after the search it is only a note about the past.
    """
    try:
        review = _state.read_review(topic)
        if review.get("paused"):
            return None
        searches = int(review.get("searches_since_review") or 0)
        turns = int(review.get("turns_since_review") or 0)
        every_searches, every_turns = _state.review_thresholds(topic)
        if searches < every_searches and turns < every_turns:
            return None
        m = _state.read_map(topic)
        narrative = str(m.get("narrative") or "").strip()
        core = str(m.get("core_question") or "").strip()
        return (
            f"先停一下，做一次全局审查再继续检索。\n"
            f"这个课题已经检索 {searches} 次、进行了 {turns} 轮工作。"
            f"要判断三件事：\n"
            f"（a）这些材料有没有改变原来的判断"
            f"（现在记着的判断是：{narrative[:80] if narrative else '还没记下判断'}）？\n"
            f"（b）现在的方向还服务核心问题（{core[:60]}）吗？\n"
            f"（c）继续检索还值得吗，还是该收手？\n"
            f"用 research_mode(action='review', project='{topic}', "
            f"verdict='continue|redirect|pause', review_note='<理由>') 记下结论，"
            f"判 redirect/pause 就同时用 action='set_state' 更新叙事和计划。"
            f"审查完再检索。"
        )
    except Exception:
        return None


def _cycle_check(topic: str, activity: dict[str, Any]) -> str | None:
    """Ask the agent to close the loop, when the loop looks open.

    Returns the text to inject, or None when there is nothing to ask.
    """
    try:
        m = _state.read_map(topic)
        core = str(m.get("core_question") or "").strip()
        task = str(m.get("current_task") or "").strip()
        narrative = str(m.get("narrative") or "").strip()
        if not core and not task:
            return None

        evidence = int(activity.get("evidence") or 0)
        moves = int(activity.get("moves") or 0)
        claims = int(activity.get("ledger") or 0)

        lines: list[str] = []

        # A lot came in and nothing was decided. Material without a
        # decision is the shape of a report, and it is the state the
        # narrative is supposed to prevent.
        if evidence >= 3 and moves == 0 and not narrative:
            lines.append(
                f"这一轮检索了 {evidence} 次，但没有推进任何解释，也没有写下现在"
                f"怎么看这个课题。材料越堆越多而判断没有形成，"
                f"课题就会变成一份文献清单。"
                f"先说清目前认为什么、哪些解释还活着，再继续找。"
            )

        # The work went somewhere other than the question.
        #
        # The anchor is the core question, not the current task. An earlier
        # version compared the claims against the current task by word
        # overlap, and that was wrong twice over: the task is the narrowest
        # level and legitimately uses different words from the question, and
        # a Chinese task shares almost no whitespace-delimited words with
        # any claim, so every claim looked like it was off topic.
        #
        # The core question is what the project is about, so a claim that
        # shares nothing with it is worth a look. The signal is one-sided:
        # no overlap means "check this", not "you have drifted".
        if core and claims >= 4:
            core_terms = _content_terms(core)
            recent = _state.read_claims(topic)[-claims:]
            if core_terms:
                off = 0
                for c in recent:
                    terms = _content_terms(str(c.get("content") or ""))
                    if terms and not (terms & core_terms):
                        off += 1
                # Half of them, and at least three, before it is worth
                # saying: a topic legitimately holds evidence from several
                # vocabularies.
                if off >= 3 and off * 2 >= len(recent):
                    lines.append(
                        f"这一轮有 {off} 条断言跟核心问题「{core[:40]}」几乎不搭词。"
                        f"先确认：现在做的这件事，还是这个课题要的吗？"
                        f"如果当前任务本身已经不对，改任务或改阶段，别继续往这个方向堆。"
                    )

        # A review is due by count. A drift that takes several turns to
        # appear is not visible in any one of them.
        review = _state.read_review(topic)
        if not review.get("paused"):
            searches = int(review.get("searches_since_review") or 0)
            turns = int(review.get("turns_since_review") or 0)
            every_searches, every_turns = _state.review_thresholds(topic)
            if searches >= every_searches or turns >= every_turns:
                lines.append(
                    f"这个课题已经积累了 {searches} 次检索、{turns} 轮工作，"
                    f"该做一次全局审查了："
                    f"（a）这些材料有没有改变原来的判断？"
                    f"（b）现在的方向还服务核心问题吗？"
                    f"（c）继续输入还值得吗，还是该收手？"
                    f"用 research_mode(action='review', project='{topic}', "
                    f"verdict='continue|redirect|pause', review_note='<理由>') 记下来，"
                    f"判 redirect/pause 就同时用 action='set_state' 更新叙事和计划。"
                )
                lines.append(
                    "现在就做这次审查——不用等下一轮。审查要给出判断，"
                    "不是把材料再列一遍。"
                )

        if not lines:
            return None
        return (
            "[Research OS · 这一轮要收口]\n" + "\n".join(lines)
        )
    except Exception:
        return None


_STOPWORDS = {
    "的", "了", "和", "与", "在", "是", "有", "对", "从", "到", "被", "把",
    "而", "及", "或", "这", "那", "个", "们", "什么", "怎么", "如何", "为什么",
    "the", "a", "an", "of", "in", "to", "and", "or", "is", "are", "for", "on",
    "with", "by", "that", "this", "it", "be", "as", "at", "from",
}


def _content_terms(text: str) -> set[str]:
    """The content words of a piece of text, for a rough overlap check."""
    out: set[str] = set()
    for raw in str(text or "").split():
        w = raw.strip("\uFF0C\u3002\uFF1B\uFF1A\u3001\uFF1F\uFF01\uFF08\uFF09()[]{}.,;:?!-\u2014\u2026\u00b7").lower().strip(chr(34) + chr(39) + chr(96))
        if len(w) < 2 or w in _STOPWORDS:
            continue
        out.add(w)
    # Chinese has no spaces, so fall back to character bigrams.
    cjk = "".join(ch for ch in str(text or "") if "\u4e00" <= ch <= "\u9fff")
    if cjk:
        for i in range(len(cjk) - 1):
            bg = cjk[i:i + 2]
            if bg not in _STOPWORDS:
                out.add(bg)
    return out


def _direction_note(topic: str) -> str:
    """Where the project is and what this phase is for.

    This replaces the two notes that used to be here. One said "you
    searched but recorded nothing", the other said "you did not move the
    question". Both were accurate and both pushed the agent into completing
    a form - which is the failure this project exists to prevent. A
    research session that stops moving is not helped by being told it
    stopped moving; it is helped by being told what the phase is for.
    """
    try:
        m = _state.read_map(topic)
        phase = str(m.get("phase") or "").strip()
        task = str(m.get("current_task") or "").strip()
        if not phase and not task:
            return ""
        bits = ["[Research OS · 这轮的方向]"]
        if task:
            bits.append(f"当前任务：{task}")
        shape = _protocol.phase_output(phase) if phase else ""
        if shape:
            bits.append(f"这个阶段要交出的是：{shape}")
        return "\n".join(bits)
    except Exception:
        return ""


# Tools that constitute a literature search. Reaching for one of these while
# not in research mode is the moment the model is about to gather evidence
# without a question, a hypothesis set, or a ledger to put it in.
SEARCH_TOOLS = (
    "mcp__pubmed__pubmed_search_articles",
    "mcp__pubmed__pubmed_fetch_articles",
    "mcp__pubmed__pubmed_europepmc_search",
    "mcp__pubmed__pubmed_fetch_fulltext",
    "mcp__pubmed_search_mcp__unified_search",
    "mcp__pubmed_search_mcp__get_fulltext",
    "mcp__openalex__openalex_search_entities",
    "mcp__openalex__openalex_get_citation_graph",
    "web_search",
    "web_extract",
)


def _state_write_guard(**kwargs: Any) -> dict[str, Any] | None:
    """Keep the state tree behind the tool.

    The ledger only works if every claim goes through the tool: the tool
    checks the kind, requires a source for observations, enforces that an
    inference does not exceed its weakest premise, and refuses a duplicate.
    An agent that writes claims.tsv directly bypasses all of it, and a
    ledger that can be corrupted by a text file is not a ledger.

    Reading the state tree is fine — that is how the agent finds out what is
    already there. Writing it is not.
    """
    try:
        tool_name = str(kwargs.get("tool_name") or "")
        args = kwargs.get("args") or {}
        if not isinstance(args, dict):
            return None

        if tool_name in ("write_file", "patch"):
            target = str(args.get("path") or args.get("file_path") or "")
            if not _is_state_path(target):
                return None
        elif tool_name in ("terminal", "execute_code"):
            if not _mentions_state_tree(_command_text(args)):
                return None
        else:
            return None

        return {
            "action": "block",
            "message": (
                "[Research OS · 账本只能走 research_mode]\n"
                "账本和研究地图由 research_mode 工具维护。直接写文件、或者"
                "调插件自己的 state 模块，都会绕过类型检查、来源要求、"
                "推论强度校验和去重——那样账本就不再可信。\n"
                "\n"
                "research_mode 是可以直接调用的工具（不是 tool_call 里的）。\n"
                "记一条：research_mode(action='claim', project='<课题>', "
                "claim={'kind':'observation','content':'...','source':'PMID ...'})\n"
                "记一批：research_mode(action='claim', project='<课题>', "
                "claims=[{'kind':'observation','content':'...','source':'PMID ...'}, "
                "{'kind':'report','content':'...','source':'PMID ...'}])"
                "——一次检索出来几条就一次记完，不用一条一个调用。\n"
                "记竞争解释：research_mode(action='contradict', project='<课题>', "
                "title='<哪个解释>', separator='<什么证据能区分>')"
            ),
        }
    except Exception:
        return None


def _command_text(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    return " ".join(str(args.get(k) or "") for k in ("command", "code", "script"))


def _is_state_path(path: str) -> bool:
    try:
        target = Path(str(path)).expanduser()
        try:
            target = target.resolve()
        except OSError:
            pass
        home = _state.HOME.resolve()
    except Exception:
        return False
    try:
        target.relative_to(home)
        return True
    except ValueError:
        return False


# The state module's own write functions. A command that imports the
# plugin's state module and calls one of these writes the state tree
# without ever naming its path - the module knows where it lives. A live
# run did exactly this, and the path-based guard let it through, so the
# ledger was written outside the tool.
#
# The check has to be on the call, not only on the path.
_STATE_WRITE_CALLS = (
    "append_claim", "write_claims", "write_map", "write_state",
    "append_contradiction", "write_contradictions", "write_review",
    "note_search_since_review", "note_turn_since_review", "record_review",
    "note_topic_reminder", "write_claims_bulk", "set_topic_paused",
    "clear_pause", "delete_topic",
)

# The names that mean "this command is reaching for the state module".
_STATE_IMPORT_HINTS = (
    "import state", "from state import", "research_os import state",
    "research_os.state", "import research_os", "plugins/research-os",
    "research_os/", "from research_os import",
)


def _calls_state_writer(text: str) -> bool:
    """True when a command reaches the state module's writer by API.

    Two parts have to be present: it pulls in the state module, and it
    calls something that writes. Importing the module to read it is
    allowed - reading the state is how the agent finds out what is
    already recorded.

    A call has to look like a call. A grep for a writer's name in a
    source file is not a write, and the first version of this check
    fired on it - which blocked a command that was only looking.
    """
    if not text:
        return False
    low = text.lower()
    if not any(h in low for h in _STATE_IMPORT_HINTS):
        return False
    for call in _STATE_WRITE_CALLS:
        # "S.append_claim(" or "state.append_claim(" - a call, not a
        # mention inside a search pattern.
        for m in re.finditer(re.escape(call), text):
            tail = text[m.end(): m.end() + 40]
            if tail.lstrip().startswith("("):
                return True
    return False


def _mentions_state_tree(text: str) -> bool:
    """True when a command writes to the state tree.

    Reading the state is how the agent finds out what is already recorded, so
    a read must not be blocked. Only a write is.

    The check is on the write target, not on whether the path appears
    anywhere: a command that reads the ledger and writes a summary to
    /tmp mentions the state tree and is not touching it.

    And it covers the API route, not only the path: a command that imports
    the state module and calls its writer never names the path, and the
    path-based check alone let a live run write the ledger outside the
    tool.
    """
    if not text:
        return False
    if _calls_state_writer(text):
        return True
    for span in _state_tree_spans(text):
        if _is_write_target(text, span):
            return True
    return False


def _state_tree_spans(text: str) -> list[tuple[int, int]]:
    """Where the state tree is named in the text."""
    spans: list[tuple[int, int]] = []
    try:
        home = str(_state.HOME.resolve())
    except Exception:
        home = ""
    lowered = text.lower()
    if home:
        needle = home.lower()
        start = 0
        while True:
            idx = lowered.find(needle, start)
            if idx < 0:
                break
            spans.append((idx, idx + len(needle)))
            start = idx + 1
    for marker in _STATE_TREE_MARKERS:
        needle = marker.lower()
        start = 0
        while True:
            idx = lowered.find(needle, start)
            if idx < 0:
                break
            spans.append((idx, idx + len(needle)))
            start = idx + 1
    # Merge overlaps so a path that matches both the home and a marker is
    # examined once.
    spans.sort()
    merged: list[tuple[int, int]] = []
    for begin, end in spans:
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((begin, end))
    return merged


_WRITE_AFTER = (
    "'w'", '"w"', "'a'", '"a"', "'w+'", '"w+"', "'a+'", '"a+"',
    "'x'", '"x"', "'wb'", '"wb"', "'ab'", '"ab"',
    "write_text", "write_bytes", "writelines", "writerow", "dump(",
)
# Operations that move or copy: the state tree path may be the source, so
# these are treated as writes when the tree is named at all.
_MOVING_OPS = ("cp ", "mv ", "rsync ", "install ", "scp ")


def _is_write_target(text: str, span: tuple[int, int]) -> bool:
    begin, end = span
    # The window before the path must not cross a heredoc body: a command
    # that writes a script to /tmp and quotes a state path inside it is
    # writing to /tmp.
    before = text[max(0, begin - 200):begin].lower()
    # Only the current line matters: a heredoc that started on an earlier
    # line has its body on the lines after it, and the path is inside that
    # body rather than a redirect target.
    line = before.rsplit("\n", 1)[-1]
    if "<<" in line:
        return False
    after = text[end:end + 34].lower()
    # A write mode after the path only counts when it is on the same
    # statement — the same rule as for the window before.
    if "\n" in after:
        after = after.split("\n", 1)[0]

    # A redirect counts only when the path is what it redirects to. The
    # operator has to be immediately to the left of the path: `2>&1` and
    # `> /dev/null` are everywhere and mean nothing here.
    stripped = line.rstrip()
    for redirect in (">>", ">"):
        if stripped.endswith(redirect):
            return True
    for op in ("tee ", "sed -i", "truncate ", "dd ", "shred ", "install ",
               "rm ", "unlink"):
        if stripped.endswith(op.rstrip()) or (" " + op) in line:
            return True
    for marker in _WRITE_AFTER:
        if marker in after:
            return True
    for op in _MOVING_OPS:
        if op in line:
            return True
    return False


# The path fragments that name the state tree.
_STATE_TREE_MARKERS = (
    "research-os" + "/topics",
    "research-os" + "/sessions",
    "research-os" + "/config.json",
    "research-os" + "/projects.json",
)

# Shell redirections and Python's write modes. Read verbs are deliberately
# absent: `cat`, `ls`, `head`, `grep` and opening a file for reading are how
# the agent looks at what is already there.
_WRITE_MARKERS = (
    ">>", "tee ", "dd ", "truncate", "sed -i", "shred ",
    "writerow", "writelines", "write_text", "write_bytes",
    "os.remove", "os.rename", "os.replace", "os.mkdir", "os.makedirs",
)


def _convergence_check(
    session_id: str,
    topic: str,
    review_due: str | None = None,
) -> dict[str, Any] | None:
    """Interrupt a search run that is not converging.

    A live run searched fifty times across three turns, recorded nothing and
    never produced an answer. The counter is per topic, because that is how
    the run actually accumulated.

    The check interrupts, it does not forbid: the agent is asked to record
    what it has or to say what it will conclude, and then it may search again.
    It interrupts at most twice per topic — after that it lets the work
    through, because a hook that keeps refusing the same tool is a stall, and
    a stall is worse than a long search.
    """
    try:
        cfg = _state.load_config()
        threshold = int(cfg.get("searches_before_converge") or 0)
        if threshold <= 0:
            return None
        count = _state.topic_search_count(topic)
        # The review is due earlier than the convergence check and it is the
        # more important of the two: a run that stops searching because it
        # was interrupted is a run that gave up, and a run that reviews where
        # it is has decided something.
        if count < threshold:
            if review_due is None:
                return None
            # The review is due before the search count is. It is the more
            # important of the two: a run that stops searching because it
            # was interrupted has given up, and a run that reviews where it
            # is has decided something.
            demanded = _state.note_review_demand(topic)
            if demanded <= 2:
                return {"action": "block", "message": review_due}
            return None
        reminded = _state.topic_search_reminded(topic)
        if reminded >= 2:
            return None

        claims = _state.read_claims(topic)
        _state.note_topic_reminder(topic)

        if claims:
            # Show what is already recorded. Without this the agent has no way
            # to know what it wrote earlier, and it records the same findings
            # again — a live run did exactly that, duplicating five rows.
            listed = "\n".join(
                f"- {c.get('id')} [{c.get('kind')}] "
                f"{str(c.get('content') or '')[:110]}"
                for c in claims[-8:]
            )
            body = (
                f"这个课题已经检索了 {count} 次，账本里有 {len(claims)} 条断言：\n"
                f"{listed}\n"
                f"（只列了最后 8 条）\n"
                f"已经记过的不要再记一遍。现在该收口了：就账本里这些证据给出判断——"
                f"你认为什么、依据哪几条、什么还不确定——"
                f"而不是再找一篇让清单更长。"
            )
        else:
            body = (
                f"这个课题已经检索了 {count} 次，但账本还是空的，回答也还没写出来。\n"
                f"先停下来：就手上已经找到的东西给出判断，说明哪些地方证据不足。"
                f"继续检索不会让答案自己出现。"
            )
        if review_due:
            body = f"{review_due}\n\n{body}"
        return {
            "action": "block",
            "message": f"[Research OS · 该收口了]\n{body}",
        }
    except Exception:
        return None


def _pre_tool_call(**kwargs: Any) -> dict[str, Any] | None:
    """Hold a literature search until the agent has a topic to put it in.

    This is the load-bearing hook. A code-specialised model reaches for
    PubMed before it thinks about the question, because retrieval is the
    shortest path to an answer. Blocking here — once, with a message that
    says what to do instead — is what turns "go search" into "open the topic
    first, then search".
    """
    try:
        tool_name = str(kwargs.get("tool_name") or "")
        if tool_name not in SEARCH_TOOLS:
            return None

        session_id = str(kwargs.get("session_id") or "unknown")
        s = _state.read_session(session_id)
        if s.get("mode") == "research" and s.get("topic"):
            topic = str(s.get("topic") or "")
            # A review that paused the topic stops the searching. Not as a
            # warning - as a block - until the state says where the work is
            # going. A review that cannot stop anything is a note in a
            # margin.
            if _state.topic_is_paused(topic):
                review = _state.read_review(topic)
                return {
                    "action": "block",
                    "message": (
                        f"这个课题现在暂停检索：上一次全局审查判定工作已经偏离核心问题"
                        f"（理由：{review.get('last_reason') or '未记录'}）。\n"
                        f"继续往这个方向搜只会让材料更多、判断更少。\n"
                        f"先用 research_mode(action='set_state', project='{topic}', "
                        f"narrative='<现在认为什么>', plan='<下一步做什么>') "
                        f"更新课题叙事和计划（或 action='review' 判 'continue' 继续），"
                        f"再检索。"
                    ),
                }
            # The topic is open. A run that searches forever and never answers
            # is the failure this catches: interrupt, ask for a record or a
            # conclusion, then let the work continue.
            #
            # The review rides along with this interrupt rather than blocking
            # on its own. A review the agent can ignore is a suggestion, and
            # a hard block on every search past a count would stop a run
            # that was about to conclude - which is the opposite of what the
            # count is for.
            due = _review_due(topic)
            return _convergence_check(session_id, topic, review_due=due)

        # pre_tool_call cannot see the user message or the conversation. The
        # turn's synthetic/not verdict was recorded by pre_llm_call, which runs
        # earlier in the same turn, and is read back here. If the turn id does
        # not match, the verdict is stale and must not be trusted.
        if _state.turn_is_synthetic(session_id, str(kwargs.get("turn_id") or "")):
            return None

        topics = _state.list_topics()
        if topics:
            names = ", ".join(t["name"] for t in topics)
            msg = (
                "先别检索。这轮看起来是研究问题，但还没进入课题——"
                "没有核心问题就直接搜文献，得到的只是一堆没有上下文的条目。\n"
                "先调 research_mode(action='on', project='<课题>') 打开对应课题，"
                "再重新检索。\n"
                f"可用课题：{names}"
            )
        else:
            msg = (
                "先别检索。还没有任何课题。\n"
                "先调 research_mode(action='create', project='<名字>', "
                "core_question='<这个课题要解决什么科学问题>') 建立课题——"
                "说不出核心问题就别开始搜。建好后调 action='on' 进入，再检索。"
            )
        return {"action": "block", "message": msg}
    except Exception:
        return None


def _post_tool_call(**kwargs: Any) -> None:
    """Track what the turn did with evidence.

    pre_llm_call fires once, at the start of a turn, so it cannot see the
    tool results the current turn produced. This hook sees every tool call as
    it happens, and records enough to answer the only question that matters
    at the next turn: did the turn gather evidence and record nothing?
    """
    try:
        tool_name = str(kwargs.get("tool_name") or "")
        session_id = str(kwargs.get("session_id") or "unknown")
        turn_id = str(kwargs.get("turn_id") or "")
        if not tool_name:
            return None
        # Only a claim write counts as recording. Creating a topic or opening
        # one is not the same act, and counting them would make a turn that
        # never wrote a claim look accounted for.
        is_ledger = tool_name == "research_mode" and _is_claim_call(kwargs.get("args"))
        # A research move is a write that changes what the topic knows, as
        # opposed to a write that adds a fact to the ledger. Recording a
        # report is a move only if it settles or shifts something. The
        # actions that move a question forward are: a new or sharpened
        # explanation, the evidence that separates two of them, a closed
        # question, and a hypothesis (an untested idea is itself a move).
        is_move = tool_name == "research_mode" and _is_move_call(kwargs.get("args"))
        _state.note_tool_activity(
            session_id,
            turn_id=turn_id,
            tool_name=tool_name,
            is_evidence=tool_name in _audit.EVIDENCE_TOOLS,
            is_ledger=is_ledger,
            is_move=is_move,
        )
        if tool_name in _audit.EVIDENCE_TOOLS:
            s = _state.read_session(session_id)
            topic = str(s.get("topic") or "")
            if topic and s.get("mode") == "research":
                _state.note_topic_search(topic, tool_name)
                _state.note_search_since_review(topic)
        return None
    except Exception:
        return None


def _is_claim_call(args: Any) -> bool:
    """True when a research_mode call actually wrote a claim."""
    if not isinstance(args, dict):
        return False
    action = str(args.get("action") or "").lower()
    if action != "claim":
        return False
    claim = args.get("claim")
    return isinstance(claim, dict) and bool(claim.get("content"))


def _is_move_call(args: Any) -> bool:
    """True when a research_mode call moved the question forward.

    Not every write is a move. Adding a report to the ledger records what is
    known; proposing a new explanation, saying what would tell two
    explanations apart, or closing a question changes what the topic is
    about. A hypothesis counts - putting an untested idea on the table is
    what research does.
    """
    if not isinstance(args, dict):
        return False
    action = str(args.get("action") or "").lower()
    if action in ("contradict", "question", "hypothesis"):
        return True
    if action == "claim":
        batch = args.get("claims")
        if isinstance(batch, list) and batch:
            # A batch counts as a move when it carries a hypothesis or an
            # inference - the same rule as a single claim.
            for one in batch:
                if not isinstance(one, dict):
                    continue
                kind = str(one.get("kind") or "").lower()
                if kind == "hypothesis":
                    return True
                if kind == "inference" and one.get("premises"):
                    return True
            return False
        claim = args.get("claim")
        if not isinstance(claim, dict):
            return False
        kind = str(claim.get("kind") or "").lower()
        if kind == "hypothesis":
            return True
        # An inference is a move when it draws on the ledger: it is the
        # agent's own reasoning, not a transcription. A report is a move
        # only when it comes with the reading that settles something.
        if kind == "inference":
            return bool(claim.get("premises"))
        if kind == "report":
            return bool(claim.get("implies") or claim.get("settles"))
    return False


def _transform_llm_output(**kwargs: Any) -> str | None:
    """Check the outgoing answer against the ledger.

    This is the only hook that sees the text the user is about to read, and
    it fires before the answer is stored, so a correction here still reaches
    the user. The failure it catches: the answer asserts something that was
    never classified, so nothing ever checked whether it was an observation
    or an inference.

    The answer is not rewritten. A note is appended when the answer contains
    an unaccounted assertion, because silently editing the agent's prose
    would hide the very thing the ledger exists to surface.
    """
    try:
        response = str(kwargs.get("response_text") or "")
        if not response.strip():
            return None
        session_id = str(kwargs.get("session_id") or "unknown")

        s = _state.read_session(session_id)
        if s.get("mode") != "research":
            return None
        topic = s.get("topic") or ""
        if not topic or not _state.topic_exists(topic):
            return None

        # What this hook checks is the shape of the answer, not the
        # bookkeeping. It used to list every sentence that was not in the
        # ledger, which produced exactly the thing this project is against:
        # a report of what was said and not said, in place of a position.
        # The ledger still holds the claims; the answer has to carry a
        # judgment.
        if not _audit.has_position(response):
            return (
                f"{response}\n\n"
                f"---\n"
                f"*[Research OS] 这一轮给的是材料，没给判断。"
                f"读者要自己合成，而合成正是最该有人做的一步。"
                f"补一句你目前认为什么、依据哪几条、什么会改变它。*"
            )
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def register(ctx: Any) -> None:
    ctx.register_tool(
        name="research_mode",
        toolset="research_os",
        schema=TOOL_SCHEMA,
        handler=_tool_handler,
        description="Research mode switch and topic manager.",
        emoji="🔬",
    )
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("pre_tool_call", _state_write_guard)
    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_hook("transform_llm_output", _transform_llm_output)
