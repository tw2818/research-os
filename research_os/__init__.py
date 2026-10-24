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
        spec = importlib.util.spec_from_file_location(full, _HERE / f"{short}.py")
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


try:
    _audit, _classify, _protocol, _state, _validate = _load_impl_modules()
except Exception:  # pragma: no cover - package layout (repo / tests)
    from . import audit as _audit  # type: ignore[no-redef]
    from . import classify as _classify  # type: ignore[no-redef]
    from . import protocol as _protocol  # type: ignore[no-redef]
    from . import state as _state  # type: ignore[no-redef]
    from . import validate as _validate  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# tool implementation
# ---------------------------------------------------------------------------

TOOL_SCHEMA: dict[str, Any] = {
    "name": "research_mode",
    "description": (
        "Research mode switch and topic manager. CALL THIS BEFORE answering a "
        "research question or before running a literature search. "
        "ACTION 'on'/'switch' enter or switch topic (needs an existing topic name). "
        "ACTION 'off' leaves research mode. ACTION 'list' lists topics. "
        "ACTION 'create' makes a new topic (needs project + core_question — a topic "
        "without a scientific question is not a research topic). "
        "ACTION 'delete' removes a topic (soft delete by default). "
        "ACTION 'repair' rebuilds missing topic state. "
        "ACTION 'claim' records a claim in the topic ledger (kind: observation/report/"
        "inference/hypothesis) and ACTION 'validate' checks the ledger. "
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
                    "source": {"type": "string", "description": "PMID/DOI/paper (observation/report)."},
                    "status": {"type": "string"},
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
            _state.set_session_mode(session_id, "research", project)
            m = _state.read_map(project)
            hint = ""
            if previous and previous != project:
                hint = (
                    f" Switched from {previous} to {project}. Do not carry hypotheses "
                    f"or claims over from {previous}."
                )
            return ok({
                "mode": "research",
                "topic": project,
                "core_question": m.get("core_question", ""),
                "hypotheses": m.get("hypotheses", []),
                "note": hint.strip(),
            })

        if action == "off":
            _state.set_session_mode(session_id, "idle")
            return ok({"mode": "idle", "topic": ""})

        if action == "claim":
            if not project:
                return fail("project is required")
            claim = dict(args.get("claim") or {})
            if not claim.get("content"):
                return fail("claim.content is required")
            claim["premises"] = " ".join(_split_list(claim.get("premises"), sep=" "))
            row = _state.append_claim(project, claim)
            return ok({"recorded": row})

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
        _state.mark_turn(session_id, turn_id=turn_id, synthetic=synthetic)
        if synthetic:
            return None

        s = _state.read_session(session_id)
        mode = s.get("mode", "idle")
        topic = s.get("topic", "")

        # Re-classify every turn: a chore inside a research session must not
        # carry the protocol, and a research question in an idle session should
        # still get a nudge (never an automatic switch — the agent decides).
        detected = _classify.classify(message, s)

        if detected == "chore" and mode == "research":
            _state.set_session_mode(session_id, "chore", topic)
            mode, topic = "chore", topic

        if mode == "research" and topic and _state.topic_exists(topic):
            ctx = _protocol.build_research_block(topic, cfg)
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
        else:
            return None

        # The turn is in research mode. Has it gathered evidence it has not
        # accounted for? This is checked here, before the agent writes its
        # answer, because afterwards the evidence lives only in the chat.
        note = _accounting_note(topic, kwargs.get("conversation_history") or [])
        if note:
            ctx = f"{ctx}\n\n{note}"

        return {"context": ctx}
    except Exception:
        return None


def _accounting_note(topic: str, history: list[Any]) -> str:
    """Ask for the ledger to be filled when the turn used evidence without
    recording it. Empty string when there is nothing to say."""
    scan = _audit.scan_turn(history)
    if scan["evidence_calls"] == 0:
        return ""
    if scan["ledger_calls"] > 0:
        return ""
    n = scan["evidence_calls"]
    what = f"{n} 次检索" if n > 1 else "一次检索"
    return (
        f"[Research OS · 账本还是空的]\n"
        f"这轮做了{what}，但账本里一条断言都没有——这些证据现在只存在于聊天记录里，"
        f"下一个会话就找不回来了。\n"
        f"动笔前先用 research_mode(action='claim', project='{topic}', claim={{...}}) "
        f"把用到的证据记进去：观察和转述要填 source（PMID/DOI），"
        f"推论要填 premises 和 inference_type，假设标成 hypothesis。"
    )


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
            return None

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
