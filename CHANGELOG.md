# Changelog

All notable changes to Research OS are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] — 2026-10-25

### Added

- **The state tree is writable only through the tool.** A `pre_tool_call`
  guard blocks a direct write to the state tree, so the typed checks, the
  source requirement, the inference strength rule and the dedup all still
  apply to every row. Reading the state is allowed and necessary.

### Fixed

- The guard judges the write target, not whether the path is mentioned: a
  command that reads the ledger and writes a summary elsewhere is allowed,
  and a redirect only makes a path a target when it is immediately to the
  left of it.
- The package drops its own cached bytecode when it is imported, not only
  when the plugin loader loads it. The tests and the scripts import the
  package, and they were running old bytecode against new source.
- Concurrent runs on one topic no longer double-count a finding: the ledger
  keeps its own content key.

## [0.2.0] — 2026-10-25

### Added

- **`pre_tool_call` search guard** — a literature search is held until a
  topic exists, and a search run that is not converging is interrupted
  instead of being allowed to keep going.
- **`transform_llm_output` check** — the outgoing answer is compared against
  the ledger, and what was said but never recorded is appended to it.
- **`post_tool_call` accounting** — each turn records the literature tools
  it used and the claims it wrote, so a run that searched forty times and
  recorded nothing is visible.
- **Inference types** — `descriptive`, `explanatory`, `predictive`,
  `analogy`, `abductive`, each with its own obligation. An `abductive`
  inference must list at least one competitor.
- **Validator catches bundled claims** — one row may not carry an
  observation together with the conclusion drawn from it.
- **Leaving research mode in plain language** — "退出科研模式", "关掉研究模式",
  "结束课题". Hermes intercepts slash commands it does not know, so
  `/research off` never reaches the plugin.
- **Self-contained install** — the installer points at the published
  repository, honours `--enable`, and keeps the CLI next to the state.

## [0.1.0] — 2026-10-24

First working version.

### Added

- **`research_mode` tool** — the agent opens and closes research mode itself
  and manages topics.
  - Actions: `on`, `off`, `status`, `list`, `create`, `switch`, `delete`,
    `claim`, `validate`, `repair`.
  - `create` requires a `core_question`: a topic without a scientific question
    is not a research topic.
  - `delete` is a soft delete (moved to `.trash/`) by default.
  - `switch` keeps research mode on while changing topic.
  - `repair` rebuilds topic state that went missing while the registry still
    lists the topic.
- **`pre_llm_call` hook** — while a session is in research mode, injects the
  topic's core question, competing explanations, claim-ledger digest, and the
  four-claim-type discipline. Injected text is agent-facing; the user never
  sees it.
- **Four claim types** with different obligations:
  - `observation` — needs a source, read in the original.
  - `report` — needs a source; abstract-level verification suffices.
  - `inference` — needs premises that exist in the ledger, and may not be
    stated more strongly than its weakest premise.
  - `hypothesis` — needs no source, but must stay marked as a hypothesis.
- **Inference types** — `descriptive`, `explanatory`, `predictive`, `analogy`,
  `abductive` — recorded per claim, because each carries a different
  obligation.
- **Strength ladder** — `descriptive < suggestive < strong < established`. An
  inference may never exceed its weakest premise.
- **Ledger validation** (`validate`) — reports errors (malformed or
  overstated claims) and warnings (legal but shaky claims) separately.
- **Intent classification** — a chore request gets no protocol injection at
  all; an operation about a named object stays a chore even when research
  words appear in it.
- **Topic isolation** — the injection is built from one topic's files only;
  nothing is merged globally. Switching topics returns a reminder not to carry
  the previous topic's hypotheses over.
- **State layer** — plain JSON/TSV on disk: `research-map.json`, `claims.tsv`,
  `contradictions.json`, `decision-log.md`.
- **CLI** (`scripts/osctl.py`) — `init`, `topics`, `create`, `map`, `claim`,
  `claims`, `validate`, `delete`, `status`.
- **Obsidian renderer** (`scripts/render_obsidian.py`) — one-way bridge from
  the state tree to a browsable vault. Never writes back into the state.
- **Deploy script** (`scripts/deploy.sh`) and **one-shot installer**
  (`install.sh`).
- **31 tests** covering state, validation, classification, protocol, and the
  tool handler.

### Notes

- Plugin tools must return a string, not a dict — Hermes rejects other types.
- Hermes' argument coercion can deliver array-shaped fields as a bare dict
  wrapped in a list. The handler normalises every shape it might see, and the
  schema declares list-like fields as delimited strings.
