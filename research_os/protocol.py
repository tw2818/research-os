"""The protocol block injected into the user's message while in research mode.

Design constraints this file exists to satisfy:

1. The user never sees this text. It rides the pre_llm_call context channel,
   which Hermes appends to the user message as agent-facing context.
2. It must stay small. Every injected character breaks the prompt cache for
   the rest of the turn, so the block is capped by config `max_inject_chars`.
3. It must not turn the agent into a lecturer. A chore request inside a
   research session must still get a chore answer.
4. It must separate what a source reports from what the agent concludes.
"""

from __future__ import annotations

import re
from typing import Any

try:
    # Private namespace first: the plugin loads its modules under
    # `research_os_impl.*` so they cannot collide with another plugin's
    # `state.py` and cannot go stale behind a reloaded entry module.
    from research_os_impl.state import (
        child_directive,
        directive_progress,
        read_claims,
        read_contradictions,
        read_map,
        read_parent,
        read_review,
        read_siblings,
        list_branches,
        last_gate_review,
    )
except ImportError:
    try:
        from .state import (
            child_directive,
            directive_progress,
            read_claims,
            read_contradictions,
            read_map,
            read_parent,
            read_review,
            read_siblings,
            list_branches,
            last_gate_review,
        )
    except ImportError:  # loaded flat inside the Hermes plugin directory
        from state import (  # type: ignore[no-redef]
            child_directive,
            directive_progress,
            read_claims,
            read_contradictions,
            read_map,
            read_parent,
            read_review,
            read_siblings,
            list_branches,
            last_gate_review,
        )

# The stations of a research project, and what thinking each one calls for.
#
# The old list ran in one direction and that was the flaw: a review does not
# narrow from start to finish, it fans out into routes and closes them one at
# a time. The stations below are a loop, not a line - 发散 opens routes, 收敛
# closes one, 更新 writes the state back, and the next round starts again from
# whichever station the work is actually at.
#
# The other flaw the old list had: nothing in it ever asked whether a route
# was worth opening at all, or whether the routes were still answering the
# core question. Once a project can hold several routes, something has to
# hold them to the question, and that is what 收敛 and the review gate are for.
PHASES: dict[str, dict[str, str]] = {
    "standing": {
        "name": "确认科研状态",
        "strictness": "open",
        "thinking": (
            "这轮在确认科研状态，先不找证据。新课题：把核心问题、目标、"
            "为什么值得问、答案会是什么样子，和用户谈清楚再往下走。"
            "同时判断这一轮的科学活动是哪一种：梳理一个领域（mapping）、"
            "比较几种解释（exploratory）、验证一个已定的问题（systematic）、"
            "整合已有分支、还是把结论写出来。"
            "活动类型决定约束强度，不是所有任务都该被当成假设检验——"
            "梳理领域的时候逼人先写否证条件，只会写出一句什么都不否证的话。"
            "老课题：先确认状态有没有变——问题还是不是那个问题，"
            "上一轮留下的话还算不算数。"
        ),
        "output": (
            "给出这一轮的核心问题、目标，以及你目前相信什么、凭什么。"
        ),
        "gate": (
            "这一阶段不找证据，也不铺路线。核心问题还没和用户对齐就往下推，"
            "后面每一站都是白做。"
        ),
    },
    "diverging": {
        "name": "发散路线",
        "strictness": "open",
        "thinking": (
            "这轮在铺路线。一个核心问题下面能走的路不止一条：列出几条可能的研究路线，"
            "每条写成一条分支（用 branch 开），说清它服务核心问题的哪一面、"
            "查什么能把它否掉。路线要开得开，但每一条都得还站在核心问题上——"
            "跟核心问题无关的分支不是发散，是跑题。"
        ),
        "output": (
            "给出这几条路线，每条一句：要检验什么、怎么否掉、什么时候算走完。"
        ),
        "gate": (
            "这一阶段不选路，也不收口，只铺路；但铺出来的每条路都要能指出"
            "它和核心问题的关系。至少两条，少于两条就是在赌第一个想法。"
        ),
    },
    "evidence": {
        "name": "找证据",
        "strictness": "mixed",
        "thinking": (
            "这轮在找证据，外部文献或本地已有材料都行。"
            "每份材料都要挂在某条路线上：它在检验哪条路线的哪个预测？"
            "和路线无关的材料记成旁支，不要混进主线账本。"
            "证据回来之后，先看它对这一轮意味着什么：它削弱了哪条路线、"
            "支持了哪条、还是补上了此前不知道的事实。"
            "最后这一种也是进展——知道了一个原先不知道的事实、把概念分得更清楚了、"
            "发现了一个之前被忽略的变量、把两个领域连上了，"
            "这些都在往前走，只是没有裁决任何一条路线。"
            "只有当这批材料既没有改变判断、也没有让知识空间变大时，"
            "才说明它没有判据。"
        ),
        "output": (
            "给出这批证据支持哪条路线、削弱哪条、还不确定哪条。"
        ),
        "gate": (
            "这一阶段以找证据为主，但结论和改问题都没有被禁止："
            "证据已经指向一个答案时直接说，被证据推翻了的问题就改掉——"
            "用 set_state 带上 core_question 和 question_reason，旧问法会留痕。"
        ),
    },
    "synthesis": {
        "name": "梳理理解",
        "strictness": "mixed",
        "thinking": (
            "这轮在梳理理解。不是把材料再摆一遍，而是把它们变成理解："
            "哪几条路线还活着、哪条被削掉了、剩下的分歧到底分歧在哪里、"
            "还缺哪一个判据才能分开。路线之间的冲突要先确认它们测的是不是同一件事。"
        ),
        "output": (
            "给出现在的理解：哪条路线还活着、为什么、还缺什么。"
        ),
        "gate": (
            "这一阶段不新增材料。梳理要靠已经进账的证据，"
            "缺证据就回 evidence 去补，不要在这里顺手编。"
        ),
    },
    "converging": {
        "name": "收敛",
        "strictness": "tight",
        "thinking": (
            "这轮在收敛，真的做取舍：留哪条路线、砍掉哪条、代价是什么、"
            "什么新证据会翻案。砍掉的要说清凭什么砍，不能只因为它不好写。"
            "如果这一站收的是一条分支，结论要写回父课题（branch 带上 contribution）——"
            "分支不写回，父课题就只是攒了一堆没消化的材料。"
        ),
        "output": (
            "给出一句能带走的话：现在的判断是什么、强到哪里、什么条件下不成立。"
        ),
        "gate": (
            "这一阶段要决断，不要把所有材料都摆出来。"
            "收完之后走一次审稿（gate），过了才往下写状态。"
        ),
    },
    "updating": {
        "name": "更新科研状态",
        "strictness": "tight",
        "thinking": (
            "这轮把状态写回去：Obsidian 的页、Zotero 的集合、决策日志、"
            "下一轮的计划。数字、引用、结论强度都要和账本对得上——"
            "写回去的状态就是下一轮的起点，写错了下一轮从错的地方开始。"
        ),
        "output": (
            "给出更新了什么、哪些没写进去、下一轮从哪一站接着走。"
        ),
        "gate": (
            "这一阶段不加新论证，只把已有的写回去。"
            "写不进去的用 open_questions 记下来，别留在对话里。"
            "更新完可以回到发散或找证据，迭代不是一次走完的。"
        ),
    },
    "writing": {
        "name": "成文表达",
        "strictness": "tight",
        "thinking": (
            "这轮在写。先说清楚给谁看、他们已经知道什么、看完要知道什么。"
            "少用'研究表明'，直接说是谁、什么设计、什么效应量、什么限制。"
        ),
        "output": (
            "给出成稿的段落式结论，读者读完能带走一个立场。"
        ),
        "gate": (
            "这一阶段只写。发现结论有问题就标出来，别在写作时顺手改结论。"
        ),
    },
}

