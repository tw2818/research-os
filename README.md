# Research OS

A research-thinking controller for [Hermes](https://hermes-agent.nousresearch.com).

The problem: general-purpose models are code-specialised. They finish tasks. They
do not ask whether a question is worth asking, whether two explanations are
actually distinguishable, or whether the sentence they just wrote is an
observation or a guess wearing an observation's clothes.

Research OS changes *how the agent reasons*, not what it knows.

---

## How it works

Two moving parts:

| | what it is | who drives it |
|---|---|---|
| `research_mode` | a **tool** the agent calls | the agent decides to enter research mode |
| `pre_llm_call` | a **hook** that injects context | the system decides how the agent behaves |

The tool is the switch; the hook is the reflex.

The agent has context the hook does not (what the user actually wants), so the
agent decides *whether* this is research. The hook then changes *how* the
agent answers for the rest of the session.

**The user never sees any of this.** The protocol rides Hermes's
`pre_llm_call` context channel, which is appended to the user message as
agent-facing context. A chore request gets no injection at all.

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
    research-map.json      core question, competing hypotheses, open questions
    claims.tsv             the claim ledger
    contradictions.json     competing explanations and what separates them
    decision-log.md         what was decided, what was abandoned, what would change it
```

`research-map.json` is not a literature summary. It is the current state of
understanding, and it is supposed to change as evidence accumulates.

Deleting a topic is a soft delete (moved to `.trash/`) by default: the state
contains thinking that may have taken weeks.

---

## Install

```bash
hermes plugins install <owner>/research-os
hermes plugins enable research-os
```

Then point it at your Obsidian vault and initialise the state tree:

```bash
python3 scripts/osctl.py init --vault /path/to/vault
```

The vault side is for reading: the agent writes the state, a renderer turns it
into prose you can browse. The machine-readable side stays in the state tree so
a diff shows what actually changed.

Update later with:

```bash
hermes plugins update research-os
```

---

## Using it

You do not need to learn any commands. The agent opens research mode when the
question warrants it, and closes it when you move on.

To be explicit:

```
/research Sleep-Memory      enter research mode on a topic
/chore                        leave it
```

Direct tool use:

```python
research_mode(action="on", project="Sleep-Memory")
research_mode(action="off")
research_mode(action="list")
research_mode(action="create", project="X", core_question="...")
research_mode(action="switch", project="Y")
research_mode(action="claim", project="X", claim={...})
research_mode(action="validate", project="X")
```

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
for the rest of the turn, so the block is capped (default 900 chars) and the
least load-bearing lines are dropped first.

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
