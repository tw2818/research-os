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
| `post_message` | a **host channel** the plugin posts through | the loop is invisible from the chat: the topic, the phase and the counters live in files nobody opens, so a stage change is announced as its own short message |

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

**The station** changes how the agent thinks *and* what it hands back. The
process is a tree, not a line: a review fans out into routes, closes them one
at a time, and loops back.

| station | what it is for |
|---|---|
| `standing` | confirm the state: core question, goal, what the project believes and why |
| `diverging` | open routes — each one a branch — and say what would kill each |
| `evidence` | go find material, external or already on disk, tied to a route |
| `synthesis` | turn the evidence into understanding: which routes are still alive |
| `converging` | make the choice: keep one, cut the rest, say what it costs |
| `updating` | write the state back: Obsidian pages, Zotero collection, decision log |
| `writing` | turn the conclusion into something a reader can take |

Each station also states what it is *not* for ("`standing` does not go
looking for evidence yet", "`synthesis` does not add material", "`converging`
adds nothing"), which is how a request belonging to another station gets
noticed. Old names (`scoping`, `weighing`, `closing`) are translated on read,
so a state file written by an earlier version keeps its history.

**The tree is bounded, and the bounds are the point.** A route is a branch and
a branch is a topic: it gets its own ledger, narrative and source list, so
its evidence cannot leak into the parent's and the parent can always say which
route a claim came from. `max_branches` (default 4) caps how many may be open
at once, `max_branch_depth` (default 2) caps how far down the tree may go. A
tree that can grow without a bound is not a review, it is a folder of
beginnings.

**A route has to say what it is for.** `branch` requires `why_relevant` — how
this route serves the parent's core question — and closing one requires
`contribution`: what it established, what it ruled out, what it left open. A
branch closed without that was abandoned, not finished, and the parent would
never know which.

**Two stations cannot be left empty-handed.** Leaving `diverging` needs at
least two routes open (one route is not divergence, it is claiming the first
idea). Leaving `converging` needs a passed review gate — see below.

**`diverging` exists because a project that only ever narrows settles
into the first frame it found.** The phase asks for the counterexample
rather than the supporting evidence: what observation would overturn the
current reading, is there a completely different way to ask the question,
and which materials the frame is treating as settled that were never
checked. It produces leads worth chasing, not conclusions — the work goes
back to `evidence` to look. What was looked for outside the frame is
recorded (`set_state` with `exploration`), so the project knows it looked
and did not find it.

**The question itself can be redefined.** A project that runs long
sometimes finds that the question it is answering is not the question
worth answering. `set_state` with `core_question` and `question_reason`
records that, and keeps the old question with the reason it was replaced
— a question that quietly changed is a question the next session cannot
place.

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

## The review gate

Every synthesis in this literature gets reviewed by someone who did not write
it, and the verdict decides whether it goes out. Research OS had the phases
but not that reader: the agent judged its own last move, always found that it
advanced the project, and moved the station itself.

`research_mode(action='gate', project=...)` calls a model that did not write
this — through the host's `ctx.llm` facade, so the plugin never sees a key —
hands it the standing (core question, narrative, plan, ledger, routes) rather
than the last message, and asks for five scores plus blocking issues pinned to
a specific claim, route or paragraph:

| axis | the question |
|---|---|
| `alignment` | is this answering the core question, or a nearby question that is easier to answer |
| `progression` | what is known now that was not known before; is each round standing on the last |
| `grounding` | does the claim's strength match the ledger's evidence |
| `convergence` | was a choice actually made, and was the cost of it said out loud |
| `deliverable` | can a reader take a position away |

Leaving `converging` for `updating` requires a `pass` or a `pass_with_caveat`.

**Three decisions, each with a cost.**

*A separate call, not a subagent.* A subagent is spawned by the agent being
reviewed, so the gate would be an instruction ("go get reviewed") rather than
a control, and the verdict would reach the state file as the agent's own
report. A separate call is made by the plugin itself: it cannot be skipped,
and what lands in the record is what the reviewer said.

*The state, not the reply.* Judging one reply rewards a fluent paragraph. The
material submitted is the standing — which is the thing that has to hold
together.

*The score is a signal, not a sentence.* Scores are not objective, and a gate
that waits for a perfect score waits forever. `gate_pass_score` (default 7.0)
is the line, `gate_max_rounds` (default 3) is the escape valve: after that
many rounds the work goes through as `pass_with_caveat`, with the reason
written into `gate-reviews.json` and rendered into the vault. A visible caveat
beats a revision loop nobody escapes. A reviewer that cannot run at all is
also a caveat, never a deadlock: blocking every project on an infrastructure
failure is worse than letting it through with "nobody reviewed this" on the
record.

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

## The literature library

A claim needs a source, and a source is a thing that can be looked up
again. A bare string in the ledger says "PMID 12345" and nothing else; an
entry in the topic's library says what the source is, where it lives, and
whether it can be re-read.

| kind | what it is | the locator |
|---|---|---|
| `zotero` | a bibliography service holds it | the item key |
| `file` | a document on disk | the path |
| `ref` | a bare identifier, nothing behind it | the DOI or PMID |

```python
research_mode(action="source", project="X", source_kind="zotero",
              source_locator="ABCD1234", source_title="A paper")
# -> {"source": {"id": "S1", ...}, "cite_as": "S1 A paper [zotero:ABCD1234]"}

research_mode(action="claim", project="X",
              claim={"kind": "observation", "content": "...", "source": "S1"})
# the ledger stores: S1 A paper [zotero:ABCD1234]
```

The id is what makes the source resolvable; the entry's own form is what
makes the ledger readable without the library open. A source already in
the library is not added twice.

**The fallback is a real fallback.** The plugin cannot call a
bibliography service, so it cannot ask whether one is there — but it can
watch which tools the agent reaches for. If the agent is filing things in
Zotero, the topic's sources go there; if it is not, they go to files, and
`kind="file"` with the path is the whole answer. `literature_backend` in
the config pins the preference (`auto`, `zotero`, `files`).

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
    sources.json            the literature library
    branches.json          the routes this topic opened: route, why it
                            serves the core question, status, the child topic
                            it became, and what closing it contributed
    gate-reviews.json      every review the gate made, with scores, blocking
                            issues and the verdict, kept so a project waved
                            through reads that way
    protocol.json          the plan, frozen before the searching starts:
                            the estimand, inclusion/exclusion, the primary
                            outcome, what would falsify the claim, and the
                            Zotero collection the study searches and files
                            against
```

`research-map.json` is not a literature summary. It is the current state of
understanding, and it is supposed to change as evidence accumulates.

**A topic can name a parent.** A cross-domain review is several
sub-questions under one question, and the isolation between topics is
deliberate — one topic's hypotheses must not leak into another. But a
review spread over separate topics had no top topic and no account of
how the evidence connects across them, so the same point got made twice
or a gap opened between the parts.

`set_state` with `parent` links a topic to the one it serves. The
sub-topic then sees the parent's question and its siblings, and the
parent's map shows the parts and where each one stands. The parent
shares no ledger and no hypotheses: a hypothesis that leaks across is
how a review ends up arguing for a position it never checked. The
horizontal link — "these two parts touch the same evidence" — is
something the review has to write down, because the ledger cannot see
it.

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

### Moving the state tree

Three places say where the state lives, and they are used in this order.

**The pointer file** — `$HERMES_HOME/research-os-home`, one line, the
path to use. The installer writes it. It comes first because it belongs
to the installation, and because an export left in a shell is a
temporary override that someone forgot about: one of those redirects
the installer, the CLI and the plugin all at once to a directory that
has nothing to do with the install, and nothing says so.

**`RESEARCH_OS_HOME`** — the per-process override, for a script that
wants a different tree for one run. It is read only when there is no
pointer file.

**The default** — `$HERMES_HOME/research-os`.

The pointer is read on every use, not once at import. The plugin is
loaded by the gateway, and the gateway is a long-running process, so a
path decided at import time would need a gateway restart to change —
which is the wrong shape for a file whose whole purpose is to say where
the tree is without restarting anything.

```bash
echo /path/to/state > ~/.hermes/research-os-home
```

**Hermes scrubs the environment of the tools it hands to the model**,
so `RESEARCH_OS_HOME` set for a one-shot process is gone by the time a
hook runs. That is why the pointer file exists, and why it is a file
rather than a variable.

---

## The rendered notes

Each topic renders to a set of notes:

| file | what it is |
|---|---|
| `00-课题地图.md` | the map: what the project is, where it stands, what is left, and what the judgement rests on |
| `00-当前认知.md` | the current view |
| `01-证据账本.md` | the ledger |
| `02-决策与转向.md` | the decision log |

The map is the one to read first. It is written as prose in the order a
researcher would want it — what the project is about, where it stands,
what is still open, and what the judgement rests on — with the claim ids
inline, so every statement in it can be traced back to the ledger.

```bash
python3 scripts/render_obsidian.py --topic X      # one topic
python3 scripts/render_obsidian.py --all          # every topic
python3 scripts/render_obsidian.py --all --plain  # plain markdown, no Obsidian syntax
python3 scripts/render_obsidian.py --all --map-only
```

**Two state trees, two vaults.** The renderer reads the tree the pointer
file names, so a second tree has to be asked for by name:

```bash
python3 scripts/render_obsidian.py --all --home /path/to/other/tree
```

That is what a test instance needs: its own state tree, its own vault,
its own notes, and the production notes untouched. `scripts/switch-tree.sh`
points a profile at one tree or the other, and prints where each profile
points.

**Without Obsidian, the notes still get written.** A missing vault is not
an error: the notes go to `~/.hermes/research-os/notes/<topic>/` as plain
markdown, and the reader is told where they went. Set `require_vault` to
`true` for the strict behaviour. `notes_dir` moves the fallback
elsewhere.

## Configuration

`~/.hermes/research-os/config.json`:

| key | default | what it does |
|---|---|---|
| `obsidian_vault` | - | where the rendered notes go |
| `obsidian_subdir` | `ResearchOS` | folder inside the vault |
| `obsidian_enabled` | `true` | set `false` to skip the vault rendering |
| `inject_protocol` | `true` | set `false` to turn the whole thing off |
| `max_inject_chars` | `1600` | how much protocol text rides along |
| `status_line` | `false` | `true` appends one line of state to every answer. Off by default: a line per answer says where the work stopped and never how it got there |
| `transition_notice` | `true` | post a short status message into the chat when the work changes stage: entering a topic, a change of phase, a review becoming due, a recorded review, leaving |
| `searches_before_converge` | `20` | searches on one topic before the agent is asked to record or conclude; `0` turns the check off |
| `review_every_searches` | `8` | searches on one topic before a global review is demanded |
| `review_every_turns` | `6` | turns on one topic before a global review is demanded |
| `literature_backend` | `auto` | `auto` uses a bibliography service if the agent has one, and files if it does not; `zotero` and `files` pin it |
| `require_vault` | `false` | `true` makes a missing Obsidian vault an error instead of falling back to plain markdown |
| `require_protocol` | `true` | in the `evidence` and `synthesis` stations, refuse a literature search until the topic's plan is frozen with `action='protocol'` and `freeze=true` |
| `require_gate` | `true` | refuse to leave `converging` for `updating` until the review gate has passed |
| `gate_pass_score` | `7.0` | the average score that counts as a pass |
| `gate_max_rounds` | `3` | after this many reviews the work goes through as `pass_with_caveat`, with the reason recorded |
| `gate_max_tokens` | `32000` | the reviewer's output budget. A reasoning model spends most of it thinking; too small a budget comes back with an empty reply that looks like a gate failure |
| `gate_model` | - | review with a different model than the one writing. Needs `plugins.entries.research-os.llm.allow_model_override`; a refused override falls back to the active model and the record says which was used |
| `gate_provider` | - | the provider for `gate_model`; needs `allow_provider_override` |
| `max_branches` | `4` | how many routes one topic may hold open at once |
| `max_branch_depth` | `2` | how deep the route tree may go |
| `notes_dir` | - | where the notes go when there is no vault (default: `<state home>/notes/`) |

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
research_mode(action="set_state", project="X", parent="The-Review")
research_mode(action="map", project="X", open_questions="Q1? | Q2?")
research_mode(action="question", project="X", question="Q that is still open")
research_mode(action="question", project="X", closed="the old question",
              question="what answered it")
research_mode(action="hypothesis", project="X", hypothesis="an untested idea")
research_mode(action="repair", project="X")
research_mode(action="delete", project="X")
research_mode(action="review", project="X", verdict="redirect", review_note="...")
research_mode(action="protocol", project="X", estimand="...",
              inclusion="A | B", falsifier="...", freeze=True)
research_mode(action="branch", project="X", route="...", why_relevant="...")
research_mode(action="branch", project="X", branch_id="B1",
              branch_status="concluded", contribution="...")
research_mode(action="gate", project="X", gate_reason="...")
research_mode(action="source", project="X", source_kind="zotero",
              source_locator="ABCD1234")
research_mode(action="library", project="X")
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