DEFAULT_PHASE = "synthesis"

# The stations in order. A project does not have to walk them in one
# direction - the loop re-enters wherever the work actually is - but the
# order is what "moving on" means when a phase is asked to advance.
PHASE_ORDER = (
    "standing", "diverging", "evidence", "synthesis", "converging", "updating", "writing",
)

# What this vocabulary used to be. A topic written by an older version names
# its phase with one of these, and a state file that stops loading because the
# words changed is a state file that loses its own history.
PHASE_ALIASES = {
    "scoping": "standing",
    "weighing": "synthesis",
    "closing": "converging",
}

# How hard each phase pushes toward a stated position.
#
# The evidence discipline is the floor and it never moves: a claim with no
# source is not a claim, whatever the phase. What changes is how hard the
# answer is pushed to commit.
#
# "open"  - the phase is for possibilities. A position is not required,
#           and naming a speculative route is the point.
# "mixed" - the phase weighs what is there. A position is expected, but
#           the answer may still say what it does not know.
# "tight" - the phase commits. A position is required, and its strength
#           is checked against the ledger.
PHASE_STRICTNESS = {
    "standing": "open",
    "diverging": "open",
    "evidence": "mixed",
    "synthesis": "mixed",
    "converging": "tight",
    "updating": "tight",
    "writing": "tight",
}


def normalize_phase(phase: str) -> str:
    """A phase name this file still knows, or empty.

    Old names are translated rather than rejected: a topic that was in
    `weighing` yesterday is in `synthesis` today, and it should not lose its
    state to a rename.
    """
    key = str(phase or "").strip()
    key = PHASE_ALIASES.get(key, key)
    return key if key in PHASES else ""


def phase_strictness(phase: str) -> str:
    """How hard this phase pushes toward a stated position."""
    entry = PHASES.get(normalize_phase(phase))
    key = str(entry.get("strictness") or "") if entry else ""
    if not key:
        key = PHASE_STRICTNESS.get(normalize_phase(phase), "mixed")
    return key or "mixed"


def phase_name(phase: str) -> str:
    """The phase's name, for a reader who sees one word and not the block.

    Empty when no phase has been set yet: a new topic has none until the
    agent sets one, and that is a healthy state, not a missing one.
    """
    key = normalize_phase(phase)
    if not key:
        return ""
    return str(PHASES[key].get("name") or key)


def phase_thinking(phase: str) -> str:
    """The thinking instruction for a phase, with its name."""
    entry = PHASES.get(normalize_phase(phase)) or PHASES[DEFAULT_PHASE]
    return f"当前阶段：{entry['name']}\n{entry['thinking']}"


def phase_gate(phase: str) -> str:
    """What this phase is not for.

    This is what replaced a keyword matcher that tried to guess whether a
    request fitted the phase. The matcher fired on "再查" and missed "再
    找找", and it could not tell a research search from a technical one -
    but the agent knows which is which, and can say so. Stating the
    boundary in the phase is more reliable than guessing at it from
    outside.
    """
    entry = PHASES.get(normalize_phase(phase)) or PHASES[DEFAULT_PHASE]
    return str(entry.get("gate") or "")


def phase_output(phase: str) -> str:
    """The shape the answer should take in this phase.

    The phase has to change the shape of the output, not only the way of
    thinking about it. A model that gathers evidence in the weighing phase
    will produce a list of findings with no position in it, and a list of
    findings is what the reader cannot use.
    """
    entry = PHASES.get(normalize_phase(phase)) or PHASES[DEFAULT_PHASE]
    return str(entry.get("output") or "")


# The four claim types, stated as the agent's own working vocabulary.
# The claim discipline. Not injected any more: the full format is in the
# tool's own description, and an agent that is about to write a claim reads
# the tool schema anyway. Restating it on every turn cost 70% of the
# injection budget to repeat something the agent was told anyway, and the
# budget is worth more spent on how to think.
#
# Kept here because the tool description and this must not drift apart, and
# because the reference implementation uses it in its own error messages.
CLAIM_RULES = """记进账本时用 research_mode(action='claim', project='<课题>', claim={...})：
observation 事实(要文献)｜ report 转述(要文献)｜ inference 推论(premises 填行号，强度不超最弱前提；父课题可引子课题，写「子课题:L3」)｜ hypothesis 假设(无来源但要留作假设)。
没来源的别写成事实。"""

