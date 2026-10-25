"""Check that every real topic's injection block still carries the standing.

The unit tests build a topic with a short question. The real topics have a
long one, and the difference is what breaks: a long core question crowds
the standing out of the block, and a short fixture will not show it. So
this check runs against the real topics in the runtime state directory.

    python3 scripts/check-injection-blocks.py

Exit code 1 if any topic is missing a load-bearing part of the block.
Run it after any change to the block, the truncation order, or the budget.
"""

import os
import sys

# The plugin source is the repo, not this skill directory: the skill holds
# the notes, the repo holds the code that builds the block. The deployed
# copy is flat (state.py at the top level), so it cannot be imported as
# the package.
_repo = os.environ.get("RESEARCH_OS_REPO") or "/home/twebery/research-os"
if not os.path.isdir(os.path.join(_repo, "research_os")):
    sys.stderr.write(
        "cannot find the research-os repo at %s\n"
        "set RESEARCH_OS_REPO to its path\n" % _repo
    )
    raise SystemExit(2)

sys.path.insert(0, _repo)

from research_os import protocol, state  # noqa: E402

# The parts of the block the turn is for. Checked by a stable prefix
# rather than by the content, so a reworded line does not read as a
# dropped line.
REQUIRED = (
    "当前阶段：",          # the phase and its name
    "当前任务：",          # what is being worked on right now
    "这轮要交出的东西：",  # the shape of this turn's answer
    "这轮不要做：",        # the phase gate
    "给可能答案",          # the thinking rules (the line was reworded when
                           # the field-specific example was removed; the
                           # check kept the old phrase and reported every
                           # topic as broken)
    "research_mode(action='claim'",  # the tool call
)


def missing_parts(topic: str, cap: int = 1600):
    m = state.read_map(topic)
    block = protocol.build_research_block(topic, {"max_inject_chars": cap})
    missing = [part for part in REQUIRED if part not in block]
    for field in ("narrative", "plan"):
        value = str(m.get(field) or "").strip()
        if value and value[:10] not in block:
            missing.append(field)
    phase = str(m.get("phase") or "").strip()
    if phase:
        entry = protocol.PHASES.get(phase)
        if entry and entry["thinking"][:12] not in block:
            missing.append("phase thinking")
    return block, missing


def main() -> int:
    topics = [t["name"] for t in state.list_topics()]
    if not topics:
        print("no topics yet — nothing to check")
        return 0
    bad = 0
    for topic in topics:
        block, missing = missing_parts(topic)
        if missing:
            bad += 1
            print("  %-32s %4d chars  MISSING: %s"
                  % (topic, len(block), ", ".join(missing)))
        else:
            print("  %-32s %4d chars  ok" % (topic, len(block)))
    if bad:
        print("\n%d topic(s) lost part of the standing." % bad)
        return 1
    print("\nall %d topics carry the standing." % len(topics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
