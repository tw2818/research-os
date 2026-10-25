"""The review gate: a reader who did not write the work, and who can say no.

A phase that only the agent writing it can judge is not gated at all. The
agent has every reason to believe the last round advanced the project, and
none of the distance a reader has who opens the state cold.

So the gate is a real call to a model that did not write this, given the
material and a rubric, and asked for the two things a vague approval cannot
give: the specific demands, and the specific place each demand is about.

Three decisions are made here, and each has a cost:

**The reviewer is a separate call, not a subagent.** A subagent is spawned by
the agent being reviewed, so the gate would be an instruction ("please go get
reviewed") rather than a control, and the verdict would reach the state file
as the agent's self-report. A separate call through the host's LLM facade
(`ctx.llm`) is made by the plugin itself: it cannot be skipped, and what lands
in the record is what the reviewer said.

**The gate reads the state, not the last message.** Judging one reply would
reward a fluent paragraph. The material submitted is the standing - core
question, narrative, plan, the ledger's claims, the routes and what they
contributed - which is what has to hold together.

**Failing to run is not a pass.** If the call errors, the record says so and
the verdict is `pass_with_caveat` with the error in it: blocking every project
on an infrastructure failure is worse than letting it through visibly, and a
caveat that says "nobody reviewed this" is the honest version of both.

Scores are not objective, and a gate that waits for a perfect score waits
forever. So the threshold is a setting, every attempt is kept, and after
`gate_max_rounds` the work goes through with the caveat written down.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    (
        "alignment",
        "围绕核心问题",
        "这一轮的工作在回答核心问题，还是在一个更容易回答的问题上打转？"
        "换过的问题如果留过痕，新问题是否还是同一个课题？",
    ),
    (
        "progression",
        "层层推进",
        "比上一轮多知道了什么？每一轮是否踩在前一轮的结论上，"
        "还是在原地重新开始、或者绕回已经否掉的说法？",
    ),
    (
        "grounding",
        "证据匹配",
        "结论的强度和账本里的证据等级匹配吗？有没有把推理写成观察、"
        "把单篇结果写成established、把假设当成结论？",
    ),
    (
        "convergence",
        "收束质量",
        "真的做取舍了吗：留下哪条路线、砍掉哪条、凭什么砍、代价说清没有？"
        "分支的结论写回父课题了吗？",
    ),
    (
        "deliverable",
        "可交性",
        "读者读完能不能带走一个立场？还是只有一堆并列的发现和一个"
        "'仍需进一步研究'？",
    ),
)

SCORES_SCHEMA = {
    "type": "object",
    "required": [key for key, _, _ in DIMENSIONS],
    "additionalProperties": False,
    "properties": {
        key: {"type": "integer", "minimum": 0, "maximum": 10, "description": label}
        for key, label, _ in DIMENSIONS
    },
}

GATE_SCHEMA = {
    "type": "object",
    "required": ["scores", "blocking_issues", "summary", "recommendation"],
    "additionalProperties": False,
    "properties": {
        "scores": SCORES_SCHEMA,
        "blocking_issues": {
            "type": "array",
            "description": "会挡住放行的问题。没有就留空数组——不是每个项目都该被卡住。",
            "items": {
                "type": "object",
                "required": ["axis", "problem", "where", "fix"],
                "additionalProperties": False,
                "properties": {
                    "axis": {"type": "string", "enum": [k for k, _, _ in DIMENSIONS]},
                    "problem": {"type": "string", "description": "问题是什么，一句话。"},
                    "where": {
                        "type": "string",
                        "description": "具体在哪：哪条主张、哪条路线、哪段叙事、哪句计划。"
                        "指不到具体位置的，不要写进来。",
                    },
                    "fix": {"type": "string", "description": "怎么改才算改掉，要可执行。"},
                },
            },
        },
        "summary": {"type": "string", "description": "两三句：这个项目现在最该修的是什么。"},
        "recommendation": {"type": "string", "enum": ["pass", "revise"]},
    },
}

INSTRUCTIONS = """你在给一个科研项目做阶段性的全局审查。写这个项目的不是你。

给你的材料是这个项目当前的状态：核心问题、现在相信什么、有什么证据、开了哪几条路线、计划下一步做什么。你要判断的不是文笔，是这件事有没有在往一个结论走。

按五个维度各打 0-10 分：
{dimensions}