# How to think about the question, before answering it.
#
# This is the part that makes the mode research rather than bookkeeping.
# It comes from the same place the multi-perspective review rules come from,
# and it is deliberately generative: each move produces something new rather
# than checking that something old is well-formed.
#
# The moves, in the order a researcher actually makes them:
#   1. reframe the question - the asked question is often a symptom
#   2. generate rival explanations, including one that contradicts the
#      obvious reading
#   3. ask what observation would tell them apart
#   4. look for the trap - the inference that looks available but is not
#   5. say plainly what is not known
#
# The fourth move is the one models skip. "Remission still shows a deficit,
# therefore the circuit is damaged" is the inference that looks available and
# is not, because a residual state explains it too. A mode that does not
# name that move will let the agent make it.
THINKING_RULES = """回答前按科研的路子走：
1. 问准：这问题背后真正要答的是什么？现在问的可能是它的影子。
2. 给可能答案：如果这问题不止一种说得通的答案，给出至少两种，包括跟你直觉相反的那种，
   写清各自在什么情况下成立。
3. 找抓手：什么材料/检验/推理能把它们分开？分不开就直说分不开，别糊过去。
   如果这问题本来就不该靠"分开"来回答（纯理论、单一答案的设计或解读问题），
   换成本来该用的判据：什么证明它对，什么证明它错。
4. 找陷阱：哪个推论看着成立其实不成立？找出那个还有别的东西也能解释的推论。
5. 取舍：这一轮只能推进一点，说清推进了哪一点、剩下的为什么先放着。
   不要把所有相关材料都摆出来——摆全了读者拿不到结论。

判断该给的时候给判断：该说现在认为什么，就直说，别用一串限定语把话说到没剩下。
该探索的时候大胆探索：大胆提一个明确标记的假说，哪怕只有弱支持——
先被否掉的想法也是进展，被 hedged 掉的想法不是。
按当前阶段的要求决定这一轮该给判断还是该给可能性。

不确定就写不确定。允许答案是"现在还不能定"——但这是查过之后的结论，不是省事的开场白。

先给判断，再给依据，最后给还缺什么。不要写成条目清单——清单把判断淹了。"""

# What the turn has to produce. A research session that ends with the same
# state it started in has done nothing, and nothing in the protocol can
# detect that except asking for it.
#
# The list is ordered by what a researcher would actually do, and the
# bookkeeping comes last on purpose: a turn that only records has done
# administration, not research.
TURN_DEMAND = """这轮要有新东西，别只复述已有内容。至少做到一件（按科研价值排序）：
提出或改写一个可能答案｜说出区分两个答案的判据｜指出一个看着成立其实不成立的推论｜
关掉一个待解问题｜明确说清这里无法定论｜新增一条带来源的断言｜
把一个此前不知道的事实、或者一个被忽略的变量记下来（这也算进展，不必裁决任何一条路线）。
做完整之后用 research_mode 记下来，例如 research_mode(action='claim', project='<课题>', claim={...})
一次查出几条就一次记完：claims=[{...},{...}]，不用一条一个调用。\n（提解释用 action='contradict'，关问题用 action='question'，提假设用 action='hypothesis'）。"""

SCOPE_RULE = """如果用户这轮要的是操作（转格式、导入、装东西、改代码），直接做，不要讨论研究意义。
只有用户问研究问题时才按上面的四类断言来写。"""


def _ledger_problems(topic: str) -> str:
    """What is wrong with the ledger, for the phases that are about to commit.

    The checks run all along - a claim with no source is not a claim
    whatever the phase. But the moment they are shown to the agent
    matters: an agent that is told its inference is overstated while it
    is still exploring will weaken the inference, and then the narrowing
    phase has nothing left to narrow. A hypothesis that was never stated
    strongly is a hypothesis that never got tested.

    So the report is produced when the work is about to commit, which is
    when a problem with the ledger actually changes what the reader
    should believe.
    """
    try:
        from .validate import validate_claims
    except ImportError:
        try:
            from validate import validate_claims  # type: ignore[no-redef]
        except Exception:
            return ""
    try:
        report = validate_claims(topic)
    except Exception:
        return ""
    errors = report.get("errors") or []
    if not errors:
        return ""
    return (
        f"账本有 {len(errors)} 条必须修掉的问题"
        "（证据分级不因为阶段放松而失效）。"
        "用 action='validate' 看清单，用 action='amend' 改——id 不变、引用不断，"
        "删掉重记会让指向它的推论全部失效。"
    )


def _claims_digest(topic: str, limit: int = 6) -> str:
    """A short view of the ledger: how many claims, and what is still pending."""
    claims = read_claims(topic)
    if not claims:
        return "账本：空"
    by_kind: dict[str, int] = {}
    pending: list[str] = []
    for c in claims:
        k = c.get("kind", "?")
        by_kind[k] = by_kind.get(k, 0) + 1
        if (c.get("status") or "") not in ("verified", "derived", "established"):
            pending.append(c.get("id", "?"))
    bits = ", ".join(f"{k}×{v}" for k, v in sorted(by_kind.items()))
    tail = f"；待核 {len(pending)} 条" if pending else "；无待核"
    return f"账本：{len(claims)} 条（{bits}）{tail}"


def _situation(topic: str) -> list[str]:
    """Where the research stands right now.

    The point of a research session is not to be tidy about claims - it is to
    move the question forward. So the injection says what the state of play
    is: which explanation currently looks best, which one is under pressure,
    and what the cheapest next test would be. That is what a researcher
    wants to know when they sit down, and it is what an agent needs in order
    to do research instead of summarising.
    """
    data = read_contradictions(topic) or {}
    items = [i for i in (data.get("items") or []) if isinstance(i, dict)]
    if not items:
        return []
    lines: list[str] = []
    best = [i for i in items if str(i.get("status") or "").lower() in ("best", "leading")]
    live = [i for i in items if str(i.get("status") or "").lower() not in
            ("refuted", "rejected", "killed", "retired")]
    if best:
        lines.append("当前最佳解释：" + "；".join(_short(i.get("title")) for i in best[:1]))
    elif live:
        lines.append("候选解释：" + "；".join(_short(i.get("title")) for i in live[:2]))
    # The explanation under pressure is the one whose separator nobody has
    # run yet, or whose status says it is contested.
    pressured = [i for i in items if str(i.get("status") or "").lower() in
                 ("contested", "weakening", "challenged")]
    for i in pressured[:1]:
        lines.append(f"受质疑的解释：{_short(i.get('title'))} —— 分开它需要：{_short(i.get('separator'))}")
    # The cheapest next test is the separator of an explanation that is still
    # live: it says what evidence would move the question, and moving the
    # question is the work.
    for i in live[:1]:
        sep = str(i.get("separator") or "").strip()
        if sep:
            lines.append(f"能分开它的证据：{_short(sep, 160)}")
            break
    return lines


def _qtext(q: Any) -> str:
    """The text of a question, however the map happens to store it.

    A closed question is stored with its answer - the question is what
    the agent needs to see, because the answer is what it would otherwise
    go and look up again.
    """
    if isinstance(q, dict):
        for key in ("question", "q", "text", "title", "content"):
            v = str(q.get(key) or "").strip()
            if v:
                return v
        return ""
    return str(q or "").strip()


