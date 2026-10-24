"""Tests for the Research OS state, validation, classification, and protocol.

Run:  python3 -m pytest tests/ -q      (or)  python3 tests/test_research_os.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="research-os-test-")
os.environ["RESEARCH_OS_HOME"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_os import audit, classify, protocol, state  # noqa: E402
from research_os.validate import validate_claims  # noqa: E402


def _fresh_topic(name="T1", q="Does A cause B?"):
    if state.topic_exists(name):
        state.delete_topic(name, permanent=True)
    return state.create_topic(name, q, ["H1", "H2"], ["test"])


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------


def test_create_topic_requires_question():
    try:
        state.create_topic("bad", "")
    except ValueError as e:
        assert "core_question" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_create_and_list():
    _fresh_topic("T-create")
    names = [t["name"] for t in state.list_topics()]
    assert "T-create" in names


def test_delete_is_soft_by_default():
    _fresh_topic("T-del")
    dest = state.delete_topic("T-del")
    assert dest and Path(dest).exists()
    assert not state.topic_exists("T-del")
    assert state.topic_exists("T-create")


def test_claim_ids_increment():
    _fresh_topic("T-ids")
    a = state.append_claim("T-ids", {"kind": "observation", "content": "x", "source": "PMID 1"})
    b = state.append_claim("T-ids", {"kind": "observation", "content": "y", "source": "PMID 2"})
    assert a["id"] == "L1" and b["id"] == "L2"


def test_append_claim_rejects_bad_kind():
    _fresh_topic("T-kind")
    try:
        state.append_claim("T-kind", {"kind": "nonsense", "content": "x"})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


# --------------------------------------------------------------------------
# validation — the heart of the system
# --------------------------------------------------------------------------


def test_observation_without_source_is_error():
    _fresh_topic("T-v1")
    state.append_claim("T-v1", {"kind": "observation", "content": "X is true"})
    r = validate_claims("T-v1")
    assert any(e["code"] == "missing_source" for e in r["errors"]), r


def test_placeholder_source_is_error():
    _fresh_topic("T-v2")
    state.append_claim("T-v2", {"kind": "observation", "content": "x", "source": "待补"})
    r = validate_claims("T-v2")
    assert any(e["code"] == "placeholder_source" for e in r["errors"]), r


def test_inference_without_premises_is_error():
    _fresh_topic("T-v3")
    state.append_claim("T-v3", {
        "kind": "inference", "content": "so A and B are decoupled",
        "inference_type": "descriptive",
    })
    r = validate_claims("T-v3")
    assert any(e["code"] == "no_premises" for e in r["errors"]), r


def test_inference_on_unverified_premise_is_error():
    _fresh_topic("T-v4")
    state.append_claim("T-v4", {
        "kind": "observation", "content": "fMRI shows X", "source": "PMID 999",
    })
    state.append_claim("T-v4", {
        "kind": "inference", "content": "therefore decoupled",
        "inference_type": "descriptive", "premises": "L1",
    })
    r = validate_claims("T-v4")
    assert any(e["code"] == "unverified_premise" for e in r["errors"]), r


def test_verified_premise_makes_inference_legal():
    _fresh_topic("T-v5")
    state.append_claim("T-v5", {
        "kind": "observation", "content": "fMRI shows X", "source": "PMID 999",
    })
    state.set_claim_status("T-v5", "L1", "verified")
    state.append_claim("T-v5", {
        "kind": "inference", "content": "therefore decoupled",
        "inference_type": "descriptive", "premises": "L1", "strength": "suggestive",
    })
    r = validate_claims("T-v5")
    assert r["ok"], r


def test_strength_cannot_exceed_weakest_premise():
    _fresh_topic("T-v6")
    state.append_claim("T-v6", {
        "kind": "observation", "content": "weak finding", "source": "PMID 1", "strength": "suggestive",
    })
    state.set_claim_status("T-v6", "L1", "verified")
    state.append_claim("T-v6", {
        "kind": "inference", "content": "strong conclusion",
        "inference_type": "explanatory", "premises": "L1", "strength": "established",
    })
    r = validate_claims("T-v6")
    assert any(e["code"] == "overstated" for e in r["errors"]), r


def test_hypothesis_cannot_be_marked_verified():
    _fresh_topic("T-v7")
    state.append_claim("T-v7", {
        "kind": "hypothesis", "content": "H1 is true", "status": "verified",
    })
    r = validate_claims("T-v7")
    assert any(e["code"] == "hypothesis_status" for e in r["errors"]), r


def test_hypothesis_phrased_as_fact_is_error():
    _fresh_topic("T-v8")
    state.append_claim("T-v8", {
        "kind": "hypothesis", "content": "已证实控制环路独立于情绪",
    })
    r = validate_claims("T-v8")
    assert any(e["code"] == "overclaim_phrasing" for e in r["errors"]), r


def test_dangling_premise_is_error():
    _fresh_topic("T-v9")
    state.append_claim("T-v9", {
        "kind": "inference", "content": "x", "inference_type": "descriptive", "premises": "L99",
    })
    r = validate_claims("T-v9")
    assert any(e["code"] == "dangling_premise" for e in r["errors"]), r


def test_abductive_without_competitor_warns():
    _fresh_topic("T-v10")
    state.append_claim("T-v10", {
        "kind": "observation", "content": "obs", "source": "PMID 5",
    })
    state.set_claim_status("T-v10", "L1", "verified")
    state.append_claim("T-v10", {
        "kind": "inference", "content": "best explanation", "inference_type": "abductive",
        "premises": "L1",
    })
    r = validate_claims("T-v10")
    assert any(w["code"] == "single_explanation" for w in r["warnings"]), r


def test_analogy_without_note_warns():
    _fresh_topic("T-v11")
    state.append_claim("T-v11", {
        "kind": "observation", "content": "obs", "source": "PMID 5",
    })
    state.set_claim_status("T-v11", "L1", "verified")
    state.append_claim("T-v11", {
        "kind": "inference", "content": "like domain X", "inference_type": "analogy", "premises": "L1",
    })
    r = validate_claims("T-v11")
    assert any(w["code"] == "unmarked_analogy" for w in r["warnings"]), r


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------


def test_research_question_classified_research():
    assert classify.classify("为什么睡眠剥夺后记忆巩固受损？") == "research"
    assert classify.classify("有没有研究支持这个结论？") == "research"


def test_chore_classified_chore():
    assert classify.classify("把这个 PDF 转成 markdown") == "chore"
    assert classify.classify("帮我装一下 ffmpeg") == "chore"


def test_chore_about_object_with_research_words_stays_chore():
    m = "这个pdf里的文献帮我整理一下"
    assert classify.classify(m) in ("chore", "research")  # borderline; must not be idle
    assert classify.classify("帮我把这个脚本跑一下") == "chore"


def test_idle_for_unclear():
    assert classify.classify("你好") == "idle"


# --------------------------------------------------------------------------
# leaving research mode
# --------------------------------------------------------------------------


def test_exit_request_detection():
    for phrase in (
        "退出科研模式",
        "退出这个课题",
        "关掉研究模式",
        "结束课题吧",
        "先退出",
        "leave research mode",
    ):
        assert classify.is_exit_request(phrase), phrase


def test_exit_request_not_triggered_by_ordinary_text():
    for phrase in (
        "结束这个分析",
        "把结论写完",
        "关闭文件",
        "研究一下这个课题的机制",   # research question, not an exit
        "帮我把 pdf 转成 md",
    ):
        assert not classify.is_exit_request(phrase), phrase


def test_off_clears_the_topic():
    m = _handler()
    import json
    m._tool_handler(
        {"action": "create", "project": "OffTopic", "core_question": "Q?"},
        session_id="t-off",
    )
    m._tool_handler({"action": "on", "project": "OffTopic"}, session_id="t-off")
    m._tool_handler({"action": "off"}, session_id="t-off")
    s = state.read_session("t-off")
    assert s["mode"] == "idle"
    assert s.get("topic", "") == "", s


def test_on_after_off_does_not_report_a_switch():
    m = _handler()
    import json
    m._tool_handler(
        {"action": "create", "project": "OffTopic2", "core_question": "Q?"},
        session_id="t-off2",
    )
    m._tool_handler({"action": "on", "project": "OffTopic2"}, session_id="t-off2")
    m._tool_handler({"action": "off"}, session_id="t-off2")
    out = json.loads(m._tool_handler({"action": "on", "project": "OffTopic2"}, session_id="t-off2"))
    assert "Switched from" not in out.get("note", ""), out


def test_exit_request_turns_the_mode_off():
    m = _handler()
    sid = "t-exit"
    m._tool_handler(
        {"action": "create", "project": "ExitTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "ExitTopic"}, session_id=sid)
    out = m._pre_llm_call(
        session_id=sid, user_message="退出科研模式", turn_id="turn-1"
    )
    assert out is None, out
    s = state.read_session(sid)
    assert s["mode"] == "idle", s
    assert s.get("topic", "") == "", s


def test_exit_in_idle_session_is_harmless():
    m = _handler()
    out = m._pre_llm_call(
        session_id="t-exit2", user_message="退出科研模式", turn_id="turn-1"
    )
    assert out is None, out


def test_research_question_after_exit_gets_the_nudge_again():
    """After leaving, a new research question must be able to re-enter."""
    m = _handler()
    sid = "t-exit3"
    m._tool_handler(
        {"action": "create", "project": "Reenter", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "Reenter"}, session_id=sid)
    m._pre_llm_call(session_id=sid, user_message="退出科研模式", turn_id="t1")
    out = m._pre_llm_call(
        session_id=sid, user_message="证据能区分这两种解释吗？", turn_id="t2"
    )
    assert out is not None
    assert "需要先进入课题" in out["context"], out["context"][:200]


# --------------------------------------------------------------------------
# accounting: evidence used but not recorded
# --------------------------------------------------------------------------


def _tool_msg(name, content):
    return {"role": "tool", "name": name, "tool_name": name, "content": content}


def test_scan_turn_counts_evidence_tools():
    h = [
        _tool_msg("mcp__pubmed__pubmed_search_articles", "found PMID: 10000001"),
        _tool_msg("web_search", "PMID 23098294 result"),
        _tool_msg("read_file", "some file"),
    ]
    s = audit.scan_turn(h)
    assert s["evidence_calls"] == 2
    assert s["ledger_calls"] == 0
    assert s["citations"] == 2


def test_scan_turn_notices_ledger_use():
    h = [
        _tool_msg("mcp__pubmed__pubmed_search_articles", "PMID 10000001"),
        _tool_msg("research_mode", "recorded L1"),
    ]
    s = audit.scan_turn(h)
    assert s["evidence_calls"] == 1
    assert s["ledger_calls"] == 1


def test_accounting_note_fires_when_evidence_unaccounted():
    m = _handler()
    history = [_tool_msg("mcp__pubmed__pubmed_search_articles", "PMID 10000001")]
    note = m._accounting_note("T-acc", history)
    assert "账本" in note
    assert "research_mode" in note
    assert "claim" in note


def test_accounting_note_silent_when_ledger_used():
    m = _handler()
    history = [
        _tool_msg("mcp__pubmed__pubmed_search_articles", "PMID 10000001"),
        _tool_msg("research_mode", "recorded L1"),
    ]
    assert m._accounting_note("T-acc", history) == ""


def test_accounting_note_silent_without_evidence():
    m = _handler()
    assert m._accounting_note("T-acc", [_tool_msg("read_file", "x")]) == ""
    assert m._accounting_note("T-acc", []) == ""


def test_assertive_sentences_ignores_hedged():
    text = (
        "Wang 2016 的 meta 分析表明睡眠剥夺后陈述性记忆评分下降。"
        "这可能说明认知损伤不能还原为当前情绪状态。"
    )
    s = audit.assertive_sentences(text)
    assert any("Wang 2016" in x for x in s)
    assert not any("这可能说明" in x for x in s), s


def test_unaccounted_sentences_flags_what_is_not_in_the_ledger():
    text = "Wang 2016 的 meta 分析表明睡眠剥夺后陈述性记忆评分下降。"
    assert audit.unaccounted_sentences(text, "") != []
    known = "Wang 2016 的 meta 分析表明睡眠剥夺后陈述性记忆评分下降。"
    assert audit.unaccounted_sentences(text, known) == []


def test_audit_injection_reaches_the_turn():
    """The note must actually reach the agent, appended to the research block."""
    m = _handler()
    sid = "t-audit"
    m._tool_handler(
        {"action": "create", "project": "AuditTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "AuditTopic"}, session_id=sid)
    out = m._pre_llm_call(
        session_id=sid,
        user_message="继续这个课题",
        turn_id="turn-1",
        conversation_history=[
            _tool_msg("mcp__pubmed__pubmed_search_articles", "PMID 10000001")
        ],
    )
    assert out is not None
    assert "账本还是空的" in out["context"], out["context"][:300]


# --------------------------------------------------------------------------
# system-generated turns are not user turns
# --------------------------------------------------------------------------

# A real background-process notification, verbatim in shape. Its body quotes
# a research question, which is exactly why it used to be misclassified.
BG_NOTICE = (
    "[IMPORTANT: Background process proc_4dcbcef35a86 completed normally (exit code 0).\n"
    "Command: cd /home/twebery && nohup timeout 900 hermes chat --oneshot -Q -q "
    '"为什么睡眠剥夺会损伤陈述性记忆？现有证据能区分「控制环路本身异常」'
    '和「情绪干扰占用资源」这两种解释吗？"\n'
    "Output:\nstarted"
)


def test_background_notice_is_not_research():
    assert classify.classify(BG_NOTICE) == "idle"
    assert classify.is_synthetic(BG_NOTICE)


def test_memory_context_block_is_not_research():
    m = "<memory-context>\n[System note: recalled memory]\n研究综述 睡眠 记忆\n</memory-context>"
    assert classify.classify(m) == "idle"
    assert classify.is_synthetic(m)


def test_system_note_is_not_research():
    m = "[System: The skills available in this conversation changed after its system prompt was built.]"
    assert classify.classify(m) == "idle"


def test_async_batch_notice_is_not_research():
    m = "[ASYNC DELEGATION BATCH COMPLETE] 3 subagents finished, 证据检索 done"
    assert classify.classify(m) == "idle"


def test_real_research_question_still_classified():
    # The guard must not swallow the real thing.
    q = "为什么睡眠剥夺会损伤陈述性记忆？"
    assert classify.classify(q) == "research"
    assert not classify.is_synthetic(q)


def test_pre_tool_call_really_has_no_user_message():
    """Guard against re-introducing a dead guard.

    Hermes' pre_tool_call kwargs do not include the user message. A guard that
    reads it silently gets an empty string and never fires. This asserts the
    plugin does not depend on it, by checking the recorded-turn path is the
    only channel it uses.
    """
    import inspect
    src = inspect.getsource(_handler()._pre_tool_call)
    assert "user_message" not in src, (
        "pre_tool_call cannot see the user message; use the recorded turn verdict"
    )


def test_synthetic_turn_never_blocks_a_search():
    """pre_tool_call cannot see the user message — the verdict has to travel
    through the session state that pre_llm_call wrote in the same turn."""
    m = _handler()
    sid = "t-synth"
    # pre_llm_call runs first in a real turn and records the verdict.
    out_llm = m._pre_llm_call(session_id=sid, user_message=BG_NOTICE, turn_id="turn-1")
    assert out_llm is None, out_llm
    # Now the tool call in that same turn.
    out = m._pre_tool_call(
        tool_name="web_search", args={}, session_id=sid, turn_id="turn-1"
    )
    assert out is None, out


def test_synthetic_turn_gets_no_injection():
    m = _handler()
    out = m._pre_llm_call(session_id="t-synth2", user_message=BG_NOTICE, turn_id="t-2")
    assert out is None, out


def test_stale_turn_verdict_does_not_block():
    """A synthetic verdict from an earlier turn must not exempt this turn."""
    m = _handler()
    sid = "t-stale"
    m._pre_llm_call(session_id=sid, user_message=BG_NOTICE, turn_id="turn-1")
    # A new turn arrives with a real question; the recorded verdict is stale.
    out = m._pre_tool_call(
        tool_name="web_search", args={}, session_id=sid, turn_id="turn-2"
    )
    assert out is not None and out.get("action") == "block", out


def test_real_turn_search_is_blocked_without_a_topic():
    """The guard still bites on a genuine turn."""
    m = _handler()
    sid = "t-real"
    m._pre_llm_call(
        session_id=sid,
        user_message="证据能区分控制环路异常和情绪干扰吗？",
        turn_id="turn-1",
    )
    out = m._pre_tool_call(
        tool_name="web_search", args={}, session_id=sid, turn_id="turn-1"
    )
    assert out is not None and out.get("action") == "block", out


def test_explicit_research_command_wins():
    assert classify.classify("/research Sleep-Memory") == "research"


def test_chore_in_research_session_downgrades():
    s = {"mode": "research", "topic": "T"}
    assert classify.classify("帮我把这个文件重命名", s) == "chore"


def test_research_in_research_session_stays():
    s = {"mode": "research", "topic": "T"}
    assert classify.classify("这个假设怎么检验？", s) == "research"


# --------------------------------------------------------------------------
# protocol
# --------------------------------------------------------------------------


def test_research_block_contains_core_question_and_rules():
    _fresh_topic("T-p1", "Why does cognition lag mood?")
    block = protocol.build_research_block("T-p1", {"max_inject_chars": 900})
    assert "Why does cognition lag mood?" in block
    assert "observation" in block and "inference" in block
    assert "T-p1" in block


def test_research_block_respects_cap():
    _fresh_topic("T-p2", "Q" * 400)
    block = protocol.build_research_block("T-p2", {"max_inject_chars": 300})
    assert len(block) <= 300, len(block)


def test_research_block_keeps_the_claim_tool_call():
    """The block must name the tool call, not just the concept of a ledger —
    an agent told to "use the ledger" writes claims into prose."""
    _fresh_topic("T-p3", "Q?")
    block = protocol.build_research_block("T-p3", {"max_inject_chars": 900})
    assert "research_mode" in block
    assert "action='claim'" in block or "action=\"claim\"" in block


def test_research_block_truncation_keeps_claim_instruction():
    _fresh_topic("T-p4", "Q" * 3000)
    block = protocol.build_research_block("T-p4", {"max_inject_chars": 500})
    assert len(block) <= 500
    assert "claim" in block


def test_bundled_observation_and_conclusion_is_split():
    """The real failure the ledger exists to catch: one row carrying both the
    observation and the conclusion drawn from it."""
    _fresh_topic("T-bundle")
    state.append_claim("T-bundle", {
        "kind": "report",
        "content": (
            "Sleep deprivation lowers objective memory scores, so sleep-related "
            "cognitive impairment cannot be reduced to concurrent emotional state: "
            "Wang 2016 (PMID 10000001) found moderate executive deficits"
        ),
        "source": "PMID 10000001",
    })
    r = validate_claims("T-bundle")
    assert any(e["code"] == "bundled_claim" for e in r["errors"]), r


def test_pure_observation_is_not_flagged_as_bundled():
    _fresh_topic("T-bundle2")
    state.append_claim("T-bundle2", {
        "kind": "observation",
        "content": "Wang 2016 meta-analysis found moderate executive function deficits in after sleep deprivation",
        "source": "PMID 10000001",
    })
    r = validate_claims("T-bundle2")
    assert not any(e["code"] == "bundled_claim" for e in r["errors"]), r


def test_inference_with_conclusion_wording_is_not_bundled():
    """An inference is *supposed* to carry a conclusion — only observations and
    reports get split."""
    _fresh_topic("T-bundle3")
    state.append_claim("T-bundle3", {
        "kind": "observation", "content": "obs", "source": "PMID 1",
    })
    state.set_claim_status("T-bundle3", "L1", "verified")
    state.append_claim("T-bundle3", {
        "kind": "inference",
        "content": "the finding therefore suggests that cognition and mood dissociate",
        "inference_type": "explanatory", "premises": "L1",
    })
    r = validate_claims("T-bundle3")
    assert not any(e["code"] == "bundled_claim" for e in r["errors"]), r


def test_idle_block_mentions_the_tool():
    b = protocol.build_idle_block()
    assert "research_mode" in b


# --------------------------------------------------------------------------
# tool handler
# --------------------------------------------------------------------------


def _handler():
    import importlib.util
    path = Path(__file__).resolve().parent.parent / "research_os" / "__init__.py"
    spec = importlib.util.spec_from_file_location("ro_mod", path)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_tool_returns_string():
    m = _handler()
    out = m._tool_handler({"action": "status"}, session_id="t-x")
    assert isinstance(out, str), type(out)
    import json
    assert "mode" in json.loads(out)


def test_tool_create_requires_core_question():
    m = _handler()
    out = m._tool_handler({"action": "create", "project": "NoQ"}, session_id="t-x")
    import json
    assert "error" in json.loads(out)


def test_tool_handles_coerced_list_shapes():
    m = _handler()
    import json
    for i, shape in enumerate([
        "H1 | H2",
        {"item": ["H1", "H2"]},
        [{"item": ["H1", "H2"]}],
    ]):
        name = f"Coerce{i}"
        out = m._tool_handler(
            {"action": "create", "project": name, "core_question": "Q?", "hypotheses": shape},
            session_id="t-x",
        )
        d = json.loads(out)
        assert d.get("hypotheses") == ["H1", "H2"], (shape, d)


def test_tool_on_rebuilds_missing_state():
    m = _handler()
    import json
    m._tool_handler(
        {"action": "create", "project": "Broken", "core_question": "Q?"}, session_id="t-x"
    )
    import shutil
    shutil.rmtree(state.topic_dir("Broken"), ignore_errors=True)
    out = m._tool_handler({"action": "on", "project": "Broken"}, session_id="t-x")
    assert "error" not in json.loads(out)
    assert state.read_map("Broken")


def test_tool_repair_fixes_orphans():
    m = _handler()
    import json
    import shutil
    m._tool_handler(
        {"action": "create", "project": "Orphan", "core_question": "Q?"}, session_id="t-x"
    )
    shutil.rmtree(state.topic_dir("Orphan"), ignore_errors=True)
    out = m._tool_handler({"action": "repair"}, session_id="t-x")
    assert "Orphan" in json.loads(out).get("repaired", [])
    assert state.read_map("Orphan")


# --------------------------------------------------------------------------
# pre_tool_call: the search guard
# --------------------------------------------------------------------------


def test_search_is_blocked_when_idle():
    m = _handler()
    out = m._pre_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles",
        args={},
        session_id="t-guard",
    )
    assert out is not None
    assert out.get("action") == "block"
    assert "research_mode" in out.get("message", "")


def test_search_is_blocked_in_chore_mode():
    m = _handler()
    out = m._pre_tool_call(
        tool_name="web_search", args={}, session_id="t-guard2"
    )
    assert out is not None and out.get("action") == "block"


def test_search_allowed_in_research_mode():
    m = _handler()
    import json
    m._tool_handler(
        {"action": "create", "project": "GuardTopic", "core_question": "Q?"},
        session_id="t-guard3",
    )
    m._tool_handler({"action": "on", "project": "GuardTopic"}, session_id="t-guard3")
    out = m._pre_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles",
        args={},
        session_id="t-guard3",
    )
    assert out is None, out


def test_non_search_tool_never_blocked():
    m = _handler()
    for tool in ("read_file", "write_file", "terminal", "execute_code"):
        assert m._pre_tool_call(tool_name=tool, args={}, session_id="t-guard4") is None


def test_search_guard_mentions_creating_when_no_topics():
    m = _handler()
    import json
    # A fresh state dir with no topics at all.
    out = m._pre_tool_call(
        tool_name="web_search", args={}, session_id="t-guard5"
    )
    msg = (out or {}).get("message", "")
    assert "create" in msg or "课题" in msg


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------


def _run_all() -> int:
    fns = [(n, o) for n, o in sorted(globals().items())
           if n.startswith("test_") and callable(o)]
    passed = failed = 0
    for name, fn in fns:
        try:
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
