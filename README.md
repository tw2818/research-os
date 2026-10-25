# Research OS

A research-thinking controller for [Hermes](https://hermes-agent.nousresearch.com).

The problem: general-purpose models are code-specialised. They finish tasks. They
do not ask whether a question is worth asking, whether two explanations are
actually distinguishable, or whether the sentence they just wrote is an
observation or a guess wearing an observation's clothes.

Research OS changes *how the agent reasons*, not what it knows.

---

## How it works

| | what it is | who drives it |
|---|---|---|
| `research_mode` | a **tool** the agent calls | the agent decides to enter research mode |
| `pre_llm_call` | a **hook** that injects context | the system decides how the agent behaves |
| `pre_tool_call` | a **hook** that holds a search | the system keeps the search attached to a question |
| `post_tool_call` | a **hook** that records what happened | the system tracks evidence and records |
| `transform_llm_output` | a **hook** that checks the outgoing answer | the system catches what was said but never recorded |

The tool is the switch; the hooks are the reflex.

The agent has context the hooks do not (what the user actually wants), so the
agent decides *whether* this is research. The hooks then change *how* the
agent answers for the rest of the session.

**The user never sees any of this.** The protocol rides Hermes's
`pre_llm_call` context channel, which is appended to the user message as
agent-facing context. A chore request gets no injection at all.

Leaving research mode works in plain language — "退出科研模式", "关掉研究模式",
"结束课题" — because Hermes intercepts slash commands it does not know, so
`/research off` never reaches the agent.

---

## The loop

This is the part that makes it a research assistant rather than a
citation checker. Five stations, and the loop closes in both directions:

```
inject the standing -> decide which question the current task advances ->
work -> judge whether the result changed the direction and whether more
input is worth it -> update the standing
```

**The standing** is what survives between turns: the core question, the
goal, the phase, the current task, the narrative and the plan. The
narrative is what we currently believe and why it changed; the plan is
what happens next. A direction change has to be written down somewhere,
not just felt — otherwise the next session reads the ledger and infers a
direction that was never chosen.

**The phase** changes how the agent thinks *and* what it hands back:

| phase | what it is for |
|---|---|
| `scoping` | frame the question, list the explanations that could compete |
| `evidence` | go find material, keep it tied to a question |
| `weighing` | decide between the explanations, say what separates them |
| `closing` | stop adding, write the conclusion |
| `writing` | turn the conclusion into something a reader can take |

Each phase also states what it is *not* for ("scoping does not go looking
for evidence yet", "closing does not add any"), which is how a request
belonging to another phase gets noticed.

**The global review** happens on a condition, not on demand: after enough
searches or turns have piled up, the agent has to look at the whole
project and decide `continue`, `redirect` or `pause`. A `pause` verdict
**blocks further literature searching** until the standing is updated, so
the decision is a control rather than a suggestion. It is asked at the
search boundary — the last moment before more input goes in, which is the
only place a "stop" can change what happens next.

**The judgement is on countable signals**, not on feel: evidence arrived
and no explanation moved; the claims landed on a different subject than
the core question; enough searches or turns have gone by. A drift that
takes three turns to appear is not visible in any one of them.

The counter is per topic, not per turn — a runaway run accumulates across
turns, and a per-turn counter resets exactly when it matters. The review
interval is a setting: `review_every_searches` and `review_every_turns`
in the config, and `review_every` in a topic's own map, because eight
searches is most of a small topic and nothing on a large one.

---

## The four claim types

The core discipline. A blanket "every sentence needs a citation" rule would
forbid reasoning itself, so claims are typed instead, and each type carries a
different obligation.

| kind | what it is | obligation |
|---|---|---|
| `observation` | a fact | needs a source, read in the original |
| `report` | someone else's conclusion | needs a source; abstract-level check is enough |
| `inference` | your own reasoning | needs premises that exist in the ledger, and may not be stated more strongly than its weakest premise |
| `hypothesis` | an untested idea | needs no source, but must stay marked as a hypothesis |

An inference is legitimate when its premises are legitimate — not when it has a
citation. Strength ladder:

```
descriptive  <  suggestive  <  strong  <  established
```

An inference may never exceed its weakest premise. `validate` enforces this.

Inference types are recorded too, because they carry different obligations:

- `descriptive` — describing what was observed
- `explanatory` — proposing a mechanism
- `predictive` — deriving a prediction from a mechanism
- `analogy` — importing a mechanism across domains (must be marked; never
  claims the target domain has verified it)
- `abductive` — offering the simplest explanation (must list at least one
  competing explanation)

---

## Topics

A topic is one research question and the state that accumulates around it:

```
<research-os-home>/topics/<topic>/
    research-map.json      the standing: core question, goal, phase,
                           current task, narrative, plan; plus competing
                           hypotheses and open questions
    claims.tsv             the claim ledger
    contradictions.json     competing explanations and what separates them
    decision-log.md         what was decided, what was abandoned, what would change it
    review.json             reviews made, the last verdict, whether it is paused
    search-count.json       searches run on this topic, and how often it was interrupted
```

`research-map.json` is not a literature summary. It is the current state of
understanding, and it is supposed to change as evidence accumulates.

`claims.tsv` is tab-separated with a fixed header: `id`, `kind`,
`inference_type`, `strength`, `content`, `premises`, `source`, `status`,
`note`, `amended_from`. The last column holds the amendment history of a
row that was revised, so a change to a recorded claim is on the record
rather than silent. The reader is keyed on the header row, so a file
written before a column was added still parses.

**A recorded row is amended, never replaced.** `action='amend'` with
`claim_id` revises a row in place: the id stays, so every other row that
cites it still points at the right thing, and the previous values are
kept in `amended_from`. Deleting the row and adding it back would give it
a new id, which breaks every row that cited it.

**The state tree is writable only through the tool.** A guard blocks a
direct write to it, so the typed checks, the source requirement, the
inference strength rule and the dedup all still apply to every row.
Reading the state is allowed and necessary.

Deleting a topic is a soft delete (moved to `.trash/`) by default: the state
contains thinking that may have taken weeks.

---

## Install

One shot, in a normal shell — not inside an agent session, because the
installer prints the gateway restart you need to do afterwards:

```bash
curl -fsSL https://raw.githubusercontent.com/tw2818/research-os/main/install.sh | bash \
  --vault /path/to/your/obsidian/vault
```

Or by hand:

```bash
hermes plugins install tw2818/research-os
hermes plugins enable research-os
```

Then point it at your Obsidian vault and initialise the state tree:

```bash
RESEARCH_OS_HOME=~/.hermes/research-os \
  python3 ~/.hermes/research-os/bin/osctl.py init --vault /path/to/vault
```

The installer leaves the plugin **copied but not enabled** unless you pass
`--enable`, and it prints the exact command to enable it. That is deliberate:
enabling a plugin changes how every conversation behaves, so it should be a
decision, not a side effect of an install script.

The vault side is for reading: the agent writes the state, a renderer turns it
into prose you can browse. The machine-readable side stays in the state tree so
a diff shows what actually changed.

Update later with:

```bash
hermes plugins update research-os
```

### Installed layout

```
~/.hermes/
  plugins/research-os/        the plugin: plugin.yaml + the modules
  research-os/
    config.json               vault path, thresholds
    sessions/<id>.json        which topic a conversation is in
    topics/<topic>/           the research state (see above)
    bin/                      osctl.py and the renderer, with their modules
```

---

## Configuration

`~/.hermes/research-os/config.json`:

| key | default | what it does |
|---|---|---|
| `obsidian_vault` | — | where the rendered notes go |
| `obsidian_subdir` | `ResearchOS` | folder inside the vault |
| `obsidian_enabled` | `true` | set `false` to skip the vault rendering |
| `inject_protocol` | `true` | set `false` to turn the whole thing off |
| `max_inject_chars` | `1600` | how much protocol text rides along |
| `searches_before_converge` | `20` | searches on one topic before the agent is asked to record or conclude; `0` turns the check off |
| `review_every_searches` | `8` | searches on one topic before a global review is demanded |
| `review_every_turns` | `6` | turns on one topic before a global review is demanded |

A topic can override the review interval in its own `research-map.json`
with `review_every: {"searches": n, "turns": n}`, which the agent can set
through `set_state`.

The convergence check interrupts a search run that is not converging — it
found one that searched fifty times, recorded nothing, and never answered.
The check interrupts rather than forbids, and it interrupts at most twice per
topic: a hook that keeps refusing the same tool is a stall, and a stall is
worse than a long search.

---

## Using it

You do not need to learn any commands. The agent opens research mode when the
question warrants it, and closes it when you move on. Ask for it in plain
language — "研究一下这个课题", "开科研模式", "退出科研模式" — and the agent
enters and leaves accordingly.

Leaving works in plain language for a reason: Hermes resolves slash commands
itself and rejects the ones it does not know, so `/research off` never
reaches the plugin.

Direct tool use:

```python
research_mode(action="on", project="Sleep-Memory")
research_mode(action="off")
research_mode(action="list")
research_mode(action="status", project="X")
research_mode(action="create", project="X", core_question="...")
research_mode(action="switch", project="Y")
research_mode(action="claim", project="X", claim={...})
research_mode(action="claim", project="X", claims=[{...}, {...}])
research_mode(action="amend", project="X", claim_id="L44", strength="suggestive")
research_mode(action="contradict", project="X", title="...", separator="...")
research_mode(action="set_state", project="X", phase="weighing", narrative="...")
research_mode(action="review", project="X", verdict="redirect", review_note="...")
research_mode(action="validate", project="X")
```

**A round of searching usually turns up several findings at once**, and a
tool that only accepts one leaves the agent looking for another way to
write them. `claims=[...]` takes the whole round in one call, and every
row goes through the same checks as a single claim — the batch is the
same validation applied once per row, not a way around it. A row that
fails validation is reported with its reason and the rows after it still
land.

Creating a topic requires a `core_question`. An agent that cannot state the
question should not create the topic.

---

## CLI

```bash
python3 scripts/osctl.py init
python3 scripts/osctl.py topics
python3 scripts/osctl.py create "Sleep-Memory" --question "..."
python3 scripts/osctl.py claim "Sleep-Memory" --kind observation \
    --content "..." --source "PMID 12345"
python3 scripts/osctl.py validate "Sleep-Memory"
```

---

## Tests

```bash
python3 tests/test_research_os.py
```

---

## Design notes

**Everything fails open.** A broken Research OS must degrade to "a normal
agent", never to "a stalled agent". The hook catches every exception and
returns `None`.

**The injection is capped.** Every injected character breaks the prompt cache
for the rest of the turn, so the block is capped (default 1600 chars) and the
least load-bearing lines are dropped first. The standing outranks the
situational lines, because the narrative and the plan are the two ends of
the loop; and the thinking rules are cut as whole units, because half a
list of moves reads as a thought and does not act like one.

**Chores stay chores.** A request to convert a file gets no protocol
injection. The scope rule is inside the injected block itself, and the
classifier treats an operation about a named object as a chore even when
research words appear.

**Topics do not leak into each other.** The injection is built from one
topic's files only; nothing is merged globally. Switching topics returns a
reminder not to carry the previous topic's hypotheses over.

---

## License

MIT