def _routes(topic: str) -> list[str]:
    """The routes this topic holds, and where each one stands.

    A tree is only readable if the register travels with it: without this,
    the agent sees the core question and a pile of claims and cannot tell
    which route a claim belongs to — or that a route it opened last week is
    still open and unattended.
    """
    try:
        rows = list_branches(topic)
    except Exception:
        return []
    if not rows:
        return []
    open_rows = [b for b in rows if str(b.get("status") or "open") in ("open", "active")]
    done_rows = [b for b in rows if b not in open_rows]
    out: list[str] = [
        f"路线：{len(rows)} 条（开着 {len(open_rows)}）："
        + " ｜ ".join(
            f"{b.get('id')}[{b.get('status')}] {_short(b.get('route'), 40)}" for b in rows
        )
    ]
    for b in open_rows[:3]:
        out.append(
            f"- {b.get('id')} 服务核心问题：{_short(b.get('why'), 60)}"
        )
    for b in done_rows[-2:]:
        contribution = _short(b.get("contribution"), 60)
        if contribution:
            out.append(f"- {b.get('id')} 已收束，贡献：{contribution}")
    # Which branches have not picked up the direction this topic gave them.
    # A directive nobody acknowledged is a directive nobody has read, and the
    # parent has no way to tell that from the branch agreeing.
    unread = [
        str(b.get("id") or "") for b in rows
        if isinstance(b.get("directive"), dict)
        and b["directive"]
        and not b["directive"].get("acknowledged_at")
    ]
    if unread:
        out.append(f"下发过但子课题还没确认：{'、'.join(unread)}")
    # Confirmed is not the same as done. The branch said it received the
    # direction; whether its own plan moved afterwards is a different
    # question, and the only one that changes what the parent should
    # expect from it.
    try:
        stalled = [
            row["branch"] for row in directive_progress(topic)
            if row.get("state") == "已确认但计划没动"
        ]
    except Exception:
        stalled = []
    if stalled:
        out.append(
            f"确认了但计划没动：{'、'.join(stalled)}——指示收到了不等于照着做了，"
            f"要么计划改成对着它做，要么说清为什么不做"
        )
    if len(open_rows) > 2:
        out.append("（同时开着三条以上路线时，先收掉一条再开新的。）")
    # The gate's last word, in the phase that has to earn its way past it.
    phase = ""
    last: dict[str, Any] = {}
    try:
        phase = str(read_map(topic).get("phase") or "")
        last = last_gate_review(topic)
    except Exception:
        last = {}
    if last and normalize_phase(phase) in ("converging", "updating", "writing"):
        verdict = str(last.get("verdict") or "")
        label = {"pass": "已放行", "revise": "未过", "pass_with_caveat": "带 caveat 放行"}.get(verdict, verdict)
        out.append(
            f"审稿：第 {last.get('round')} 轮 {label}（{last.get('total')}/10）"
            + (f"，还没改的：{_short((last.get('blocking') or [{}])[0].get('problem'), 50)}"
               if verdict == "revise" else "")
        )
    return out


def _parent_directive(topic: str) -> list[str]:
    """What the parent told this branch to do, until the branch receives it.

    It sits above the standing because it can change what this round is for.
    Once acknowledged it is history, not instruction, and it drops out of the
    block instead of being repeated every turn.
    """
    try:
        d = child_directive(topic)
    except Exception:
        return []
    if not d:
        return []
    decision = {
        "continue": "继续", "redirect": "改向", "pause": "暂停", "close": "收尾",
    }.get(str(d.get("decision") or ""), str(d.get("decision") or ""))
    bits = [f"父课题（{d.get('parent')}）对这条例线（{d.get('branch')}）的指示：{decision}"]
    if d.get("next_question"):
        bits.append(f"要检验的是：{_short(d.get('next_question'), 70)}")
    if d.get("reason"):
        bits.append(f"理由：{_short(d.get('reason'), 70)}")
    bits.append(f"优先级：{d.get('priority') or 'normal'}")
    if d.get("acknowledged_at"):
        bits.append("（已确认）")
        try:
            rows = [row for row in directive_progress(str(d.get("parent") or ""))
                    if row.get("branch") == str(d.get("branch") or "")]
        except Exception:
            rows = []
        if rows and not rows[0].get("plan_moved_after"):
            bits.append(
                "（确认了但计划没动：这轮要么照着做，要么在叙事里说清为什么不做）"
            )
    else:
        bits.append(
            "（还没确认：先调 research_mode(action='acknowledge', "
            f"project='{topic}', acknowledge_note='<这一轮怎么对着它做，或为什么不做>')）"
        )
    return [" ｜ ".join(bits)]


def _frame(topic: str, m: dict[str, Any] | None = None) -> list[str]:
    """What the project has already settled, and what it has not touched.

    The injection used to carry the standing and the current situation,
    and the rest of the map was invisible. The agent knew which
    explanation was ahead and what evidence would separate it, and knew
    nothing about the questions the project had already closed or about
    the hypotheses it had never come back to. So it did the work again
    for questions that were already answered, and it narrowed onto the one
    hypothesis in front of it without noticing that the map still held
    six others it had not touched.

    These lines are the project's frame. They are deliberately short: a
    frame that lists everything is a file, not a line. What belongs here
    is what changes what the agent does next - a question that is already
    answered (do not go looking for it again), the questions that are
    still open (that is where the work is), and how wide the space of
    competing hypotheses still is (so the judgment does not quietly
    become narrower than the question).

    None of this is a task for the agent. It is a picture of the project,
    and the project keeps working whatever the agent does with it.
    """
    m = m if m is not None else read_map(topic)
    lines: list[str] = []

    # The questions that are closed. This is the line that saves the
    # most work: an agent that does not know a question is settled will
    # go and settle it again, and the search is the expensive part.
    settled = [t for t in (_qtext(q) for q in (m.get("resolved_questions") or [])) if t]
    if settled:
        items = "；".join(_short(q, 55) for q in settled[:2])
        more = f"（另有 {len(settled) - 2} 条）" if len(settled) > 2 else ""
        lines.append(f"已解问题（不用再查）：{items}{more}")

    # The questions still open. Two of them, and how many are left, so
    # the agent can tell whether it is looking at the whole list or at
    # the top of it.
    open_q = [t for t in (str(q).strip() for q in (m.get("open_questions") or [])) if t]
    if open_q:
        items = " ｜ ".join(_short(q, 38) for q in open_q[:2])
        more = f"（另有 {len(open_q) - 2} 条）" if len(open_q) > 2 else ""
        lines.append(f"待解问题（还没解）：{items}{more}")

    # The hypotheses the project is holding. The count is the point: the
    # current judgment usually weighs one or two of them, and the agent
    # has no other way of noticing that the map still holds the rest.
    #
    # Which ones are untouched cannot be worked out from the state - a
    # hypothesis has no status field, and matching the map's list against
    # the ledger's wording is a guess. So the line states what is
    # certain, the width of the space, and leaves the comparison to the
    # agent that can read the map.
    hyp = [t for t in (str(h).strip() for h in (m.get("hypotheses") or [])) if t]
    if hyp:
        if not str(m.get("narrative") or "").strip():
            lines.append(
                f"未处理的假设：地图上 {len(hyp)} 个竞争假设，都还没写进当前判断"
            )
        else:
            lines.append(
                f"未处理的假设：地图上 {len(hyp)} 个竞争假设，当前判断未必都覆盖过"
            )
    return lines


