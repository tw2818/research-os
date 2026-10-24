# Changelog

All notable changes to Research OS are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
