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
        "(on_track | drifting | off_track) and the reason. A project can drift without any single "
        "claim being wrong, so the alignment is recorded explicitly. "
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
                    "delete", "claim", "validate", "repair",
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
            "verdict": {
                "type": "string",
                "description": (
                    "For action 'review': on_track | drifting | off_track. Whether "
                    "the work is still serving the core question."
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
            return ok({
                "mode": s.get("mode", "idle"),
                "topic": s.get("topic", ""),
                "topics": [t["name"] for t in topics],
            })

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
            if goal is None and phase is None and task is None:
                return fail(
                    "give at least one of: goal (what this project is trying "
                    "to get at), phase (scoping/evidence/weighing/closing/"
                    "writing), current_task (what is being done right now)"
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
            )
            if phase is not None and str(phase).strip() != before:
                _state.record_phase_change(
                    project, before, str(phase).strip(), str(args.get("note") or "")
                )
            return ok(
                {
                    "goal": m.get("goal"),
                    "phase": m.get("phase"),
                    "current_task": m.get("current_task"),
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
                    "verdict is required: on_track | drifting | off_track"
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
            return ok({"review": entry, "reviews": len(m["reviews"])})

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
                "[Research OS · 别直接改状态树]\n"
                "账本和研究地图由 research_mode 工具维护。直接写文件会绕过"
                "类型检查、来源要求、推论强度校验和去重——那样账本就不再可信。\n"
                "要记一条断言：research_mode(action='claim', project='<课题>', "
                "claim={'kind':'observation','content':'...','source':'PMID ...'})\n"
                "要记竞争解释：research_mode(action='contradict', project='<课题>', "
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


def _mentions_state_tree(text: str) -> bool:
    """True when a command writes to the state tree.

    Reading the state is how the agent finds out what is already recorded, so
    a read must not be blocked. Only a write is.

    The check is on the write target, not on whether the path appears
    anywhere: a command that reads the ledger and writes a summary to
    /tmp mentions the state tree and is not touching it.
    """
    if not text:
        return False
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


def _convergence_check(session_id: str, topic: str) -> dict[str, Any] | None:
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
        if count < threshold:
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
                f"先看这个清单：已经记过的不要再记一遍，重复的记录不算新的证据。"
                f"缺什么就补什么——缺的用 research_mode(action='claim') 记下来"
                f"（观察和转述填 source，推论填 premises 和 inference_type），"
                f"然后用现有证据给出结论，不要为了再找一篇而无限检索。"
            )
        else:
            body = (
                f"这个课题已经检索了 {count} 次，但账本还是空的，回答也还没写出来。\n"
                f"先停下来：用 research_mode(action='claim') 记下你已经找到的关键证据，"
                f"或者直接就现有证据给出结论并说明哪些地方证据不足。"
                f"继续检索不会让答案自己出现。"
            )
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
            # The topic is open. A run that searches forever and never answers
            # is the failure this catches: interrupt, ask for a record or a
            # conclusion, then let the work continue.
            return _convergence_check(session_id, str(s.get("topic") or ""))

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