def _short(text: Any, limit: int = 110) -> str:
    """Trim a long field to its first clause.

    The separators are written for the record and run to several hundred
    characters. Injected, they eat the budget that the standing and the
    alignment check need, and the point of a separator - that an
    observation would tell two explanations apart - survives the first
    sentence.
    """
    t = " ".join(str(text or "").split())
    if len(t) <= limit:
        return t
    cut = t[:limit]
    for sep in ("。", "; ", "；", ". "):
        idx = cut.rfind(sep)
        if idx > limit * 0.5:
            return cut[: idx + len(sep)].rstrip()
    return cut.rstrip() + "…"


def _standing(topic: str) -> list[str]:
    """Where the project stands: the goal, the phase, the current task.

    These three sit above the evidence. The core question says what the
    project is about; the goal says what it is trying to get at; the
    current task says what is being done right now. An agent that sees only
    the current task will do it well and drift.
    """
    m = read_map(topic)
    lines: list[str] = []
    goal = str(m.get("goal") or "").strip()
    if goal:
        lines.append(f"课题目标：{goal}")
    phase = str(m.get("phase") or "").strip()
    if phase:
        lines.append(phase_thinking(phase))
    task = str(m.get("current_task") or "").strip()
    if task:
        lines.append(f"当前任务：{task}")
    # The narrative is what we currently believe and why it changed. It is
    # the answer to "where is this project", and it is the thing a review
    # compares the work against.
    narr = str(m.get("narrative") or "").strip()
    if narr:
        lines.append(f"当前判断（课题叙事）：{narr}")
    plan = str(m.get("plan") or "").strip()
    if plan:
        lines.append(f"下一步计划：{plan}")

    # The top topic, and the siblings under it.
    #
    # A cross-domain review is several sub-questions under one question.
    # Each sub-question has its own ledger and its own hypotheses, and
    # that isolation is deliberate. But the sub-question cannot see the
    # others, and a review that does not know two of its parts touch the
    # same evidence will end up making the same point twice, or leaving a
    # gap between them.
    #
    # What comes here is the parent question and what the siblings are
    # about. Not their hypotheses and not their ledger.
    parent = read_parent(topic)
    if parent:
        lines.append(f"所属总课题：{parent.get('name')}")
        if parent.get("core_question"):
            lines.append(f"总课题的核心问题：{parent['core_question']}")
    siblings = read_siblings(topic)
    if siblings:
        listed = "；".join(
            f"{s['name']}（{s['core_question'][:40] or '尚未写明核心问题'}）"
            for s in siblings[:5]
        )
        lines.append(
            f"同一总课题下的其他子课题：{listed}。"
            f"如果两处证据指向同一件事，说出来；"
            f"各子课题的假设不互相继承。"
        )
    return lines


def _review_owed_line(topic: str) -> str:
    """The debt, in one line, in front of the agent at the top of the block.

    This is the mechanism that replaces a reminder the agent could answer
    in prose and move past. The prose answer looked like compliance and
    left nothing behind; this line is read before anything else, cannot be
    cut by the budget, and stays until a review is recorded.
    """
    d = read_review(topic)
    if not d.get("owed") or d.get("abandoned_at"):
        return ""
    demanded = int(d.get("demanded") or 0)
    turns = int(d.get("owed_turns") or 0)
    return (
        f"审查欠着：已催 {demanded} 次、已过去 {turns} 轮仍未做。"
        "这轮先做审查（research_mode action='review'），再谈别的。"
    )


def review_demand(topic: str, review: dict[str, Any] | None = None) -> list[str]:
    """Ask for the review, when a review is due.

    A review that fires every turn is a nag and gets ignored; one that
    fires on a counter is a checkpoint. The counters are per topic: a
    drift that takes three turns to appear is not visible in any one of
    them, so the review has to look at the topic, not the turn.
    """
    r = review or read_review(topic)
    if r.get("paused"):
        return [
            "这个课题现在处于暂停状态：上一次全局审查判定工作已经偏离核心问题。",
            "先别继续检索。先想清楚：这个课题现在到底在回答什么、"
            "手上这些材料说明了什么、下一步要做什么，"
            "用 research_mode(action='set_state', ...) 把叙事和计划改掉，"
            "再回来检索。",
        ]
    return []


def _alignment(topic: str) -> list[str]:
    """Ask, every turn, whether the current task still serves the core
    question.

    Drift is the failure that no local check catches. Every claim in the
    ledger can be sourced, every inference can be within its premises, and
    the project can still have become a different project - a pile of
    well-supported facts about a question nobody is asking any more.

    So the alignment is stated on every turn, with both sides of it: the
    core question, and the current task. The agent is asked to notice the
    gap. Nothing is blocked; the point is that the gap is visible while
    there is still time to close it.
    """
    m = read_map(topic)
    core = str(m.get("core_question") or "").strip()
    task = str(m.get("current_task") or "").strip()
    goal = str(m.get("goal") or "").strip()
    if not task:
        return []
    if not core and not goal:
        return []
    # One line. The standing is already stated just above, so repeating it
    # here would spend the budget on a copy. What this line has to do is
    # put the task and the question next to each other and ask.
    side = f"（目标：{_short(goal, 60)}）" if goal else ""
    return [
        f"对齐检查：当前任务「{_short(task, 60)}」还在推进核心问题吗{side}？"
        f"若只是在解决更小的子问题，说出来，并说要不要调任务或换阶段。"
    ]