规则：
- 你是审稿人，不是编辑。说得过去的项目就放行；有明显问题的才写进 blocking_issues。要求完美会让项目永远交不出来。
- 每个 blocking issue 必须指到具体位置：哪条主张、哪条路线、哪段叙事。指不到具体位置的抱怨不要写。
- 不要发明证据。材料里没有的文献、数字、设计，不能当成已经存在的东西来评价。
- 分数是给作者的信号，放行与否由阈值决定，这个阈值不告诉你，也不要猜。
- 如果材料本身不足以判断某一维，写进 summary 说明缺什么，不要靠印象打分。

输出 JSON：scores、blocking_issues、summary、recommendation。"""


def _rubric_text() -> str:
    return "\n".join(
        f"- {label}（{key}）：{prompt}" for key, label, prompt in DIMENSIONS
    )


def _clip(text: Any, limit: int = 400) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def build_brief(
    topic: str,
    *,
    core_question: str = "",
    goal: str = "",
    phase: str = "",
    narrative: str = "",
    plan: str = "",
    current_task: str = "",
    claims: Optional[List[Dict[str, Any]]] = None,
    branches: Optional[List[Dict[str, Any]]] = None,
    open_questions: Optional[List[Any]] = None,
    parent: str = "",
) -> str:
    """The material the reviewer gets. Built from the state, not the last reply.

    A reply can be fluent about work that has not moved. The standing cannot:
    if the narrative still says the same thing as three rounds ago, that is
    visible here and invisible in a paragraph.
    """
    lines: List[str] = [f"项目：{topic}"]
    if parent:
        lines.append(f"这是一条分支，父课题：{parent}")
    lines += [
        "",
        "## 核心问题",
        core_question.strip() or "（没写）",
        "",
        "## 目标",
        goal.strip() or "（没写）",
        "",
        f"## 当前阶段",
        phase.strip() or "（没设）",
        "",
        "## 现在相信什么（课题叙事）",
        narrative.strip() or "（没写）",
        "",
        "## 当前任务",
        current_task.strip() or "（没写）",
        "",
        "## 下一步计划",
        plan.strip() or "（没写）",
    ]

    rows = [c for c in (claims or []) if isinstance(c, dict)]
    if rows:
        lines += ["", "## 证据账本（最近的主张）"]
        for c in rows[-8:]:
            kind = str(c.get("kind") or "?")
            strength = str(c.get("strength") or "").strip()
            source = str(c.get("source") or "").strip()
            head = f"- [{kind}" + (f" · {strength}" if strength else "") + "]"
            if source:
                head += f" {source}"
            lines.append(f"{head} {_clip(c.get('content'), 160)}")
        if not any(str(c.get("source") or "").strip() for c in rows[-8:]):
            lines.append("- 注：这一批主张没有任何来源标注。")

    routes = [b for b in (branches or []) if isinstance(b, dict)]
    if routes:
        lines += ["", "## 路线（分支）"]
        for b in routes:
            status = str(b.get("status") or "open")
            line = f"- {b.get('id')} [{status}] {_clip(b.get('route'), 180)}"
            why = _clip(b.get("why"), 160)
            if why:
                line += f"（服务核心问题的方式：{why}）"
            contribution = _clip(b.get("contribution"), 220)
            if contribution:
                line += f" → 收束贡献：{contribution}"
            lines.append(line)

    questions = [q for q in (open_questions or []) if str(q or "").strip()]
    if questions:
        lines += ["", "## 待解问题"]
        for q in questions[-8:]:
            lines.append(f"- {_clip(q, 200)}")

    return "\n".join(lines)


def total_score(scores: Dict[str, Any]) -> float:
    """Equal weights. The dimensions are not equally important, but a
    weighting nobody agreed on is a number that looks precise and is not."""
    values: List[float] = []
    for key, _, _ in DIMENSIONS:
        try:
            values.append(float(scores.get(key, 0)))
        except (TypeError, ValueError):
            values.append(0.0)
    return round(sum(values) / len(values), 2) if values else 0.0


def decide(
    *,
    blocking: List[Dict[str, Any]],
    total: float,
    round_no: int,
    pass_score: float,
    max_rounds: int,
    llm_ok: bool = True,
) -> tuple[str, str]:
    """The verdict, and why.

    `pass` - no blocking issue and the score is above the line.
    `revise` - something has to change first, and there are rounds left.
    `pass_with_caveat` - either the rounds ran out, or nobody could review.
      The work goes on and the record says exactly why.

    The caveat is not a loophole: it is written into the topic's state and
    rendered into the vault, so a project that was waved through says so
    every time someone opens it.
    """
    if not llm_ok:
        return "pass_with_caveat", "审稿人没能跑起来（见 error），这一轮没有真正被审过。"
    graded = f"总分 {total}/10，底线 {pass_score}"
    if not blocking and total >= pass_score:
        return "pass", graded + "，没有 blocking 问题。"
    if round_no >= max_rounds:
        return (
            "pass_with_caveat",
            graded + f"，第 {round_no} 轮仍有问题，按 max_rounds={max_rounds} 放行——"
            "返修不设上限会让项目永远交不出来，但问题在记录里。",
        )
    if blocking:
        return "revise", graded + f"，{len(blocking)} 个 blocking 问题待改。"
    return "revise", graded + "，分数没到线。"


def _extract_json(text: str) -> Optional[dict]:
    """The JSON inside a reply that was supposed to be JSON.

    A schema-shaped request is a hint, not a guarantee: providers that ignore
    `response_format` still return the object, often wrapped in a fence or
    with a sentence in front of it. Failing the gate on a provider's
    formatting is failing it on the wrong thing.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    for candidate in (raw,):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
    if raw.startswith("```"):
        body = raw.split("```")
        for part in body:
            chunk = part.strip()
            if chunk.startswith("json"):
                chunk = chunk[4:].strip()
            if chunk.startswith("{"):
                try:
                    value = json.loads(chunk)
                    if isinstance(value, dict):
                        return value
                except ValueError:
                    pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            value = json.loads(raw[start:end + 1])
            if isinstance(value, dict):
                return value
        except ValueError:
            return None
    return None


