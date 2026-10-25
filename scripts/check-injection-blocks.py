"""Acceptance check for the injection block, run against the real topics.

The fixtures in the test suite use a short core question. The real
topics have a long one, and the difference showed up as a bug the
fixtures could not see: the block kept the evidence and lost the
memory. So the check runs against the real state tree.

A fixture with a short string is not a real case.
"""
import os
import sys

sys.path.insert(0, "/home/twebery/research-os")
os.environ.setdefault("RESEARCH_OS_HOME", "/home/twebery/.hermes/research-os")

from research_os import protocol, state  # noqa: E402

# What a turn is for. If one of these is missing, the turn has forgotten
# what the work is for and is answering the last thing it was asked.
REQUIRED = [
    ("核心问题", lambda m, b: str(m.get("core_question") or "")[:10] in b),
    ("课题目标", lambda m, b: str(m.get("goal") or "")[:10] in b),
    ("当前阶段", lambda m, b: "当前阶段：" in b),
    ("阶段思考", lambda m, b: protocol.phase_thinking(m.get("phase") or "").split("\n")[1][:10] in b),
    ("当前任务", lambda m, b: str(m.get("current_task") or "")[:10] in b),
    ("当前判断", lambda m, b: (not m.get("narrative")) or str(m["narrative"])[:10] in b),
    ("下一步计划", lambda m, b: (not m.get("plan")) or str(m["plan"])[:10] in b),
    ("阶段门槛", lambda m, b: "这轮不要做" in b),
    ("输出形状", lambda m, b: "这轮要交出的东西" in b),
    ("科研思维", lambda m, b: "给竞争解释" in b),
    ("tool 调用行", lambda m, b: "research_mode(action='claim'" in b),
]

# The topic's frame has to reach the agent: the questions that are
# settled (so the work is not repeated), the ones that are not, and the
# competing hypotheses the current judgement may not cover.
#
# A topic with no resolved questions has no line to show, so the check
# is on the topic having them, not on the block always carrying them.
FRAME = [
    ("已解问题", lambda m, b: (not _has_resolved(m)) or "已解" in b),
    ("待解问题", lambda m, b: (not _has_open(m)) or "待解" in b or "还没解" in b),
    ("未处理假设", lambda m, b: (not _has_hyps(m)) or "假设" in b),
]


def _has_resolved(m):
    return bool(m.get("resolved_questions"))


def _has_open(m):
    return bool(m.get("open_questions"))


def _has_hyps(m):
    return bool(m.get("hypotheses"))

# The topics are found, not named. A check that lists the topics it
# checks only works for those topics, and it carries whoever's own
# research into a public repository.
TOPICS = [t["name"] for t in state.list_topics()] or ["<no topics>"]

fail = 0
for t in TOPICS:
    m = state.read_map(t)
    b = protocol.build_research_block(t, {"max_inject_chars": 1600})
    print("=" * 68)
    print("%s   %d 字   phase=%s" % (t, len(b), m.get("phase")))
    for label, check in REQUIRED:
        ok = check(m, b)
        print("   %-12s %s" % (label, "在" if ok else "*** 丢了 ***"))
        if not ok:
            fail += 1
    for label, check in FRAME:
        ok = check(m, b)
        print("   %-12s %s" % (label, "在" if ok else "*** 丢了 ***"))
        if not ok:
            fail += 1
    print()
    print("   --- 块内容 ---")
    for line in b.split("\n"):
        print("   | " + line[:104])
    print()

print("=" * 68)
print("必需项缺失:", fail)
sys.exit(1 if fail else 0)