def build_research_block(topic: str, config: dict[str, Any] | None = None) -> str:
    """The text injected while a session is in research mode for `topic`."""
    m = read_map(topic)
    if not m:
        return f"[Research OS · {topic}]\n该课题还没有 research-map.json，先读它再回答。"

    hyp = [h for h in (m.get("hypotheses") or []) if str(h).strip()]

    lines = [f"[Research OS · {topic}]"]
    lines.append(f"核心问题：{m.get('core_question', '')}")
    # A direction from the parent comes before anything this topic thinks it
    # is doing: it is the one line that can make the rest of the block
    # obsolete.
    lines.extend(_parent_directive(topic))
    # Where the project stands comes before what is known. The goal, the
    # phase and the current task are what the turn is for; the evidence is
    # what the turn works with.
    lines.extend(_standing(topic))
    # The routes come right after the standing: a project holding several
    # routes cannot be read from its core question alone. Which routes are
    # open, which closed and what each contributed is the part of the state
    # that says whether this is still one project or has quietly become five.
    lines.extend(_routes(topic))
    # A review that was asked for and not made is a debt, and it goes above
    # the situation: while it is outstanding the agent's own method is
    # withheld, so continuing in the old frame is not one of the options.
    # The demand used to live at the search boundary only, which meant the
    # agent could spend its whole turn on something else and never meet it.
    owed = _review_owed_line(topic)
    if owed:
        lines.append(owed)
    # The phase decides the shape of the answer, not only the way of
    # thinking about it. A model that gathers evidence while it should be
    # weighing explanations produces a list of findings with no position in
    # it, and a list of findings is what the reader cannot use.
    phase = str(m.get("phase") or "").strip()
    if phase:
        shape = phase_output(phase)
        if shape:
            lines.append(f"这轮要交出的东西：{shape}")
        gate = phase_gate(phase)
        if gate:
            lines.append(f"这轮不要做：{gate}")
        # The ledger report appears in the phases that are about to
        # commit. Earlier than that, it would only tell the agent to
        # weaken what it has just proposed.
        if phase_strictness(phase) == "tight":
            problems = _ledger_problems(topic)
            if problems:
                lines.append(problems)
    # The frame comes next: what the project has already settled and what
    # it has not. It is the part of the map the agent cannot see by
    # looking at the question in front of it, and it is what stops the
    # same work being done twice.
    lines.extend(_frame(topic, m))
    # The situation comes next: the state of play, not a list of things
    # someone wrote down once.
    lines.extend(_situation(topic))
    if not any(
        ln.startswith(("当前最佳解释", "候选解释", "受质疑", "能分开")) for ln in lines
    ) and hyp:
        lines.append("竞争解释：" + " ｜ ".join(hyp))
    # The alignment check: the drift that no local check catches.
    lines.extend(_alignment(topic))
    # The thinking moves come before the claim rules. A mode that leads with
    # the format teaches the model to fill the format; a mode that leads with
    # the thinking teaches it to think, and the ledger is where the thinking
    # gets recorded.
    #
    # While a review is owed they are withheld rather than repeated. Repeating
    # them is what already failed: the agent read the reminder and kept
    # working, because nothing it needed had been taken away. The moves are
    # the one thing it cannot proceed without, so they are what the debt
    # costs, and they come back when the review is recorded.
    if owed:
        lines.append(
            "思维规则暂不发放：这个课题的审查一直没做。先调 "
            "research_mode(action='review', project=..., verdict='continue|redirect|pause', "
            "review_note='...') 把审查做掉，规则下一轮复原。"
        )
    else:
        lines.append(THINKING_RULES)
        lines.append(TURN_DEMAND)
        lines.append(SCOPE_RULE)
    lines.append(f"（以上仅属于 {topic} 课题，不要套用到别的话题。）")

    block = "\n".join(lines)
    # The fallback matches the shipped default. It used to be 900, which
    # is the old budget - and a config that failed to load would have
    # quietly cut the block to half its size, losing the standing and the
    # phase along with it. A cap that shrinks on error is a cap that
    # removes the thing it was protecting.
    cap = int((config or {}).get("max_inject_chars", 1600))
    if len(block) > cap:
        # The rules are reserved inside _truncate before the standing is
        # placed, so a long narrative cannot push them out.
        block = _truncate(block, cap)
    return block


# The line prefixes that survive the cut.
_KEEP_PREFIXES = (
    '[Research OS',
    '核心问题：',
    # The review debt. It is placed above the situation and has to survive
    # the cut, or a long narrative pushes the one line the agent owes out
    # of the block and the mechanism is decorative.
    '审查欠着：',
    '思维规则暂不发放：',
    '课题目标：',
    '当前阶段：',
    '当前任务：',
    '当前判断（课题叙事）：',
    '下一步计划：',
    '所属总课题：',
    '总课题的核心问题：',
    '同一总课题下的其他子课题：',
    '这轮要交出的东西：',
    '这轮不要做：',
    '账本有 ',
    '对齐检查',
    '  课题目标：',
    '  核心问题：',
    '  当前任务：',
    '  当前判断（课题叙事）：',
    '  下一步计划：',
    '  若当前任务',
    '当前最佳解释',
    '候选解释',
    '受质疑的解释',
    '能分开它的证据',
    '竞争解释：',
    # The frame: the questions already settled, the ones still open, and
    # the hypotheses the map is still holding.
    '已解问题（不用再查）：',
    '待解问题（还没解）：',
    '未处理的假设：',
    '如果用户这轮',
    '（以上仅属于',
)