JSON_SHAPE = """{
  "scores": {"alignment": 0, "progression": 0, "grounding": 0, "convergence": 0, "deliverable": 0},
  "blocking_issues": [
    {"axis": "alignment", "problem": "一句话说问题", "where": "哪条主张／哪条路线／哪段叙事", "fix": "怎么改才算改掉"}
  ],
  "summary": "两三句：这个项目现在最该修的是什么",
  "recommendation": "pass"
}"""


def _call_complete(llm: Any, instructions: str, brief: str, *, model: str, provider: str,
                   timeout: float, max_tokens: int = 8000) -> tuple[Any, Optional[dict], str]:
    """Plain completion, JSON parsed out of the text.

    This is the primary path on purpose. A schema-shaped request is only as
    good as the provider's support for it, and a provider that accepts the
    schema and returns nothing at all leaves the gate with no material to
    read — which is exactly what happened with the first version here. Asking
    for JSON in the prompt and parsing it tolerates every provider; the
    structured call is kept as the fallback for the ones that are strict.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "你是一位严格的审稿人，正在评估一个进行中的科研项目的阶段状态。"
                "你没有参与这个项目，也不为它辩护。只输出 JSON。"
            ),
        },
        {
            "role": "user",
            "content": (
                instructions
                + "\n\n按这个形状输出，只输出 JSON，不要解释、不要代码围栏：\n"
                + JSON_SHAPE
                + "\n\n项目材料：\n\n"
                + brief
            ),
        },
    ]
    kwargs: Dict[str, Any] = {
        "messages": messages,
        # Generous on purpose. A reasoning model spends its budget thinking,
        # and the first live run here came back with an empty reply because
        # 2200 tokens were gone before any text was written - the call
        # succeeded, the answer was blank.
        "max_tokens": max_tokens,
        "timeout": timeout,
        "purpose": "research-os phase gate",
    }
    if model:
        kwargs["model"] = model
    if provider:
        kwargs["provider"] = provider
    result = llm.complete(**kwargs)
    text = str(getattr(result, "text", "") or "")
    return result, _extract_json(text), text


def _call_structured(llm: Any, instructions: str, brief: str, *, model: str, provider: str,
                     timeout: float, max_tokens: int = 8000) -> tuple[Any, Optional[dict], str]:
    """The schema-shaped call, for providers that honour it."""
    base: Dict[str, Any] = {
        "instructions": instructions,
        "input": [{"type": "text", "text": brief}],
        "json_schema": GATE_SCHEMA,
        "schema_name": "gate_review",
        "json_mode": True,
        "system_prompt": (
            "你是一位严格的审稿人，正在评估一个进行中的科研项目的阶段状态。"
            "你没有参与这个项目，也不为它辩护。"
        ),
        "max_tokens": max_tokens,
        "timeout": timeout,
        "purpose": "research-os phase gate",
    }
    if model:
        base["model"] = model
    if provider:
        base["provider"] = provider
    result = llm.complete_structured(**base)
    parsed = getattr(result, "parsed", None)
    text = str(getattr(result, "text", "") or "")
    if not isinstance(parsed, dict):
        parsed = _extract_json(text)
    return result, parsed, text


def run(
    topic: str,
    *,
    llm: Any,
    brief: str,
    round_no: int,
    pass_score: float = 7.0,
    max_rounds: int = 3,
    model: str = "",
    provider: str = "",
    timeout: float = 240.0,
    max_tokens: int = 8000,
) -> Dict[str, Any]:
    """Ask the reviewer, and turn the answer into a record.

    Never raises: a gate that crashes takes the whole session's research
    state with it, and the one thing a gate must not do is lose the work it
    was judging. A failure becomes a `pass_with_caveat` record with the error
    in it.
    """
    import time as _time

    record: Dict[str, Any] = {
        "at": _time.time(),
        "topic": topic,
        "round": round_no,
        "brief_chars": len(brief),
        "model": "",
        "provider": "",
        "via": "",
        "scores": {key: 0 for key, _, _ in DIMENSIONS},
        "total": 0.0,
        "blocking": [],
        "summary": "",
        "recommendation": "",
        "verdict": "",
        "why": "",
        "llm_ok": False,
        "attempts": 0,
        "error": "",
    }
    instructions = INSTRUCTIONS.replace("{dimensions}", _rubric_text())
    notes: list[str] = []
    result: Any = None
    parsed: Optional[dict] = None
    text = ""
    try:
        for via, call in (
            ("complete", _call_complete),
            ("structured", _call_structured),
        ):
            record["attempts"] += 1
            try:
                result, parsed, text = call(
                    llm, instructions, brief,
                    model=model, provider=provider, timeout=timeout,
                    max_tokens=max_tokens,
                )
            except Exception as exc:  # noqa: BLE001 - try the other path
                notes.append(f"{via}: {type(exc).__name__}: {str(exc)[:160]}")
                continue
            if isinstance(parsed, dict):
                record["via"] = via
                break
            notes.append(f"{via}: 没有可解析的 JSON（回应 {len(text)} 字符）")
        record["model"] = str(getattr(result, "model", "") or "")
        record["provider"] = str(getattr(result, "provider", "") or "")
        record["reply_chars"] = len(text)
        usage = getattr(result, "usage", None)
        if usage is not None:
            record["usage"] = {
                "input": int(getattr(usage, "input_tokens", 0) or 0),
                "output": int(getattr(usage, "output_tokens", 0) or 0),
            }
        if not isinstance(parsed, dict):
            detail = "；".join(notes) or "没有回应"
            if text:
                detail += "；回应开头：" + _clip(text, 140)
            raise ValueError("审稿人没有给出可用的 JSON — " + detail)
        raw_scores = parsed.get("scores")
        scores = raw_scores if isinstance(raw_scores, dict) else {}
        record["scores"] = {
            key: int(scores.get(key) or 0) for key, _, _ in DIMENSIONS
        }
        record["total"] = total_score(record["scores"])
        blocking = [b for b in (parsed.get("blocking_issues") or []) if isinstance(b, dict)]
        record["blocking"] = blocking[:12]
        record["summary"] = str(parsed.get("summary") or "").strip()
        record["recommendation"] = str(parsed.get("recommendation") or "").strip()
        record["llm_ok"] = True
    except Exception as exc:  # noqa: BLE001 - a gate may not take the session down
        record["error"] = f"{type(exc).__name__}: {exc}"[:600]

    verdict, why = decide(
        blocking=record["blocking"],
        total=float(record["total"]),
        round_no=round_no,
        pass_score=pass_score,
        max_rounds=max_rounds,
        llm_ok=bool(record["llm_ok"]),
    )
    record["verdict"] = verdict
    record["why"] = why
    return record


def demands_text(record: Dict[str, Any], *, limit: int = 3) -> str:
    """The blocking issues, as the list the agent has to work through."""
    rows = [b for b in record.get("blocking") or [] if isinstance(b, dict)]
    if not rows:
        return ""
    out: List[str] = []
    for b in rows[:limit]:
        axis = str(b.get("axis") or "")
        label = next((lbl for key, lbl, _ in DIMENSIONS if key == axis), axis)
        out.append(
            f"- {label}：{_clip(b.get('problem'), 120)}\n"
            f"  位置：{_clip(b.get('where'), 120)}\n"
            f"  怎么改：{_clip(b.get('fix'), 160)}"
        )
    return "\n".join(out)


def dump(record: Dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False)
