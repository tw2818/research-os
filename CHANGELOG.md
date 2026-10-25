# Changelog

All notable changes to Research OS are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

- **The discipline was enforced at every moment of a project whose
  moments are not the same.** A model that must always have a position
  answers an open question with a position - so the exploration stage
  produced conclusions nobody checked, and the narrowing stage had
  nothing left to narrow. The phases now carry a strictness: `scoping`
  and `diverging` are open (a position is not required, a bold marked
  hypothesis is encouraged), `evidence` and `weighing` are mixed, and
  `closing` and `writing` are tight (a position is required and the
  ledger's problems are reported). The evidence discipline itself is the
  floor and never moves: a claim with no source is not a claim in any
  phase. What changed is when the agent is pushed, and when it is shown
  the ledger's problems - the checks run all along, but showing them
  during exploration only teaches the agent to weaken what it has just
  proposed.
- **The open phases forbade the two things an open phase is for.**
  `evidence` said "no conclusions, and do not change the question", and
  a question the evidence has disproven does not become a settled
  question - it becomes a wrong one. `diverging` said "no conclusions",
  which also took away the right to land on something. Both gates now
  say what the phase is for rather than what it must not do.
- **A narrative was a paragraph, and a paragraph was cut into pieces.**
  The block is assembled line by line, so the three paragraphs of a
  long narrative became three lines: the first matched the standing
  pattern and was clipped, the other two matched nothing and were
  dropped. The agent saw a conclusion and the start of a reason, with
  the rest silently gone. The narrative and the plan are now rejoined
  and clipped as prose.
- **A long standing took the whole injection budget, and the thinking
  rules were cut out of the block.** The block then said what the
  project was and nothing about how to work on it. The rules are
  reserved first now and the standing is cut to fit, because the
  standing is in the topic file and the rules are nowhere else. A cut
  standing says so, so the agent knows the narrative it has is short.
- **A cross-domain review had no way to be a review.** Topics are
  isolated on purpose - one topic's hypotheses must not leak into
  another - but a review spread over several sub-questions had no top
  topic and no account of how the evidence connects across them. A
  topic can now name a parent: it sees the question it serves and its
  siblings, and the parent's map shows the parts and where each one
  stands. The parent shares no ledger and no hypotheses; a hypothesis
  that leaks across is how a review ends up arguing for a position it
  never checked.
- **The review asked whether to keep going, and that is answerable with
  a shrug.** It now asks four questions, and the fourth has teeth: if
  continuing the search cannot be said to change any particular
  judgement, then continuing it is not worth it and the work should
  stop. The counters still decide when to ask - they are a checkpoint,
  not a judge.

## [0.11.0] — 2026-10-25

The rules come in layers, and the middle layer can be set aside on the
record. A system that treats every rule as a law is a bureaucracy; a system
that treats every rule as a suggestion is nothing at all.

- **The floor, the controls, the advice, the goal.** The floor (a claim
  without a source, a record changed without a trace, a premise that points
  nowhere, a ledger written outside the tool) is never waived - it protects
  the record itself. Three controls are good defaults and bad laws:
  two routes before leaving divergence, a frozen plan before searching, a
  review before writing. `action='waive'` sets one of them aside for the
  stage it guards, with a reason that is required, kept in `waivers.json`,
  shown in the injected block, written into the vault, and handed to the
  reviewer - who judges the reason rather than the presence.
- **The stage is a kind of research, not a number.** `mode='mapping'` joins
  `systematic` and `exploratory`: building the map of an area commits to
  its scope, because there is no claim to refute yet and demanding a
  falsifier produces a formal sentence that refutes nothing - and teaches
  the agent to write down things that are not true.
- **Progress is not only a route being weakened.** The evidence stage and
  the reviewer's "progression" axis now count knowing a previously unknown
  fact, sharpening a concept, noticing an ignored variable and connecting
  two fields as movement. A batch that neither decides a route nor enlarges
  the space is the only one with no criterion.
- **The reviewer does not reward caution.** Its instructions say that a
  hedged project does not score for hedging, and that a clearly marked
  hypothesis does not lose points for being bold. What loses points is
  writing a hypothesis as a conclusion.
- **A shared source has to resolve across topics.** "S1" is one topic's
  library id, so two branches writing "S1" are two papers; the digest now
  keys sources by DOI / PMID / Zotero key, and reports same-looking local
  ids as `local_source_collisions` rather than as shared evidence.
- **Confirming a directive is not the same as doing it.** `directive_progress`
  compares the branch's acknowledgement with its own plan timestamp, and the
  parent's block says which branches confirmed and did not move.
- **The negated words were still in the stopword list.** `not`, `no` and
  `never` are out, so "improved" and "did not improve" are no longer the
  same token bag. Double negation ("not absent", "无不") is read as a
  presence, and "not only" is not a negation.

## [0.10.0] — 2026-10-25

- **A judgement may cite a branch's ledger.** An inference's premise can be
  `branch-topic:L3`, and the validator reads that ledger — status, strength,
  source — instead of asking for the finding to be copied upward. Only the
  citing topic's parent, children and siblings may be cited: the same
  reference from an unrelated topic is refused (`foreign_premise`), because a
  conclusion imported from a stranger's ledger looks exactly like one that
  was reasoned to. The validator reports every borrowed premise in
  `cross_premises`, and the borrowed premise's strength participates in the
  "not stronger than its weakest premise" rule.
- **A digest is not a synthesis.** Syntheses now carry a state:
  `digest_generated` → `judgment_recorded` (the parent wrote narrative/plan
  back) → `integrated` (a parent claim's premise points into a branch the
  digest covered). A parent may not enter `converging`/`updating` while a
  closed branch sits at `digest_generated` — the old guard compared
  timestamps, so calling `synthesize` and moving on passed it.
- **Similar wording is not agreement.** Claim pairs are now reported with a
  `hint` (`wording_overlap` / `same_source` / `polarity_differs`) and an
  empty `relation` for the judgement step to fill. Negation detection pairs
  with the tokeniser, and `not` is deliberately absent from the stopword list:
  dropping it made "improved" and "did not improve" the same token bag, which
  reported a contradiction as a duplicate. Polarity-flipped pairs sort first
  — if two routes reached opposite conclusions, that is the finding.
- **The parent can direct a branch.** `action='directive'` records
  continue / redirect / pause / close per branch, with the reason, the next
  question and a priority; `action='acknowledge'` is the branch confirming it
  received it. Until acknowledged, the directive sits at the top of the
  branch's injected block. Without this the tree only flows upward: a branch
  reports, the parent nods, and the branch carries on with whatever it was
  already doing.
- **Exploratory reviews are not forced into a systematic protocol.**
  `mode="exploratory"` requires only the falsifier before freezing, instead
  of estimand + inclusion + falsifier. Freezing a guessed inclusion criterion
  is worse than an honest open question, and the falsifier is the one
  commitment that works for both kinds of review.

## [0.9.0] — 2026-10-25

- **The parent synthesises its branches, or it does not close the round.**
  `action='synthesize'` counts what only the parent can see: sources two
  branches both rely on, pairs of claims that say nearly the same thing,
  conflicts still open in any branch, and branches that closed after the
  parent last revised its narrative. It returns the demands the write-back has
  to answer, and a parent may not advance to `converging` or `updating` while
  a closed branch has not been synthesised. The digest is counted, not asked
  of a model: judgement stays with the agent, and a model handed no material
  invents the relationship between branches instead of finding it.
- **Chinese tokenising is by character bigram.** A run of Chinese has no
  spaces, so the first version tokenised a whole sentence as one token and two
  claims differing by three characters scored zero similarity.
- **The core question has one door.** `set_state` recorded the old question
  and the reason; `action='map'` wrote `m["core_question"]` straight into the
  file. Both now go through `add_core_question_change`, which refuses a change
  with no reason — a rule with a shorter bypass is not a rule.
- **A parent link may not close a loop.** `parent == topic` caught the
  shortest cycle only; a parent whose own parent is that topic made a cycle
  the depth walk could not leave. `would_cycle` walks the ancestor chain.
- **A pause lifts only on an answer to the pause.** Any successful `set_state`
  cleared it, including one that only recorded an exploration or a parent
  link — neither says where the work is going now. Lifting now needs
  `narrative`, `plan` or `core_question`, and the payload says
  `still_paused` when the note did not count.
- **`has_position` is documented as a floor, not a validator.** It matches
  words: it can say an answer has no position and cannot say whether the
  position is sound. The semantic check is the gate; the docstring now says
  so instead of leaving it to be inferred from the name.

## [0.8.0] — 2026-10-25

- **The reviewer needs a large output budget, and finds out the hard way.**
  The first four live runs came back with an empty reply: `usage` showed
  `output=8000` against `max_tokens=8000`, i.e. a reasoning model spending the
  whole budget thinking and writing nothing. A blank reply reads as "the gate
  failed" when it was the ceiling that failed, so `gate_max_tokens` is 32000
  by default and the record keeps `usage` and `reply_chars` to tell the two
  apart. A provider that accepts the schema and ignores it is covered by the
  plain-completion path, JSON parsed out of the text.

- **The process is a tree, not a line.** Six stations instead of one-way
  phases: 确认科研状态 / 发散路线 / 找证据 / 梳理理解 / 收敛 / 更新科研状态
  (plus 成文表达, kept for the draft). The old names translate on read, so
  a topic written under the old vocabulary keeps its history.
- **A route is a branch and a branch is a topic.** `branch` opens one, with
  `why_relevant` required — how it serves the parent's core question — and it
  becomes a child topic with its own ledger, so its evidence cannot leak into
  the parent's. `max_branches` (4) and `max_branch_depth` (2) bound the tree:
  a tree that grows without a bound is a folder of beginnings, not a review.
- **Closing a route costs a contribution.** What it established, what it
  ruled out, what it left open. A branch closed without that was abandoned,
  not finished.
- **Two stations cannot be left empty-handed.** Leaving 发散 needs two open
  routes; leaving 收敛 needs a passed review gate.
- **The review gate.** `action='gate'` calls a model that did not write the
  work — through the host's `ctx.llm` facade, so no key reaches the plugin —
  and hands it the standing rather than the last reply. Five axes (alignment /
  progression / grounding / convergence / deliverable), scores, and blocking
  issues pinned to a specific claim or route. Leaving 收敛 for 更新科研状态
  needs `pass` or `pass_with_caveat`.
- **What reviewed the work, and how it was decided.** A subagent is spawned
  by the agent under review, so it would have been an instruction rather than
  a control; a separate call cannot be skipped. `gate_pass_score` (7.0) is the
  line, `gate_max_rounds` (3) is the escape valve — after that the work goes
  through with the caveat in `gate-reviews.json` and in the vault, because a
  revision loop nobody escapes is worse than a written caveat. A reviewer that
  cannot run is also a caveat, never a deadlock.
- **The ledger guard stops blocking source maintenance.** It matched call
  names in the text, and the plugin's own source contains them, so editing
  `state.write_map` was refused by the guard that lives beside it. A guard
  that cannot tell "writing the ledger" from "editing the code that writes the
  ledger" teaches the person hitting it to route around it.
- **The vault gains the routes and the reviews**: `## 路线（分支）` on the map
  page, and `02-审稿记录.md` with scores, blocking issues and every earlier
  round.

## [0.7.0] — 2026-10-25

- **The plan is frozen before the searching starts.** The phases went from
  framing straight to gathering, so a run could search for hours and only then
  discover that the quantity it was collecting had never been defined — which
  is what happened here: nine count queries before anyone asked whether the
  short band and the long band were even the same estimand. `action='protocol'`
  records the estimand, the inclusion and exclusion criteria, the primary
  outcome, what would falsify the claim, and the Zotero collection the study
  searches and files against.
- **A search in `evidence` or `weighing` is refused until the plan is frozen.**
  `scoping` and `diverging` are exempt — exploring is what they are for — and
  `closing` and `writing` search to verify a citation, so a gate there would
  block the work it exists to protect. `require_protocol: false` turns it off.
- **Changing a frozen plan costs a reason.** `unfreeze_reason` unlocks it and
  the values being replaced are kept in `history`: a plan changed after the
  data arrived has to be visible as a change, or the freeze means nothing.
- **The plan renders into the vault** as `00-研究方案.md`, ahead of the ledger,
  because the ledger is only evidence if something was fixed in advance to
  judge it against. In Obsidian a Zotero key becomes a
  `zotero://select/library/collections/<key>` link; a collection name is
  printed as a name, because a link that goes nowhere is worse than a name.

## [0.6.3] — 2026-10-25

- **The status moved out of the answer and onto the stage change.** A footer on
  every answer reported the state at the end of each turn — which says where
  the work stopped and never how it got there, and says nothing at all on a
  turn that only calls tools. What was wanted is a line at the moment the work
  changes stage, posted as its own message: entering a topic, a change of
  phase, a review becoming due, a recorded review, leaving.
- Posted through `PluginContext.post_message`, which is the host's own channel:
  it posts into the session bound to the call and refuses outside a live
  gateway by returning a result instead of raising. Every failure is swallowed,
  because a status line must never break the action that triggered it.
- The same line twice is not a second transition, so re-entering the topic or
  setting the same phase again posts nothing. `transition_notice: false` turns
  it off; `status_line` stays, now off by default.

## [0.6.2] — 2026-10-25

- **Every answer now ends with one line of state.** The loop's state lives in
  files the user never opens — which topic, which phase, what this turn did,
  how big the ledger is, how far the last review is behind, whether a review
  is owed — so from the chat a long run is a black box that reports on itself,
  and nothing checks the report. The line is appended by
  `transform_llm_output`, the only hook that sees the text the user is about
  to read, and it is the state read back from the tree rather than the
  agent's account of it. `status_line: false` turns it off. It rides below
  any note the same hook already appends, and it never replaces an answer:
  the agent's prose is untouched above it.

## [0.6.1] — 2026-10-25

- **The counters could not see the work the loop is built on.** One live
  turn of research work recorded six claims in two batch calls and the
  session file said `ledger: 0`; the same turn read three pages and was
  counted as one search; and a `web_search` issued from inside
  `execute_code` was judged to be an unknown session and blocked, while the
  identical call made directly by the agent went through. Three separate
  blindings, and the review demand rides on all three numbers - so a turn
  could do its searching somewhere the counter cannot see and write its
  findings in the shape the counter does not recognise, and the loop would
  report that nothing had happened.
- **A batch write counts its rows.** `_is_claim_call` knew only the single
  `claim` shape, while `claims=[...]` is the form the tool's own
  description recommends for a round of searching: the more faithfully the
  tool was used, the less the loop could see. `_claim_rows` counts both
  shapes, and `note_tool_activity` takes a row count rather than a flag.
- **`web_extract` is evidence.** It was already a literature search at the
  guard (`SEARCH_TOOLS`) and absent from `EVIDENCE_TOOLS`, so the same tool
  answered two different questions - blocked as a search when idle, and
  invisible as a search once open.
- **A composite session id resolves to its parent.** Tools called from
  inside `execute_code`, or from a delegated child, arrive with
  `<parent>:<child>:<turn>`, which names no session file. Read as unknown,
  it made the guard see an idle conversation and refuse a search that
  belonged to an open topic. The parent is taken first; the pointer is the
  fallback only when the call named no session at all, because a named
  conversation must not be filed under whichever one spoke last.
- **A search from inside `execute_code` now reaches the counter.** The
  sandbox bridge calls its tools with no session and no turn, and the host
  suppresses `post_tool_call` inside the executor that owns the outer call,
  so everything done inside `execute_code` was invisible to the counters —
  which is how a turn can read six sources there and be judged as having
  read nothing. The pre hook does fire, so an allowed nested search is
  counted at that point: after the block checks, because a refused search
  is not work.

## [0.6.0] — 2026-10-25

Note on the versions before this one: `plugin.yaml` was bumped to 0.4.0 and
0.5.0 without changelog entries being written, so the headings above are
behind the manifest. This entry is the first one written since 0.3.0.

- **A reminder the agent could answer in prose and walk past.** The review
  demand lived at the search boundary only: it blocked twice, then became
  a note. A live topic read the demand, spent four turns on layout and
  translation work, and nothing anywhere said a review was still
  outstanding — the counters had been spent and the topic carried on as if
  nothing had been asked. A demand now opens a **debt** (`owed`,
  `owed_since`, `owed_turns`) that only a recorded review closes. While it
  is owed the debt line sits at the top of the injected block, survives the
  budget cut, and the thinking rules are **withheld** rather than repeated:
  repeating them is what already failed, so the debt costs the agent the one
  thing it cannot proceed without. The rules come back when the review is
  recorded.
- **The debt is visible to the reader, not only to the agent.** An agent can
  talk its way past a line in its own context; it cannot talk its way past
  something the user reads. When a review is owed and the answer did not
  make one, `transform_llm_output` appends a note naming how many times it
  has been asked and how many turns it has been owed, plus the one question
  the review has to answer. It is not added on the turn the review was
  recorded, which `note_session_reviewed` distinguishes by turn id rather
  than by timestamp.
- **The debt outlives its cap instead of being reset.** After four owed
  turns the nag stops — a hook that keeps refusing is a stall — but
  `abandoned_at` is written rather than the counters being cleared, so the
  topic keeps the fact that the review was never made.
- **A tool call that lost its session wrote to a session nobody was in.**
  `action='on'` through a bridge returned a full success payload while the
  conversation's session stayed idle, so every guard that reads the session
  saw an idle conversation and the agent was blocked from searching its own
  topic. The hooks always get the real session id, so they now record it in
  `<state>/.active_session` (bounded to 15 minutes), and the tool falls back
  to that. When nothing can be resolved, `on` and `off` **fail** with a
  message instead of answering with a payload that looks like success.

## [Unreleased]

## [0.3.0] — 2026-10-25

Five gaps, and the same shape in all of them: the work was fine and the
record of the work was missing.

### Added

- **The question can be redefined.** `set_state` takes `core_question`
  and `question_reason`, and the old question is kept with the reason it
  was replaced. A project that runs long sometimes finds that the
  question it is answering is not the question worth answering, and a
  tool that cannot record that forces the agent either to stay on a bad
  question or to smuggle the change in through the narrative, where
  nothing can see it.
- **A diverging phase.** A project that only ever narrows settles into
  the first frame it found. The new phase asks for the counterexample
  rather than the supporting evidence, and states that what it produces
  is a lead to chase, not a conclusion — the work goes back to
  `evidence`. What was looked for outside the frame is recorded
  (`exploration`), so the project knows it looked and did not find it.
- **The topic's frame reaches the agent.** The settled questions, the
  open ones, and the competing hypotheses the current judgement may not
  cover were all in the state file and none of them in the injected
  block — the block kept the evidence and lost the memory. The settled
  questions go in ahead of the rules, because a question that is already
  answered is work nobody should be repeating.
- **A literature library.** A claim needs a source, and a source is a
  thing that can be looked up again. Each topic keeps a small library
  (`sources.json`); a claim cites an entry by id (`S1`) and the ledger
  stores the entry's readable form, so the source resolves and the
  ledger reads. A source already in the library is not added twice.
- **A rendered map of the topic.** The notes were a ledger export. There
  is now a map, written in the order a researcher wants it: what the
  project is, where it stands, what is still open, and what the
  judgement rests on — prose, with the claim ids inline, so every
  statement in it can be traced back.
- **The notes do not need Obsidian.** A missing vault is not an error:
  the notes go to `<state home>/notes/<topic>/` as plain markdown and the
  reader is told where they went. `require_vault: true` restores the
  strict behaviour.
- **The literature backend is detected, not assumed.** The plugin cannot
  call a bibliography service, so it watches which tools the agent
  reaches for. If the agent is filing things in Zotero, the topic's
  sources go there; if it is not, they go to files. `literature_backend`
  in the config pins the preference.

### Fixed

- **The state tree could not be moved, and a stale export could move it
  by accident.** `RESEARCH_OS_HOME` is not visible to the agent: Hermes
  scrubs the environment of the tools it hands to the model, so a
  variable set for a one-shot process is gone by the time a hook runs.
  A pointer file is used instead — but it was read at import, and the
  plugin is loaded by a gateway that lives for months, so changing it
  needed a gateway restart. The path is now resolved on every use, and
  a changed pointer takes effect inside a process that has already
  imported the module.

  The pointer comes first, and it lives under `HERMES_HOME`, so the
  tree moves with the Hermes home. An export left in a shell redirected
  the installer, the CLI and the plugin all at once to a directory that
  had nothing to do with the install, and nothing said so. The pointer
  belongs to the installation; the environment variable is a
  per-process override, read only when there is no pointer.
- **A research question outside biomedicine was not recognised as
  research.** The classifier's word list was mostly biomedical and
  statistical — p values, effect sizes, cohort, meta-analysis — and its
  question shapes were the ones that end in a question mark. So "is it
  A or B", the shape most research questions have, was not a question
  at all, and a question about a text, a proof, a design or an
  argument was not a research request. The agent answered it with no
  research discipline and nothing said that anything was missing. The
  word list and the question shapes are both wider now.
- **A delete did not leave research mode.** "删除文件" was in the chore
  list and "删掉这个文件" was not. A chore is what takes a session out of
  research mode, so an unrecognised one left the agent in research mode
  with the research block still riding along on a turn that was
  deleting a file.
- **The rules carried one researcher's questions into every field.** The
  moves came from a bench experiment, and so did their wording: "what
  observation would separate these two explanations", "at least two
  rival explanations". A question settled by a proof, a reading of a
  text or a design constraint has no observation to make, and asking
  for one asks the wrong thing — so the question was being answered in
  the wrong shape. The moves now read "what material, test or line of
  reasoning", and they say outright that a question which should not be
  settled by separation needs a different test: what would show it
  right, what would show it wrong. The worked example in the rules
  named a syndrome, a deficit and a circuit; it is gone, and a test
  now checks the injected text for any field-specific content.
- **The open questions could be crowded out of the block.** The settled
  questions were given the standing's priority and the open ones were
  left below the rules, so a project with a long narrative and a full
  list of open questions lost the open questions and kept the settled
  ones — the agent was left knowing what was done and not knowing what
  was left. Both are about where the work is, and getting either of
  them wrong costs a search, so both now sit with the standing. The
  width of the hypothesis space stays below the rules: it is a warning
  that the judgement may be narrower than the question, not a place to
  work.
- **The state tree could not be moved.** `RESEARCH_OS_HOME` set the
  location, and it works for a script — but Hermes scrubs the
  environment of the tools it hands to the model, so the variable was
  gone by the time a hook ran. An agent told to work in a separate tree
  silently worked in the default one, and the only symptom was the
  files not being where they were supposed to be. The location is now
  also read from `~/.hermes/research-os-home` (one line, the path to
  use), which survives the scrub. The environment variable still wins
  when it is actually there.

### Changed

- The Zotero filing tools — creating an item, importing a PDF — count as
  evidence tools now, so a run that files rather than searches is
  counted for what it did.
- `scripts/check-injection-blocks.py` checks the block against the real
  topics, not against a fixture with a short question in it. A long real
  field crowds the standing out of the block, and a short fixture will
  not show it.

## [0.2.2] — 2026-10-25

The theme of this release: the agent kept doing the science and dropping
the memory of it. Every fix below is a case where the work was fine and
the record of the work was missing.

### Added

- **A way to revise a row without breaking the references.** `amend`
  changes a claim's content, strength, kind, source or status in place.
  The id stays, so every premise that cited it still points at the same
  claim, and the old values are kept in a new `amended_from` column. The
  agent used it to downgrade a claim from `strong` to `suggestive` after
  finding that the original wording claimed more than the evidence
  supported, and to rewrite the content rather than just the label.
- **A batch entry for claims.** One search usually turns up several
  findings, and one call per finding is a lot of round trips. `claims`
  takes a list; each row goes through the same checks as a single claim,
  and a row that fails validation is reported in `rejected` with its
  reason instead of taking the rest of the batch down with it.
- **A global review, on a condition, with teeth.** A topic is reviewed
  after enough searches or turns accumulate, and the review decides
  `continue`, `redirect` or `pause`. `pause` blocks further literature
  searching until the standing is updated, so the decision is a control
  rather than a suggestion.
- **A standing that survives the turn.** The goal, the phase, the current
  task, the narrative and the plan are read back at the start of every
  turn. The narrative and the plan are the two ends of the loop: a review
  that redirects writes them, and the next turn reads them back.
- **A review interval that can be set.** `review_every_searches` and
  `review_every_turns` in the config, and `review_every` in a topic's own
  map. Eight searches is most of a small topic and nothing on a large one.
- **A phase that changes the shape of the answer, not just its topic.**
  `scoping`, `evidence`, `weighing`, `closing`, `writing`. Each phase
  changes how the agent thinks and what it hands back, and each phase
  says what it is not for.

### Fixed

- **The standing had no room on real topics.** Every real topic has a
  long core question, and the question took the space: the narrative, the
  plan, the phase instruction and the tool call all fell off the end of
  the block, on all three topics. A block that survived had the evidence
  and lost what the project had concluded. The standing comes first in
  the cut now, and the situation lines give up the room - they are
  context the topic file already holds.
- **A cap that shrank on error.** The fallback budget was 900 while the
  shipped default was 1600, so a config that failed to load would have
  quietly cut the block in half, losing the standing and the phase with
  it.
- **A name could be taken forever.** A topic directory removed by
  anything other than `delete` left an orphan in the registry, and the
  orphan took the name hostage: creating a topic with that name was
  refused, so the name could never be used again. The orphan stays in the
  list, because `on` and `repair` find it there and rebuild it, but the
  name is free.
- **`status` answered about the session instead of the topic.** It
  ignored the `project` argument, so an agent asking about a topic got
  the session's state - idle, for any caller outside a session - and went
  to read the files instead. It now returns the topic's goal, phase,
  task, narrative, plan, claim count, review count and pause state.
- **A guard that failed open.** The guard on the state tree used `re`
  without importing it, and the exception was swallowed by the outer
  handler, so every check threw and every write was allowed. Guards that
  break are worse than no guard, because they report a clean sheet.
- **A batch that stopped at the first bad row.** One inference missing
  its type raised and the rest of the batch was lost, while the tool
  reported that it had recorded one claim and gave no reason.
- **A new column that was not written.** The amended-from values were
  filtered out by the writer, so a revision left no trace.

### Changed

- The thinking rules are the body of the injected block and the full
  assertion format lives in the tool description. A block whose job is
  to make the agent think should not be mostly a form.
- The rules are never cut in half. A half-list of moves is worse than no
  list, because the agent reads the heading of a set and none of the set.
  When they do not fit, the tool call line goes in instead - it is
  shorter than either rule block, and it is what keeps the ledger alive.

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
  the first end-to-end run produced it: "In the trial cohort the event rate
  was lower in the treated arm, so the treatment prevents the outcome
  rather than delaying it", filed as a `report`.
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

A live run against PubMed: the agent named its own topic from the paper it
was reading, wrote four claims with the correct kinds — a `report` for the
study itself, two `observation`s carrying effect sizes, an `inference` with
`premises L1 L2 L3` — then found a second paper and recorded it as L5 with
PMID and DOI, `pending`. In the answer it labelled observations and
inferences inline and flagged the print-year versus online-year discrepancy
itself.

The output check then caught the one thing the agent said in prose but did
not record: a sentence that named a subgroup effect and then attributed it
to a mechanism. That sentence is in the answer and not in the ledger, so
nothing had checked whether it was an observation or an inference.

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