def _rejoin_paragraphs(block: str) -> str:
    """Put the narrative and the plan back together as single lines.

    They are written as paragraphs - a blank line between them - and the
    block is cut line by line, so each paragraph of a long narrative
    became a separate line and the ones that matched no pattern were
    dropped. The agent saw a conclusion and the start of a reason, with
    the rest of the reasons gone and nothing to say so.
    """
    out: list[str] = []
    buf: list[str] = []
    buf_prefix = ""

    def flush() -> None:
        nonlocal buf, buf_prefix
        if not buf:
            return
        out.append(buf_prefix + " ".join(x.strip() for x in buf))
        buf = []
        buf_prefix = ""

    # A line that starts its own field. The narrative and the plan are
    # the two fields with paragraphs, so only their continuation lines
    # are absorbed - not the next field's header, which was being
    # swallowed into the plan and lost with it.
    own_field = re.compile(
        r"^(\[Research OS|核心问题：|课题目标：|当前阶段：|当前任务：|"
        r"审查欠着：|思维规则暂不发放：|"
        r"这轮要交出的东西：|这轮不要做：|对齐检查|已解问题|待解问题|"
        r"当前最佳解释|候选解释|受质疑的解释|能分开它的证据|竞争解释：|"
        r"未处理的假设：|如果用户这轮|（以上仅属于|做完整之后用|"
        r"回答前按科研|账本有|1\.|2\.|3\.|4\.|5\.|6\.|先给判断|不确定就写|"
        r"该判断该给|该探索的时候|按当前阶段)"
    )

    for raw in block.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            # A blank line inside a standing field means the field
            # continues.
            if buf:
                buf.append("")
            continue
        m = re.match(r"^(当前判断（课题叙事）：|下一步计划：)(.*)$", line)
        if m:
            flush()
            buf_prefix = m.group(1)
            buf = [m.group(2)]
            continue
        if buf and own_field.match(line):
            flush()
            out.append(line)
            continue
        if buf:
            buf.append(line)
            continue
        out.append(line)
    flush()
    return "\n".join(out)