- The injected block is capped (default 900 characters): every injected
  character breaks the prompt cache for the rest of the turn.
- Everything fails open. A broken Research OS degrades to a normal agent.

### Added (post-v0.1.0 verification)

- **`pre_tool_call` search guard** — a literature search outside research mode
  is blocked with a message saying what to do instead. This is the load-bearing
  hook: a code-specialised model reaches for PubMed before it thinks about the
  question, because retrieval is the shortest path to an answer. Verified
  end-to-end — the agent was blocked twice (PubMed, then web_search), then
  called `research_mode`, created a topic, and only then searched.
- **Bundled-claim detection** — a claim that carries both an observation and
  the conclusion drawn from it is flagged, with the split point identified and
  the rewrite suggested. This is the failure the ledger exists to catch, and
  the first end-to-end run produced it: "Remitted MDD retains objective
  cognitive deficits, so depression-related cognitive impairment cannot be
  reduced to concurrent emotional state", filed as a `report`.
- **`repair` action** — rebuilds topic state that went missing while the
  registry still lists the topic.
- **State rebuild on `on`** — entering a topic whose state directory is gone
  restores it from the registry instead of entering an empty topic.
- **Argument-shape normalisation** — the handler accepts every shape Hermes'
  argument coercion can deliver for a list-like field (declared list, bare dict
  wrapped in a list, plain string), and the schema declares list-like fields as
  delimited strings.
- **Line-level truncation** — the injected block caps each line before dropping
  lines, so a long core question cannot eat the whole budget.
- **Claim-tool-call wording** — the injected block names the exact tool call and
  gives the argument shape. Told only to "use the ledger", the agent classified
  claims in prose instead of recording them; the first end-to-end run produced
  no claim rows until this was fixed.

### Verified end-to-end

- The agent was blocked from searching, called `research_mode`, created a topic
  with a falsifiable core question and three competing explanations (including
  a third the design had not anticipated: "both, and separable"), then recorded
  a `report` claim with three PMIDs and left its status `pending`.
- The validator then flagged that claim as bundling an observation with a
  conclusion.

41 tests.

### Added (v0.2.0)

- **`post_tool_call` hook** — accumulates what the turn did, per turn id: how
  many evidence tools it used, and how many claims it wrote. A claim write
  counts only when the call was `action=claim` with content; creating or
  opening a topic is a different act.
- **`transform_llm_output` hook** — checks the outgoing answer against the
  ledger and appends a note listing the assertions that were never
  recorded. The answer itself is not rewritten. An empty ledger means every
  assertion is unaccounted for, which is the case worth flagging.
- **Gap report on the next turn** — when the previous turn gathered evidence
  and recorded nothing, the next turn's injected context says so before the
  agent builds another answer on top of it.
- **Private module namespace** — the implementation modules load under
  `research_os_impl.*` instead of flat top-level names, so they cannot
  collide with another plugin's `state.py` and cannot go stale behind a
  reloaded entry module.
- **Stale bytecode dropped on load** — a `.pyc` survives an edit that lands
  in the same second and leaves the size unchanged, and the old bytecode then
  runs against the new source. The loader removes it before each import.

### Verified end-to-end

A live run against PubMed: the agent named its own topic
(`Wang 2016 the topic it opened meta-analysis`), wrote four claims
with the correct kinds — a `report` for the study itself, two
`observation`s carrying effect sizes, an `inference` with `premises L1 L2 L3`
— then found a second paper and recorded it as L5 with PMID and DOI, `pending`.
In the answer it labelled observations and inferences inline and flagged the
print-year versus online-year discrepancy itself.

The output check then caught the one thing the agent said in prose but did
not record: the sentence about late-onset deficits implying vascular and
neurodegenerative factors. That sentence is in the answer and not in the
ledger, so nothing had checked whether it was an observation or an
inference.

76 tests.

### Added (v0.2.1)

- **Convergence check** — the search count is kept per topic, and once a
  topic passes `searches_before_converge` (default 20) the next search is
  interrupted with a request to record what has been found or to say what
  will be concluded. The check interrupts at most twice per topic: after that
  it lets the work through, because a hook that keeps refusing the same tool
  is a stall, and a stall is worse than a long search. Set the threshold to
  0 to turn it off.

  This came from a live run that searched fifty times across three turns,
  recorded nothing, and never produced an answer.

- The count belongs to the topic, so switching away does not erase it, and
  coming back with a new question starts a fresh count.