def _truncate(block: str, cap: int) -> str:
    """Cut the block to fit, keeping the parts that act rather than the parts
    that are merely long.

    Two things are cut differently. A single long line - a core question
    typed by a person - is truncated, because there is nothing else to do
    with it. A rule that spans several lines is kept whole, because half a
    list of moves is worse than no list: the agent reads the heading of a
    set and none of the set.

    The order is: the three rule blocks first (they are what changes the
    agent's behaviour), then the situational lines (what state the topic is
    in), then whatever fits.
    """
    if len(block) <= cap:
        return block

    def clip(line: str) -> str:
        # A long situational line yields to the rules. The question can be
        # read in full in the topic file; the rules are what the agent needs
        # in front of it.
        limit = max(80, min(200, cap // 6))
        return line if len(line) <= limit else line[: limit - 1] + "…"

    def clip_standing(line: str, limit: int) -> str:
        """Cut a standing line to what the turn can use.

        The narrative and the plan are prose, and prose is written
        conclusion-first. Cutting the tail keeps the conclusion and the
        first reason. The full text is in the topic file.
        """
        if len(line) <= limit:
            return line
        return line[: max(40, limit - 1)] + "…（全文见课题文件）"

    blocks = [THINKING_RULES, TURN_DEMAND]
    # A tiny line that survives every cut: naming the tool call. Without it
    # an agent told to "record it" writes into prose, and the ledger stays
    # empty while the answer looks complete.
    # The standing and the alignment check come first: they are what the
    # turn is for, and the rules are how to work on it. Losing the standing
    # to keep a rule inverts the block.
    # What survives the cut, in what order.
    #
    # The order is the standing first, because the standing is what the
    # turn is for: the question, the goal, the phase, the task, what we
    # currently believe, what happens next. The narrative and the plan
    # are the two ends of the loop - a review that redirects writes them,
    # and the next turn reads them back - so they outrank the situation
    # lines, which are context the topic file already holds.
    #
    # A block that kept the evidence and lost the narrative had the
    # standing of a project that did not know what it had concluded.
    priority = re.compile(
        r"^(" + "|".join(re.escape(x) for x in _KEEP_PREFIXES) + r")"
    )
    # Three tiers. The first tier is what the turn is for - the standing,
    # the rules, the tool call. The second tier is the situation: the
    # competing explanations, the evidence that separates them. The
    # situation is real context, but it is also in the topic file, and it
    # is long - four lines of it took more room than the rules and the
    # tool call together.
    #
    # A block that kept the situation and lost the tool call was a
    # research session that read well and recorded nothing.
    keep_first = re.compile(
        r"^(\[Research OS|核心问题：|课题目标：|当前阶段：|当前任务：|"
        r"审查欠着：|思维规则暂不发放：|"
        r"当前判断（课题叙事）：|下一步计划：|这轮要交出的东西：|这轮不要做：|"
        r"所属总课题：|总课题的核心问题：|同一总课题下的其他子课题：|"
        r"如果用户这轮|（以上仅属于)"
    )
    keep_second = re.compile(
        r"^(对齐检查|当前最佳解释|候选解释|受质疑的解释|"
        r"能分开它的证据|竞争解释：)"
    )
    # The questions the project has already answered. They sit with the
    # standing, above the rules, because an agent that does not know a
    # question is settled will go and settle it again, and the search is
    # the expensive part. Repeating that work costs more than a few
    # characters of rules.
    # The settled questions and the open ones are both about where the
    # work is, and both sit with the standing, above the rules. A
    # question that is already answered is work nobody should be
    # repeating; a question that is still open is where the work is.
    # Getting either of them wrong costs a search, and a search costs
    # more than the rules do.
    #
    # They were split once because they were both below the rules, and
    # the rules took the space: a project with a long narrative and many
    # open questions lost the open questions, and the agent was left
    # with the settled ones and no idea what was still open.
    keep_settled = re.compile(
        r"^(已解问题（不用再查）：|待解问题（还没解）：)"
    )
    # The width of the hypothesis space is worth having and is not worth
    # the rules. It is a warning, not a place to work.
    keep_third = re.compile(r"^(未处理的假设：)")

    kept: list[str] = []
    used = 0
    # The narrative and the plan are paragraphs. Rejoin them so each is
    # clipped as a whole piece of prose, and so a cut is visible.
    block = _rejoin_paragraphs(block)
    lines = block.split("\n")
    second: list[str] = []
    third: list[str] = []
    settled: list[str] = []

    # The rules are reserved before anything else is placed. The
    # standing is what the turn is about and it is in the topic file;
    # the rules are what change how the agent works and they are
    # nowhere else. So when the two compete, the standing is the one
    # that gets cut.
    tool_line = "做完整之后用 research_mode(action='claim', project='<课题>', claim={...}) 记下来。"
    # While a review is owed the thinking rules are not in the block at all,
    # so reserving their cost would spend the budget on a line that is not
    # there and squeeze out the standing - the block would be short and the
    # debt line would be the first thing to go.
    rules_present = THINKING_RULES.split("\n")[0][:10] in block
    rules_cost = sum(len(b) + 1 for b in blocks) if rules_present else 0
    think_cost = len(blocks[0]) + 1
    tool_cost = len(tool_line) + 1
    reserved = rules_cost if rules_cost + 400 <= cap else (think_cost + tool_cost)
    standing_budget = max(0, cap - reserved - 120)

    # The standing fields, measured before anything is placed. One pass
    # in file order meant the last field had no room left by the time
    # it was reached and was dropped whole - a standing that says what
    # we believe and nothing about what happens next is half a standing,
    # and the half that is missing is the half the loop closes on.
    standing_lines: list[tuple[int, str, int]] = []  # (index, line, floor)
    for i, line in enumerate(lines):
        if any(line.startswith(b.split("\n")[0][:10]) for b in blocks):
            continue
        tier2 = keep_second.match(line)
        tier3 = keep_third.match(line)
        tier_settled = keep_settled.match(line)
        if tier2:
            second.append(clip(line))
            continue
        if tier3:
            third.append(clip(line))
            continue
        if tier_settled:
            settled.append(clip(line))
            continue
        if not keep_first.match(line):
            continue
        if line.startswith(("当前判断（课题叙事）：", "下一步计划：")):
            standing_lines.append((i, line, 120))
        else:
            standing_lines.append((i, clip(line), 0))

    # The prose fields share the standing budget, and each keeps a floor
    # so neither of them disappears.
    prose = [(i, ln) for i, ln, _f in standing_lines
             if ln.startswith(("当前判断（课题叙事）：", "下一步计划："))]
    per = max(120, standing_budget // max(1, len(prose)))
    clipped_standing: list[str] = []
    for idx, ln, _floor in standing_lines:
        if ln.startswith(("当前判断（课题叙事）：", "下一步计划：")):
            clipped_standing.append(clip_standing(ln, per))
        else:
            clipped_standing.append(ln)
    standing_cost = sum(len(x) + 1 for x in clipped_standing)

    # If the standing still does not fit, cut the prose fields further,
    # down to their floor, before dropping anything.
    if standing_cost > standing_budget:
        prose_idx = [k for k, ln in enumerate(standing_lines)
                     if ln[1].startswith(("当前判断（课题叙事）：", "下一步计划："))]
        over = standing_cost - standing_budget
        for k in prose_idx:
            if over <= 0:
                break
            cur = clipped_standing[k]
            floor = 120
            if len(cur) > floor:
                cut = min(over, len(cur) - floor)
                clipped_standing[k] = (
                    cur[: len(cur) - cut] + "…（全文见课题文件）"
                )
                over -= cut

    for (idx, _orig, _floor), ln in zip(standing_lines, clipped_standing):
        cost = len(ln) + 1
        if used + cost > cap - reserved:
            continue
        kept.append(ln)
        used += cost
        # The phase thinking is the line right under "当前阶段：". Without
        # it the phase is a label with nothing under it, and the thinking
        # the phase exists to install never reaches the agent.
        if ln.startswith("当前阶段："):
            nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
            if nxt and nxt.strip() and not keep_first.match(nxt) and not keep_second.match(nxt):
                nxt = clip(nxt)
                cost2 = len(nxt) + 1
                if used + cost2 <= cap:
                    kept.append(nxt)
                    used += cost2

    # The questions already settled go in with the standing, before the
    # rules. This is the one place where the frame outranks the way of
    # thinking, and it is deliberate: the value of the rules is that the
    # turn does research rather than bookkeeping, and the value of a
    # settled question is that the turn does not do it twice.
    for line in settled:
        cost = len(line) + 1
        if used + cost > cap:
            continue
        kept.append(line)
        used += cost

    # The rules go in as whole blocks. A half-list of moves is worse than
    # no list: the agent reads the heading of a set and none of the set.
    # So they are never cut - they are either in or not.
    #
    # The tool call line is the fallback. TURN_DEMAND ends with the same
    # instruction, so when the rules fit the line is redundant, and when
    # they do not fit, the line is what keeps the ledger alive. It is
    # shorter than either rule block, which is why it can be the one
    # that fits.
    if used + rules_cost <= cap:
        for b in blocks:
            kept.append(b)
            used += len(b) + 1
    elif used + think_cost + tool_cost <= cap:
        # The moves stay and the demand goes. The demand block says what
        # a turn has to produce, and the tool call says the same thing
        # about recording it; the moves are the part that cannot be
        # restated in one line, so they are the part that stays.
        kept.append(blocks[0])
        used += think_cost
        kept.append(tool_line)
        used += tool_cost
    elif used + tool_cost <= cap:
        kept.append(tool_line)
        used += tool_cost
    else:
        # Not even the tool call fits. Give up the longest standing lines
        # first - they are the most compressible, and a compressed
        # question still says what the project is about.
        for ln in sorted(kept, key=len, reverse=True):
            if used + tool_cost <= cap:
                break
            kept.remove(ln)
            used -= len(ln) + 1
        if used + tool_cost <= cap:
            kept.append(tool_line)
            used += tool_cost

    # The frame takes what is left after the rules: what is still open,
    # and how wide the space of hypotheses still is. It is worth more
    # than the situation, which is context the topic file already holds.
    for line in third:
        cost = len(line) + 1
        if used + cost > cap:
            continue
        kept.append(line)
        used += cost

    # The situation takes what is left. It is context, and it is in the
    # topic file, so it is the first thing to go when the block is tight.
    for line in second:
        cost = len(line) + 1
        if used + cost > cap:
            continue
        kept.append(line)
        used += cost

    if not kept:
        kept.append(clip(blocks[0]))
    else:
        line = clip(tool_line)
        cost = len(line) + 1
        if used + cost <= cap and not any(tool_line in k for k in kept):
            kept.append(line)
        elif used + cost > cap:
            # Make room by dropping the least important block first.
            for i in range(len(kept) - 1, -1, -1):
                if kept[i] in blocks and kept[i] is not blocks[0]:
                    freed = len(kept[i]) + 1
                    del kept[i]
                    used -= freed
                    if used + cost <= cap:
                        kept.append(line)
                        break

    out = "\n".join(kept)
    if len(out) > cap:
        out = out[: cap - 1] + "…"
    return out


def build_idle_block() -> str:
    """Injected in idle mode: almost nothing. Just enough that the agent knows
    the capability exists if a research question shows up mid-chore."""
    return (
        "[Research OS · idle]\n"
        "当前不在科研模式（无需四类断言纪律）。"
        "若用户这轮是研究问题（求解释、比较、查证、假设、证据），"
        "先调 research_mode(action='on', project=...) 进入对应课题，再按四类断言回答。"
    )
