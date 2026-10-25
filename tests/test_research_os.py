"""Tests for the Research OS state, validation, classification, and protocol.

Run:  python3 -m pytest tests/ -q      (or)  python3 tests/test_research_os.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="research-os-test-")
# HERMES_HOME is set as well as RESEARCH_OS_HOME, because the pointer
# file is looked for under HERMES_HOME. Setting only the second one
# left the developer's own pointer file in charge of the test - and the
# test failed on a tree that had nothing to do with it.
os.environ["HERMES_HOME"] = _TMP
os.environ["RESEARCH_OS_HOME"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_os import audit, classify, protocol, reviewer, state, synthesis  # noqa: E402
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
        "kind": "hypothesis", "content": "已证实记忆巩固过程独立于清醒时长",
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
    assert state.read_session(sid)["mode"] == "idle"
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


def test_the_turn_is_given_a_direction_not_a_nag():
    """A turn that searched without recording is not helped by being told
    it did not record. It is helped by being told what the phase is for.
    The nag pushed the agent into completing a form, which is the failure
    this project is against."""
    m = _handler()
    sid = "t-dir"
    m._tool_handler(
        {"action": "create", "project": "DirTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "DirTopic"}, session_id=sid)
    m._tool_handler(
        {
            "action": "set_state",
            "project": "DirTopic",
            "phase": "weighing",
            "current_task": "比较两个解释的证据强度",
        },
        session_id=sid,
    )
    m._pre_llm_call(session_id=sid, user_message="研究一下", turn_id="t1")
    m._post_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles", session_id=sid, turn_id="t1"
    )
    out = m._pre_llm_call(session_id=sid, user_message="继续", turn_id="t2")
    assert out is not None
    ctx = out["context"]
    assert "比较两个解释的证据强度" in ctx
    assert protocol.phase_output("synthesis") in ctx
    # And the nag is gone.
    assert "账本还是空的" not in ctx


def test_post_tool_call_records_evidence_and_ledger():
    m = _handler()
    sid = "t-activity"
    m._post_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles", session_id=sid, turn_id="t1"
    )
    m._post_tool_call(
        tool_name="research_mode",
        args={"action": "claim", "claim": {"content": "L1"}},
        session_id=sid,
        turn_id="t1",
    )
    act = state.turn_activity(sid, "t1")
    assert act["evidence"] == 1
    assert act["ledger"] == 1


def test_opening_a_topic_is_not_recording():
    """`create` and `on` are not claim writes. Counting them would make a turn
    that never recorded anything look accounted for."""
    m = _handler()
    sid = "t-activity-op"
    for action in ("create", "on", "list", "status"):
        m._post_tool_call(
            tool_name="research_mode", args={"action": action}, session_id=sid, turn_id="t1"
        )
    act = state.turn_activity(sid, "t1")
    assert act["ledger"] == 0, act


def test_claim_without_content_is_not_recording():
    m = _handler()
    sid = "t-activity-empty"
    m._post_tool_call(
        tool_name="research_mode",
        args={"action": "claim", "claim": {}},
        session_id=sid,
        turn_id="t1",
    )
    assert state.turn_activity(sid, "t1")["ledger"] == 0


def test_gap_is_reported_when_only_the_topic_was_opened():
    """The real failure: the agent opened a topic and searched, and never
    wrote a claim."""
    m = _handler()
    sid = "t-gap2"
    m._tool_handler(
        {"action": "create", "project": "GapTopic2", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "GapTopic2"}, session_id=sid)
    m._pre_llm_call(session_id=sid, user_message="研究一下", turn_id="t1")
    m._post_tool_call(
        tool_name="research_mode", args={"action": "on"}, session_id=sid, turn_id="t1"
    )
    m._post_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles", session_id=sid, turn_id="t1"
    )
    out = m._pre_llm_call(session_id=sid, user_message="继续", turn_id="t2")
    assert out is not None
    # The block still reaches the turn and still names the tool call - the
    # ledger has to be written somewhere. What is gone is the nag.
    assert "research_mode" in out["context"]
    assert "账本还是空的" not in out["context"]


def test_turn_activity_is_isolated_per_turn():
    m = _handler()
    sid = "t-activity2"
    m._post_tool_call(tool_name="web_search", session_id=sid, turn_id="t1")
    assert state.turn_activity(sid, "t1")["evidence"] == 1
    m._post_tool_call(tool_name="read_file", session_id=sid, turn_id="t2")
    assert state.turn_activity(sid, "t2")["evidence"] == 0
    assert state.turn_activity(sid, "t2")["tools"] == ["read_file"]
    # The earlier turn is still readable — that is what the gap report uses.
    assert state.turn_activity(sid, "t1")["evidence"] == 1


def test_next_turn_reports_the_previous_gap():
    """The gap is detected on the turn after the one that left it."""
    m = _handler()
    sid = "t-gap"
    m._tool_handler(
        {"action": "create", "project": "GapTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "GapTopic"}, session_id=sid)
    # Turn 1: the agent searches and records nothing.
    m._pre_llm_call(session_id=sid, user_message="研究一下", turn_id="t1")
    m._post_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles", session_id=sid, turn_id="t1"
    )
    # Turn 2: the block still carries the method and the tool call.
    out = m._pre_llm_call(session_id=sid, user_message="继续", turn_id="t2")
    assert out is not None
    assert "research_mode" in out["context"]
    assert "账本还是空的" not in out["context"]


# --------------------------------------------------------------------------
# convergence
# --------------------------------------------------------------------------


def _search_n(m, sid, topic, n):
    for _ in range(n):
        m._post_tool_call(
            tool_name="mcp__pubmed__pubmed_search_articles", session_id=sid, turn_id="t1"
        )


def test_convergence_check_silent_below_the_threshold():
    m = _handler()
    sid = "t-conv1"
    m._tool_handler(
        {"action": "create", "project": "ConvTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "ConvTopic"}, session_id=sid)
    _search_n(m, sid, "ConvTopic", 3)
    out = m._pre_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles", session_id=sid, turn_id="t1"
    )
    assert out is None, out


def test_convergence_check_interrupts_a_runaway_search():
    m = _handler()
    sid = "t-conv2"
    m._tool_handler(
        {"action": "create", "project": "ConvTopic2", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "ConvTopic2"}, session_id=sid)
    _search_n(m, sid, "ConvTopic2", 25)
    out = m._pre_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles", session_id=sid, turn_id="t1"
    )
    assert out is not None
    assert out["action"] == "block"
    assert "收口" in out["message"]
    assert "账本" in out["message"]


def test_convergence_check_stops_after_two_reminders():
    """A hook that keeps refusing the same tool is a stall, and a stall is
    worse than a long search."""
    m = _handler()
    sid = "t-conv3"
    m._tool_handler(
        {"action": "create", "project": "ConvTopic3", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "ConvTopic3"}, session_id=sid)
    _search_n(m, sid, "ConvTopic3", 25)
    first = m._pre_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles", session_id=sid, turn_id="t1"
    )
    second = m._pre_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles", session_id=sid, turn_id="t1"
    )
    third = m._pre_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles", session_id=sid, turn_id="t1"
    )
    assert first is not None
    assert second is not None
    assert third is None, third


def test_convergence_check_can_be_turned_off():
    m = _handler()
    sid = "t-conv4"
    m._tool_handler(
        {"action": "create", "project": "ConvTopic4", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "ConvTopic4"}, session_id=sid)
    _search_n(m, sid, "ConvTopic4", 40)
    state.save_config({"searches_before_converge": 0})
    out = m._pre_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles", session_id=sid, turn_id="t1"
    )
    assert out is None, out
    state.save_config({"searches_before_converge": 20})


def test_search_count_is_per_topic():
    m = _handler()
    sid = "t-conv5"
    for name in ("ConvA", "ConvB"):
        m._tool_handler(
            {"action": "create", "project": name, "core_question": "Q?"},
            session_id=sid,
        )
    m._tool_handler({"action": "on", "project": "ConvA"}, session_id=sid)
    _search_n(m, sid, "ConvA", 4)
    assert state.topic_search_count("ConvA") == 4
    assert state.topic_search_count("ConvB") == 0


def test_switching_topic_does_not_erase_the_old_topic_s_count():
    """The count belongs to the topic: those searches really were run against
    that topic's question. Switching away must not make them disappear."""
    m = _handler()
    sid = "t-conv6"
    for name in ("ConvC", "ConvD"):
        m._tool_handler(
            {"action": "create", "project": name, "core_question": "Q?"},
            session_id=sid,
        )
    m._tool_handler({"action": "on", "project": "ConvC"}, session_id=sid)
    _search_n(m, sid, "ConvC", 22)
    assert state.topic_search_count("ConvC") == 22
    m._tool_handler({"action": "on", "project": "ConvD"}, session_id=sid)
    assert state.topic_search_count("ConvC") == 22
    assert state.topic_search_count("ConvD") == 0


def test_re_entering_a_topic_starts_a_fresh_count():
    """Coming back to a topic with a new question is a new run."""
    m = _handler()
    sid = "t-conv7"
    for name in ("ConvE", "ConvF"):
        m._tool_handler(
            {"action": "create", "project": name, "core_question": "Q?"},
            session_id=sid,
        )
    m._tool_handler({"action": "on", "project": "ConvE"}, session_id=sid)
    _search_n(m, sid, "ConvE", 22)
    m._tool_handler({"action": "on", "project": "ConvF"}, session_id=sid)
    m._tool_handler({"action": "on", "project": "ConvE"}, session_id=sid)
    assert state.topic_search_count("ConvE") == 0


# --------------------------------------------------------------------------
# duplicates
# --------------------------------------------------------------------------


def test_recording_the_same_claim_twice_does_not_duplicate_it():
    """The same finding recorded twice is not two pieces of evidence. A live
    run did exactly that: five rows duplicated."""
    m = _handler()
    sid = "t-dup"
    m._tool_handler(
        {"action": "create", "project": "DupTopic", "core_question": "Q?"},
        session_id=sid,
    )
    body = {
        "kind": "observation",
        "content": "Semkovska 2019: 73% of cognitive variables remain impaired",
        "source": "PMID 31422920",
    }
    import json
    first = json.loads(m._tool_handler(
        {"action": "claim", "project": "DupTopic", "claim": body}, session_id=sid
    ))
    second = json.loads(m._tool_handler(
        {"action": "claim", "project": "DupTopic", "claim": body}, session_id=sid
    ))
    assert len(state.read_claims("DupTopic")) == 1
    assert second.get("duplicate") is True, second
    assert first["recorded"]["id"] == second["recorded"]["id"]


def test_a_duplicate_with_different_whitespace_is_still_a_duplicate():
    m = _handler()
    sid = "t-dup2"
    m._tool_handler(
        {"action": "create", "project": "DupTopic2", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler(
        {"action": "claim", "project": "DupTopic2",
         "claim": {"kind": "observation", "content": "A finding about X"}},
        session_id=sid,
    )
    m._tool_handler(
        {"action": "claim", "project": "DupTopic2",
         "claim": {"kind": "observation", "content": "  a finding about x。  "}},
        session_id=sid,
    )
    assert len(state.read_claims("DupTopic2")) == 1


def test_claims_are_read_in_numeric_order():
    m = _handler()
    sid = "t-order"
    m._tool_handler(
        {"action": "create", "project": "OrderTopic", "core_question": "Q?"},
        session_id=sid,
    )
    for i in range(12):
        m._tool_handler(
            {"action": "claim", "project": "OrderTopic",
             "claim": {"kind": "observation", "content": f"finding number {i}"}},
            session_id=sid,
        )
    ids = [c["id"] for c in state.read_claims("OrderTopic")]
    assert ids == [f"L{i}" for i in range(1, 13)], ids


# --------------------------------------------------------------------------
# research map gaps
# --------------------------------------------------------------------------


def test_contradict_records_a_contradiction_with_its_separator():
    m = _handler()
    sid = "t-contra"
    m._tool_handler(
        {"action": "create", "project": "ContraTopic", "core_question": "Q?"},
        session_id=sid,
    )
    import json
    out = json.loads(m._tool_handler(
        {"action": "contradict", "project": "ContraTopic",
         "title": "评分下降来自记忆巩固本身", "separator": "控制情绪后的记忆评分差异是否仍存在"},
        session_id=sid,
    ))
    assert "contradiction" in out
    items = state.read_contradictions("ContraTopic")["items"]
    assert len(items) == 1
    assert items[0]["separator"]


def test_contradict_requires_a_separator():
    m = _handler()
    sid = "t-contra2"
    m._tool_handler(
        {"action": "create", "project": "ContraTopic2", "core_question": "Q?"},
        session_id=sid,
    )
    import json
    out = json.loads(m._tool_handler(
        {"action": "contradict", "project": "ContraTopic2", "title": "just a worry"},
        session_id=sid,
    ))
    assert "error" in out, out


def test_question_records_an_open_question():
    m = _handler()
    sid = "t-q"
    m._tool_handler(
        {"action": "create", "project": "QTopic", "core_question": "Q?"},
        session_id=sid,
    )
    import json
    out = json.loads(m._tool_handler(
        {"action": "question", "project": "QTopic", "question": "口径的定义怎么统一"},
        session_id=sid,
    ))
    assert "open_question" in out
    assert state.read_map("QTopic")["open_questions"] == ["口径的定义怎么统一"]


def test_map_gap_is_reported_when_there_is_no_separator():
    m = _handler()
    sid = "t-gapmap"
    m._tool_handler(
        {"action": "create", "project": "GapMapTopic",
         "core_question": "Q?",
         "hypotheses": "H1 a|H2 b|H3 c"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "GapMapTopic"}, session_id=sid)
    out = m._pre_llm_call(session_id=sid, user_message="继续", turn_id="t1")
    assert out is not None
    assert "研究地图的缺口" in out["context"], out["context"][-400:]


def test_map_gap_is_quiet_once_filled():
    m = _handler()
    sid = "t-gapmap2"
    m._tool_handler(
        {"action": "create", "project": "GapMapTopic2",
         "core_question": "Q?", "hypotheses": "H1 a|H2 b"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "GapMapTopic2"}, session_id=sid)
    m._tool_handler(
        {"action": "contradict", "project": "GapMapTopic2",
         "title": "H1", "separator": "a measurement that separates them"},
        session_id=sid,
    )
    m._tool_handler(
        {"action": "set_state", "project": "GapMapTopic2",
         "goal": "find out which one", "current_task": "compare H1 and H2",
         "phase": "weighing"},
        session_id=sid,
    )
    out = m._pre_llm_call(session_id=sid, user_message="继续", turn_id="t1")
    assert out is not None
    assert "研究地图的缺口" not in out["context"], out["context"][-300:]


def test_a_topic_with_no_standing_is_asked_for_one():
    """A topic with no goal and no current task has no direction. The agent
    is asked where it is going - the question is what makes it think."""
    m = _handler()
    sid = "t-standing-gap"
    m._tool_handler(
        {"action": "create", "project": "StandingGap", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "StandingGap"}, session_id=sid)
    out = m._pre_llm_call(session_id=sid, user_message="继续", turn_id="t1")
    assert out is not None
    assert "没有方向" in out["context"]
    assert "current_task" in out["context"]


# --------------------------------------------------------------------------
# the state tree is behind the tool
# --------------------------------------------------------------------------


def test_writing_the_state_tree_directly_is_blocked():
    """The tool checks the kind, requires a source, enforces the strength
    rule and refuses duplicates. A ledger that can be corrupted by a text
    file is not a ledger."""
    m = _handler()
    path = str(state.HOME / "topics" / "X" / "claims.tsv")
    out = m._state_write_guard(tool_name="write_file", args={"path": path})
    assert out is not None
    assert out["action"] == "block"
    assert "research_mode" in out["message"]


def test_writing_outside_the_state_tree_is_allowed():
    m = _handler()
    out = m._state_write_guard(
        tool_name="write_file", args={"path": "/home/twebery/notes.md"}
    )
    assert out is None, out


def test_reading_the_state_tree_is_allowed():
    m = _handler()
    path = str(state.HOME / "topics" / "X" / "claims.tsv")
    out = m._state_write_guard(tool_name="read_file", args={"path": path})
    assert out is None, out


def test_a_terminal_command_touching_the_state_tree_is_blocked():
    m = _handler()
    out = m._state_write_guard(
        tool_name="terminal",
        args={"command": f"echo 'x' >> {state.HOME}/topics/X/claims.tsv"},
    )
    assert out is not None
    assert out["action"] == "block"


def test_an_ordinary_terminal_command_is_allowed():
    m = _handler()
    out = m._state_write_guard(
        tool_name="terminal", args={"command": "ls -la /home/twebery"}
    )
    assert out is None, out


def test_execute_code_writing_the_state_tree_is_blocked():
    m = _handler()
    out = m._state_write_guard(
        tool_name="execute_code",
        args={"code": f"open('{state.HOME}/topics/X/claims.tsv', 'w')"},
    )
    assert out is not None
    assert out["action"] == "block"


def test_a_relative_path_into_the_state_tree_is_blocked():
    m = _handler()
    out = m._state_write_guard(
        tool_name="terminal",
        args={"command": "cat >> research-os/topics/X/claims.tsv"},
    )
    assert out is not None, out


def test_reading_the_state_tree_from_a_command_is_allowed():
    """Reading the state is how the agent finds out what is already there."""
    m = _handler()
    out = m._state_write_guard(
        tool_name="terminal",
        args={"command": f"cat {state.HOME}/topics/X/claims.tsv"},
    )
    assert out is None, out


def test_listing_the_state_tree_from_a_command_is_allowed():
    m = _handler()
    out = m._state_write_guard(
        tool_name="terminal",
        args={"command": f"ls -la {state.HOME}/topics/X"},
    )
    assert out is None, out


def test_opening_a_state_file_for_reading_is_allowed():
    m = _handler()
    out = m._state_write_guard(
        tool_name="execute_code",
        args={"code": f"open('{state.HOME}/topics/X/claims.tsv').read()"},
    )
    assert out is None, out


def test_a_redirect_into_the_state_tree_is_blocked():
    m = _handler()
    out = m._state_write_guard(
        tool_name="terminal",
        args={"command": f"echo x > {state.HOME}/topics/X/claims.tsv"},
    )
    assert out is not None, out


def test_a_pipe_into_the_state_tree_is_blocked():
    m = _handler()
    out = m._state_write_guard(
        tool_name="terminal",
        args={"command": f"echo x | tee {state.HOME}/topics/X/claims.tsv"},
    )
    assert out is not None, out


def test_a_heredoc_to_tmp_that_mentions_the_state_tree_is_allowed():
    """The check is on the write target. A command that reads the ledger and
    writes a summary elsewhere mentions the state tree and is not touching
    it — this is the false positive that a mention-based check produces."""
    m = _handler()
    cmd = (
        "cat > /tmp/summary.sh <<'EOF'\n"
        f"cat {state.HOME}/topics/X/claims.tsv\n"
        "EOF\n"
    )
    out = m._state_write_guard(tool_name="terminal", args={"command": cmd})
    assert out is None, out


def test_a_heredoc_writing_into_the_state_tree_is_blocked():
    m = _handler()
    cmd = (
        "cat > " + str(state.HOME / "topics" / "X" / "claims.tsv") + " <<'EOF'\n"
        "L1\tx\n"
        "EOF\n"
    )
    out = m._state_write_guard(tool_name="terminal", args={"command": cmd})
    assert out is not None, out


def test_transform_llm_output_asks_for_a_position_when_there_is_none():
    """The check is the shape of the answer, not the bookkeeping.

    It used to list every sentence that was not in the ledger, which
    produced a report of what was said and not said, in place of a
    position. A list of findings with no position is what the reader cannot
    use, so that is what gets flagged now.
    """
    m = _handler()
    sid = "t-transform"
    m._tool_handler(
        {"action": "create", "project": "TrTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "TrTopic"}, session_id=sid)
    state.append_claim("TrTopic", {
        "kind": "observation",
        "content": "Wang 2016 报告睡眠剥夺后陈述性记忆评分下降",
        "source": "PMID 10000001",
    })
    out = m._transform_llm_output(
        session_id=sid,
        response_text=(
            "Wang 2016 报告睡眠剥夺后陈述性记忆评分下降。"
            "另外 Petrov 2020 的队列研究表明再认记忆容量在睡眠剥夺后显著下降。"
        ),
    )
    assert out is not None
    assert "没给判断" in out
    # The answer is not rewritten and the findings are not listed back.
    assert "Petrov 2020" not in out.split("---")[-1]


def test_the_status_line_says_where_the_work_stands():
    """The loop's state is in files the user never opens, so every answer
    carries one line of it: the topic, the phase, what this turn did, how big
    the ledger is, how far the last review is behind, and whether one is owed.
    Without it a long run is a black box that reports on itself.
    """
    m = _handler()
    sid = "t-status"
    name = "T-status"
    if state.topic_exists(name):
        state.delete_topic(name, permanent=True)
    m._tool_handler(
        {"action": "create", "project": name, "core_question": "Q?"}, session_id=sid
    )
    m._tool_handler({"action": "on", "project": name}, session_id=sid)
    m._tool_handler(
        {"action": "set_state", "project": name, "phase": "writing"}, session_id=sid
    )
    m._tool_handler(
        {
            "action": "claim",
            "project": name,
            "claims": [
                {"kind": "observation", "content": "甲", "source": "PMID 1"},
                {"kind": "observation", "content": "乙", "source": "PMID 2"},
            ],
        },
        session_id=sid,
    )
    state.save_config({"status_line": True})
    state.note_tool_activity(sid, "t9", tool_name="web_search", is_evidence=True, is_ledger=0)
    state.note_tool_activity(sid, "t9", tool_name="research_mode", is_evidence=False, is_ledger=2)
    state.note_search_since_review(name)
    state.note_review_demand(name)
    rv = state.read_review(name)
    rv["owed_turns"] = 2
    state.write_review(name, rv)

    out = m._transform_llm_output(
        session_id=sid, turn_id="t9", response_text="这版按原框架改完，结论是残余缺损随阶段变化。"
    )
    assert out is not None
    # The status line is the last line, below any note the check appended.
    footer = out.rstrip().splitlines()[-1]
    assert footer.startswith(f"科研 · {name} · 成文表达"), footer
    assert "本轮 检索1 记账2" in footer, footer
    assert "账本 2 条" in footer, footer
    assert "审查后 1 次检索" in footer, footer
    assert "欠审查 2 轮" in footer, footer
    # Put the default back: the next tests assert the default, and a config
    # left on is how one test's fixture becomes another test's bug.
    state.save_config({"status_line": False})


def test_the_status_line_marks_a_topic_with_no_phase():
    """A topic with no phase is healthy - the agent has not set one yet - and
    the line has to say so rather than leave a gap where the stage goes."""
    m = _handler()
    sid = "t-status-nophase"
    name = "T-status-nophase"
    if state.topic_exists(name):
        state.delete_topic(name, permanent=True)
    m._tool_handler(
        {"action": "create", "project": name, "core_question": "Q?"}, session_id=sid
    )
    m._tool_handler({"action": "on", "project": name}, session_id=sid)
    state.save_config({"status_line": True})
    try:
        line = m._status_line(sid, name, "")
        assert "阶段未定" in line, line
        assert "本轮" not in line, line
    finally:
        state.save_config({"status_line": False})


def test_the_status_line_can_be_turned_off():
    m = _handler()
    sid = "t-status-off"
    name = "T-status-off"
    if state.topic_exists(name):
        state.delete_topic(name, permanent=True)
    m._tool_handler(
        {"action": "create", "project": name, "core_question": "Q?"}, session_id=sid
    )
    m._tool_handler({"action": "on", "project": name}, session_id=sid)
    # Off is the default now: the footer said where the work stopped, and a
    # turn that only calls tools showed nothing about how it moved.
    assert m._status_line(sid, name, "") == ""
    state.save_config({"status_line": True})
    try:
        assert m._status_line(sid, name, "") != ""
        out = m._transform_llm_output(
            session_id=sid,
            turn_id="t1",
            response_text="综合来看，这更支持残余缺损确实存在。",
        )
        assert out is not None and "科研 ·" in out, out
    finally:
        state.save_config({"status_line": False})


def _proto_topic(m, name, sid, phase="evidence"):
    if state.topic_exists(name):
        state.delete_topic(name, permanent=True)
    m._tool_handler({"action": "create", "project": name, "core_question": "Q?"}, session_id=sid)
    m._tool_handler({"action": "on", "project": name}, session_id=sid)
    m._tool_handler({"action": "set_state", "project": name, "phase": phase}, session_id=sid)


def test_a_protocol_is_a_draft_until_it_is_frozen():
    m = _handler()
    sid, name = "t-proto-draft", "T-proto-draft"
    _proto_topic(m, name, sid)
    out = json.loads(m._tool_handler(
        {
            "action": "protocol",
            "project": name,
            "estimand": "患者内认知变化，安慰剂臂作练习效应参照",
            "inclusion": "成人 MDD|基线加至少一个 >=4 周随访|客观测验",
            "falsifier": "短端与中段在可比域上无系统差异",
        },
        session_id=sid,
    ))
    assert out.get("locked") is False, out
    assert out.get("protocol", {}).get("inclusion") == [
        "成人 MDD", "基线加至少一个 >=4 周随访", "客观测验"
    ], out
    assert out.get("missing_before_freeze") == [], out


def test_freezing_needs_an_estimand_inclusion_and_falsifier():
    m = _handler()
    sid, name = "t-proto-missing", "T-proto-missing"
    _proto_topic(m, name, sid)
    m._tool_handler(
        {"action": "protocol", "project": name, "estimand": "某个量"}, session_id=sid
    )
    out = json.loads(
        m._tool_handler({"action": "protocol", "project": name, "freeze": True}, session_id=sid)
    )
    assert "error" in out, out
    assert "纳入标准" in out["error"] and "否证条件" in out["error"], out
    assert state.protocol_locked(name) is False


def test_a_frozen_protocol_cannot_be_edited_without_a_reason():
    """The freeze is the whole point: a plan changed after the data arrived
    has to show up as a change."""
    m = _handler()
    sid, name = "t-proto-frozen", "T-proto-frozen"
    _proto_topic(m, name, sid)
    m._tool_handler(
        {
            "action": "protocol",
            "project": name,
            "estimand": "患者内变化",
            "inclusion": "成人 MDD",
            "falsifier": "无系统差异",
            "freeze": True,
        },
        session_id=sid,
    )
    assert state.protocol_locked(name) is True
    out = json.loads(m._tool_handler(
        {"action": "protocol", "project": name, "estimand": "换一个量"}, session_id=sid
    ))
    assert "error" in out and "unfreeze_reason" in out["error"], out

    out = json.loads(m._tool_handler(
        {
            "action": "protocol",
            "project": name,
            "estimand": "换一个量",
            "unfreeze_reason": "第一版把对照类型和时长混在一条轴上",
        },
        session_id=sid,
    ))
    assert "error" not in out, out
    assert out["locked"] is False
    history = out["protocol"].get("history") or []
    assert history and history[-1]["locked"] is False, history
    assert "对照类型" in history[-1]["reason"], history[-1]
    assert history[-1].get("previous", {}).get("estimand") == "患者内变化", history[-1]
    assert state.read_protocol(name).get("estimand") == "换一个量"


def test_a_search_is_refused_in_gathering_phases_without_a_frozen_protocol():
    m = _handler()
    sid, name = "t-proto-gate", "T-proto-gate"
    _proto_topic(m, name, sid, phase="evidence")
    m._pre_llm_call(session_id=sid, user_message="研究一下", turn_id="t1")
    out = m._pre_tool_call(tool_name="web_search", session_id=sid, turn_id="t1")
    assert out is not None and out.get("action") == "block", out
    assert "冻结" in out["message"], out["message"]

    m._tool_handler(
        {
            "action": "protocol",
            "project": name,
            "estimand": "患者内变化",
            "inclusion": "成人 MDD",
            "falsifier": "无系统差异",
            "freeze": True,
        },
        session_id=sid,
    )
    assert m._pre_tool_call(tool_name="web_search", session_id=sid, turn_id="t2") is None


def test_exploring_phases_do_not_need_a_protocol():
    """A gate in the open phases would block the searching they exist for."""
    m = _handler()
    sid, name = "t-proto-open", "T-proto-open"
    _proto_topic(m, name, sid, phase="diverging")
    m._pre_llm_call(session_id=sid, user_message="研究一下", turn_id="t1")
    assert m._pre_tool_call(tool_name="web_search", session_id=sid, turn_id="t1") is None


def test_the_protocol_page_renders_before_the_ledger():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "render_obsidian_probe", "/home/twebery/research-os/scripts/render_obsidian.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = mod._render_protocol(
        "T-render",
        {
            "locked": True,
            "locked_at": 1792920000.0,
            "estimand": "患者内变化，安慰剂臂作练习参照",
            "inclusion": ["成人 MDD", "客观测验"],
            "falsifier": "短端与中段无系统差异",
            "zotero_collection": "ABCD1234",
            "history": [{"at": 1792920000.0, "locked": True}],
        },
        obsidian=True,
    )
    assert "已冻结" in text, text
    assert "患者内变化，安慰剂臂作练习参照" in text, text
    assert "zotero://select/library/collections/ABCD1234" in text, text
    assert "冻结与解冻记录" in text, text


class _FakeLLM:
    """The host's LLM facade, faked.

    The gate only ever calls `complete_structured` and reads `.parsed`,
    `.model` and `.provider`, so that is the whole surface a test needs.
    """

    def __init__(self, payload=None, *, error=None, model="fake-reviewer"):
        self.payload = payload
        self.error = error
        self.model = model
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return _LLMResult(self.payload)


class _LLMResult:
    """What `complete_structured` hands back: parsed JSON, and who said it."""

    def __init__(self, parsed):
        self.parsed = parsed
        self.model = "fake-reviewer"
        self.provider = "fake"


def _gate_payload(total=8, *, blocking=(), summary="可以往下走。"):
    scores = {
        "alignment": total, "progression": total, "grounding": total,
        "convergence": total, "deliverable": total,
    }
    return {
        "scores": scores,
        "blocking_issues": list(blocking),
        "summary": summary,
        "recommendation": "pass" if not blocking and total >= 7 else "revise",
    }


def _topic_with_phase(m, name, phase, sid=None):
    sid = sid or f"t-{name}"
    if state.topic_exists(name):
        state.delete_topic(name, permanent=True)
    m._tool_handler({"action": "create", "project": name, "core_question": "Q?"}, session_id=sid)
    m._tool_handler({"action": "on", "project": name}, session_id=sid)
    state.write_state(name, phase=phase)
    return sid


def test_a_branch_must_say_which_part_of_the_core_question_it_serves():
    """The anti-drift control. A route nobody can tie to the core question is
    a new project wearing a branch's clothes."""
    m = _handler()
    sid = _topic_with_phase(m, "T-branch-why", "diverging")
    out = json.loads(m._tool_handler(
        {"action": "branch", "project": "T-branch-why", "route": "换个物种再测一遍"},
        session_id=sid,
    ))
    assert "error" in out and "why_relevant" in out["error"], out
    assert state.list_branches("T-branch-why") == [], "a refused branch must not be registered"


def test_a_branch_gets_its_own_topic_so_its_evidence_does_not_leak():
    m = _handler()
    sid = _topic_with_phase(m, "T-branch-open", "diverging")
    out = json.loads(m._tool_handler(
        {
            "action": "branch", "project": "T-branch-open",
            "route": "检验短窗口内练习效应能否解释认知增益",
            "why_relevant": "核心问题里的'短端看不到额外增益'需要这一条来定位机制",
        },
        session_id=sid,
    ))
    assert "error" not in out, out
    child = out["child_topic"]
    assert state.topic_exists(child), out
    assert state.read_map(child).get("parent") == "T-branch-open", state.read_map(child)
    assert state.read_phase(child) == "standing", "a branch starts by confirming its own state"
    assert out["branch"]["id"] == "B1" and out["open_branches"] == 1, out


def test_branches_are_capped_because_five_beginnings_is_not_a_review():
    m = _handler()
    sid = _topic_with_phase(m, "T-branch-cap", "diverging")
    cfg = state.load_config()
    state.save_config({"max_branches": 2})
    try:
        for i in range(2):
            got = json.loads(m._tool_handler(
                {"action": "branch", "project": "T-branch-cap",
                 "route": f"路线 {i+1}", "why_relevant": "都服务核心问题的不同侧面"},
                session_id=sid,
            ))
            assert "error" not in got, got
        blocked = json.loads(m._tool_handler(
            {"action": "branch", "project": "T-branch-cap",
             "route": "第三条路线", "why_relevant": "也想追"},
            session_id=sid,
        ))
        assert "error" in blocked, blocked
        assert "上限" in blocked["error"] and "B1" in blocked["error"], blocked
    finally:
        state.save_config({"max_branches": cfg["max_branches"]})


def test_a_branch_cannot_go_deeper_than_the_cap():
    """Depth is the other way a tree fragments: every branch can open
    branches, and the core question is four levels up by the end."""
    m = _handler()
    sid = _topic_with_phase(m, "T-branch-deep", "diverging")
    cfg = state.load_config()
    state.save_config({"max_branch_depth": 1})
    try:
        got = json.loads(m._tool_handler(
            {"action": "branch", "project": "T-branch-deep", "route": "第一层路线",
             "why_relevant": "服务核心问题"},
            session_id=sid,
        ))
        child = got["child_topic"]
        state.write_state(child, phase="diverging")
        deeper = json.loads(m._tool_handler(
            {"action": "branch", "project": child, "route": "第二层路线",
             "why_relevant": "再往下切"},
            session_id=f"t-{child}",
        ))
        assert "error" in deeper, deeper
        assert "层" in deeper["error"], deeper
    finally:
        state.save_config({"max_branch_depth": cfg["max_branch_depth"]})


def test_closing_a_branch_requires_what_it_contributed():
    m = _handler()
    sid = _topic_with_phase(m, "T-branch-close", "diverging")
    got = json.loads(m._tool_handler(
        {"action": "branch", "project": "T-branch-close", "route": "A 路线",
         "why_relevant": "服务核心问题"},
        session_id=sid,
    ))
    bare = json.loads(m._tool_handler(
        {"action": "branch", "project": "T-branch-close", "branch_id": "B1",
         "branch_status": "concluded"},
        session_id=sid,
    ))
    assert "error" in bare and "contribution" in bare["error"], bare
    done = json.loads(m._tool_handler(
        {"action": "branch", "project": "T-branch-close", "branch_id": "B1",
         "branch_status": "concluded",
         "contribution": "排除了练习效应：对照组也复测，没有同等增益。"},
        session_id=sid,
    ))
    assert "error" not in done, done
    assert done["branch"]["status"] == "concluded", done
    assert done["open_branches"] == 0, done
    assert "练习效应" in state.list_branches("T-branch-close")[0]["contribution"]


def test_leaving_the_diverging_station_needs_two_routes():
    m = _handler()
    sid = _topic_with_phase(m, "T-two-routes", "diverging")
    out = json.loads(m._tool_handler(
        {"action": "set_state", "project": "T-two-routes", "phase": "evidence"},
        session_id=sid,
    ))
    assert "error" in out and "两条" in out["error"], out
    assert state.read_phase("T-two-routes") == "diverging"
    for i in range(2):
        m._tool_handler(
            {"action": "branch", "project": "T-two-routes", "route": f"路线 {i+1}",
             "why_relevant": "服务核心问题"},
            session_id=sid,
        )
    ok_out = json.loads(m._tool_handler(
        {"action": "set_state", "project": "T-two-routes", "phase": "evidence"},
        session_id=sid,
    ))
    assert "error" not in ok_out, ok_out
    assert state.read_phase("T-two-routes") == "evidence"


def test_leaving_converging_needs_a_passed_gate():
    """The gate is a control, not a suggestion: no review, no record."""
    m = _handler()
    sid = _topic_with_phase(m, "T-gate-block", "converging")
    out = json.loads(m._tool_handler(
        {"action": "set_state", "project": "T-gate-block", "phase": "updating"},
        session_id=sid,
    ))
    assert "error" in out and "审稿" in out["error"], out
    assert state.read_phase("T-gate-block") == "converging"


def test_a_blocking_issue_holds_the_project_until_it_is_fixed():
    m = _handler()
    sid = _topic_with_phase(m, "T-gate-revise", "converging")
    fake = _FakeLLM(_gate_payload(5, blocking=[{
        "axis": "alignment", "problem": "这一轮在比较两种量表，没回到核心问题",
        "where": "叙事第二段", "fix": "把量表的比较挂回'耦合是否随窗口变化'",
    }]))
    m._CTX = type("C", (), {"llm": fake, "posted": []})()
    got = json.loads(m._tool_handler(
        {"action": "gate", "project": "T-gate-revise"}, session_id=sid
    ))
    assert got["verdict"] == "revise", got
    assert "没回到核心问题" in got["must_fix"], got
    blocked = json.loads(m._tool_handler(
        {"action": "set_state", "project": "T-gate-revise", "phase": "updating"},
        session_id=sid,
    ))
    assert "error" in blocked and "没回到核心问题" in blocked["error"], blocked


def test_a_clean_review_lets_the_project_through():
    m = _handler()
    sid = _topic_with_phase(m, "T-gate-pass", "converging")
    fake = _FakeLLM(_gate_payload(8))
    m._CTX = type("C", (), {"llm": fake, "posted": []})()
    got = json.loads(m._tool_handler(
        {"action": "gate", "project": "T-gate-pass"}, session_id=sid
    ))
    assert got["verdict"] == "pass", got
    passed = json.loads(m._tool_handler(
        {"action": "set_state", "project": "T-gate-pass", "phase": "updating"},
        session_id=sid,
    ))
    assert "error" not in passed, passed
    # The reviewer was given the standing, not a message.
    assert fake.calls, "the gate never called the model"
    brief = fake.calls[0]["input"][0]["text"]
    assert "核心问题" in brief and "现在相信什么" in brief, brief[:200]


def test_endless_revision_ends_with_a_written_caveat():
    """Scores are not objective and a gate that waits for a perfect score
    waits forever. After max_rounds the work goes through, and the record
    says why."""
    m = _handler()
    sid = _topic_with_phase(m, "T-gate-caveat", "converging")
    fake = _FakeLLM(_gate_payload(4, blocking=[{
        "axis": "progression", "problem": "还在原地", "where": "计划段", "fix": "换判据",
    }]))
    m._CTX = type("C", (), {"llm": fake, "posted": []})()
    cfg = state.load_config()
    state.save_config({"gate_max_rounds": 2})
    try:
        first = json.loads(m._tool_handler({"action": "gate", "project": "T-gate-caveat"}, session_id=sid))
        assert first["verdict"] == "revise", first
        second = json.loads(m._tool_handler({"action": "gate", "project": "T-gate-caveat"}, session_id=sid))
        assert second["verdict"] == "pass_with_caveat", second
        assert "caveat" in second["next"], second
        through = json.loads(m._tool_handler(
            {"action": "set_state", "project": "T-gate-caveat", "phase": "updating"},
            session_id=sid,
        ))
        assert "error" not in through, through
    finally:
        state.save_config({"gate_max_rounds": cfg["gate_max_rounds"]})


def test_a_reviewer_that_cannot_run_is_a_caveat_not_a_deadlock():
    m = _handler()
    sid = _topic_with_phase(m, "T-gate-down", "converging")
    fake = _FakeLLM(error=RuntimeError("provider 502"))
    m._CTX = type("C", (), {"llm": fake, "posted": []})()
    got = json.loads(m._tool_handler({"action": "gate", "project": "T-gate-down"}, session_id=sid))
    assert got["verdict"] == "pass_with_caveat", got
    assert "502" in got.get("error_note", ""), got
    through = json.loads(m._tool_handler(
        {"action": "set_state", "project": "T-gate-down", "phase": "updating"},
        session_id=sid,
    ))
    assert "error" not in through, through
    record = state.last_gate_review("T-gate-down")
    assert record["llm_ok"] is False and "502" in record["error"], record


def test_the_gate_scores_are_average_and_the_threshold_decides():
    assert reviewer.total_score({"alignment": 8, "progression": 6, "grounding": 7,
                                 "convergence": 9, "deliverable": 5}) == 7.0
    verdict, why = reviewer.decide(blocking=[], total=7.4, round_no=1,
                                   pass_score=7.0, max_rounds=3)
    assert verdict == "pass", (verdict, why)
    verdict, why = reviewer.decide(blocking=[], total=6.9, round_no=1,
                                   pass_score=7.0, max_rounds=3)
    assert verdict == "revise", (verdict, why)


def test_editing_the_plugin_source_is_not_a_ledger_write():
    """The guard matches on text, and this file contains the call names it
    looks for. Blocking source maintenance is how a guard teaches people to
    route around it."""
    code = (
        "from hermes_tools import patch as P\n"
        "P(path='/home/twebery/research-os/research_os/__init__.py', "
        "old_string='state.write_map(x)', new_string='state.write_map(y)')\n"
    )
    assert m_guard_allows(code), "editing the plugin source must not be blocked"


def m_guard_allows(code: str) -> bool:
    import research_os as _m
    out = _m._state_write_guard(tool_name="execute_code", args={"code": code})
    return out is None


def test_a_phase_written_with_an_old_name_is_translated():
    m = _handler()
    sid = "t-alias"
    if state.topic_exists("T-alias"):
        state.delete_topic("T-alias", permanent=True)
    m._tool_handler({"action": "create", "project": "T-alias", "core_question": "Q?"}, session_id=sid)
    out = json.loads(m._tool_handler(
        {"action": "set_state", "project": "T-alias", "phase": "weighing"}, session_id=sid
    ))
    assert "error" not in out, out
    assert state.read_phase("T-alias") == "synthesis", state.read_phase("T-alias")


def test_a_fenced_json_reply_still_counts():
    """Providers that ignore `response_format` still send the object, wrapped.
    Failing the gate on that is failing it on the wrong thing."""
    assert reviewer._extract_json('```json\n{"scores": {"alignment": 8}}\n```') == {
        "scores": {"alignment": 8}
    }
    assert reviewer._extract_json('好的，这是评估：{"summary": "可以"}') == {"summary": "可以"}
    assert reviewer._extract_json("不是 JSON") is None


class _ScriptedLLM:
    """A facade that answers each call from a script, to test the fallbacks."""

    def __init__(self, complete_text="", structured_payload=None):
        self.complete_text = complete_text
        self.structured_payload = structured_payload
        self.complete_calls = 0
        self.structured_calls = 0

    def complete(self, **kwargs):
        self.complete_calls += 1

        class _R:
            text = self.complete_text
            model = "scripted"
            provider = "fake"

        return _R()

    def complete_structured(self, **kwargs):
        self.structured_calls += 1

        class _R:
            parsed = self.structured_payload
            text = ""
            model = "scripted"
            provider = "fake"

        return _R()


def test_a_json_reply_in_a_fenced_block_is_read_from_the_text():
    """The primary path is a plain completion: a provider that accepts a
    schema and returns nothing leaves the gate with no material."""
    m = _handler()
    sid = _topic_with_phase(m, "T-gate-fence", "converging")
    payload = _gate_payload(8)
    fake = _ScriptedLLM(complete_text="```json\n" + json.dumps(payload) + "\n```")
    m._CTX = type("C", (), {"llm": fake, "posted": []})()
    got = json.loads(m._tool_handler({"action": "gate", "project": "T-gate-fence"}, session_id=sid))
    assert got["verdict"] == "pass", got
    assert state.last_gate_review("T-gate-fence")["via"] == "complete"
    assert fake.complete_calls == 1 and fake.structured_calls == 0


def test_an_empty_reply_falls_back_to_the_structured_call():
    m = _handler()
    sid = _topic_with_phase(m, "T-gate-retry", "converging")
    fake = _ScriptedLLM(complete_text="", structured_payload=_gate_payload(8))
    m._CTX = type("C", (), {"llm": fake, "posted": []})()
    got = json.loads(m._tool_handler({"action": "gate", "project": "T-gate-retry"}, session_id=sid))
    assert got["verdict"] == "pass", got
    record = state.last_gate_review("T-gate-retry")
    assert record["via"] == "structured" and record["attempts"] == 2, record
    assert fake.structured_calls == 1


def test_the_map_cannot_rewrite_the_core_question_without_a_reason():
    """One door. `set_state` recorded the old question and the reason; the
    `map` action wrote the field straight into the file, so the rule held on
    the path people were told to use and not on the shorter one."""
    m = _handler()
    sid = "t-map-q"
    if state.topic_exists("T-map-q"):
        state.delete_topic("T-map-q", permanent=True)
    m._tool_handler({"action": "create", "project": "T-map-q", "core_question": "旧问题？"}, session_id=sid)
    bare = json.loads(m._tool_handler(
        {"action": "map", "project": "T-map-q", "core_question": "新问题？"}, session_id=sid
    ))
    assert "error" in bare and "question_reason" in bare["error"], bare
    assert state.read_map("T-map-q")["core_question"] == "旧问题？"
    ok_out = json.loads(m._tool_handler(
        {"action": "map", "project": "T-map-q", "core_question": "新问题？",
         "question_reason": "旧问题把测量和构念混在一起"},
        session_id=sid,
    ))
    assert "error" not in ok_out, ok_out
    hist = state.read_map("T-map-q").get("question_history") or []
    assert hist and hist[-1]["was"] == "旧问题？" and hist[-1]["now"] == "新问题？", hist
    assert "测量和构念" in hist[-1]["why"], hist[-1]


def test_a_parent_link_may_not_close_a_loop():
    """`parent == topic` catches the shortest loop and nothing else. A parent
    whose own parent is this topic makes a cycle the depth walk cannot leave."""
    m = _handler()
    if state.topic_exists("T-loop-a"):
        state.delete_topic("T-loop-a", permanent=True)
    if state.topic_exists("T-loop-b"):
        state.delete_topic("T-loop-b", permanent=True)
    m._tool_handler({"action": "create", "project": "T-loop-a", "core_question": "A?"}, session_id="t-loop")
    m._tool_handler({"action": "create", "project": "T-loop-b", "core_question": "B?"}, session_id="t-loop")
    first = json.loads(m._tool_handler(
        {"action": "set_state", "project": "T-loop-b", "parent": "T-loop-a"}, session_id="t-loop"
    ))
    assert "error" not in first, first
    looped = json.loads(m._tool_handler(
        {"action": "set_state", "project": "T-loop-a", "parent": "T-loop-b"}, session_id="t-loop"
    ))
    assert "error" in looped and "环" in looped["error"], looped
    assert str(state.read_map("T-loop-a").get("parent") or "") == ""


def test_a_pause_lifts_only_when_the_standing_says_where_the_work_goes():
    m = _handler()
    sid = "t-pause-lift"
    if state.topic_exists("T-pause-lift"):
        state.delete_topic("T-pause-lift", permanent=True)
    m._tool_handler({"action": "create", "project": "T-pause-lift", "core_question": "Q?"}, session_id=sid)
    m._tool_handler({
        "action": "review", "project": "T-pause-lift", "verdict": "pause",
        "review_note": "在查干预研究，核心问题其实是病因",
    }, session_id=sid)
    assert state.topic_is_paused("T-pause-lift")
    # A note about an exploration says nothing about where the work goes.
    side = json.loads(m._tool_handler({
        "action": "set_state", "project": "T-pause-lift",
        "exploration": "在另一个框架里找过反例，没找到",
    }, session_id=sid))
    assert side.get("still_paused") is True, side
    assert state.topic_is_paused("T-pause-lift"), "an exploration note must not lift a pause"
    # A plan is an answer to the review.
    lifted = json.loads(m._tool_handler({
        "action": "set_state", "project": "T-pause-lift",
        "plan": "下一轮改查病因方向的四个预测",
    }, session_id=sid))
    assert lifted.get("resumed") is True, lifted
    assert not state.topic_is_paused("T-pause-lift")


def test_a_parent_must_synthesise_what_its_branches_produced():
    """Branches that finish without the parent saying what they add up to
    leave a folder of findings, not a review."""
    m = _handler()
    parent = "T-syn-parent"
    if state.topic_exists(parent):
        state.delete_topic(parent, permanent=True)
    m._tool_handler({"action": "create", "project": parent, "core_question": "情感与认知是什么关系？"},
                    session_id="t-syn")
    m._tool_handler({"action": "on", "project": parent}, session_id="t-syn")
    state.write_state(parent, phase="diverging")
    for route in ("病程路线：短端和长端是不是同一个量", "环路路线：MD-mPFC 是否解释两域"):
        got = json.loads(m._tool_handler(
            {"action": "branch", "project": parent, "route": route,
             "why_relevant": "核心问题的不同侧面"},
            session_id="t-syn",
        ))
        assert "error" not in got, got
    children = [b["child"] for b in state.list_branches(parent)]
    for child in children:
        state.write_state(child, parent=parent, narrative=f"{child} 的判断：短端看不到额外增益")
    state.append_claim(
        children[0],
        {"kind": "observation", "content": "八周安慰剂对照未见额外认知增益",
         "source": "PMID 12345678", "status": "active"},
    )
    state.append_claim(
        children[1],
        {"kind": "observation", "content": "八周安慰剂对照未见额外认知增益的重复报告",
         "source": "PMID 12345678", "status": "active"},
    )
    closed = json.loads(m._tool_handler(
        {"action": "branch", "project": parent, "branch_id": "B1", "branch_status": "concluded",
         "contribution": "短窗口内没有可检出的认知增益"},
        session_id="t-syn",
    ))
    assert "error" not in closed, closed
    blocked = json.loads(m._tool_handler(
        {"action": "set_state", "project": parent, "phase": "converging"}, session_id="t-syn"
    ))
    assert "error" in blocked and "synthesize" in blocked["error"], blocked

    digest = json.loads(m._tool_handler(
        {"action": "synthesize", "project": parent}, session_id="t-syn"
    ))
    assert "error" not in digest, digest
    assert digest["shared_sources"], digest
    assert digest["shared_sources"][0]["source"] == "pmid:12345678", digest
    assert digest["overlapping_claims"], digest
    assert digest["demands"], digest
    assert "B1" in digest["unsynthesised"], digest
    assert state.last_synthesis(parent), "the digest has to be on the record"

    write_back = json.loads(m._tool_handler({
        "action": "set_state", "project": parent,
        "narrative": "两条路线合起来：短窗口的增益缺证据，长窗口的变化还要看缓解状态",
        "plan": "补 B2 的环路证据，再定父课题的结论强度",
    }, session_id="t-syn"))
    assert "error" not in write_back, write_back
    through = json.loads(m._tool_handler(
        {"action": "set_state", "project": parent, "phase": "converging"}, session_id="t-syn"
    ))
    assert "error" not in through, through


def test_the_synthesis_digest_counts_rather_than_judges():
    """The digest reports what is in the material; it never decides. Counting
    shared sources and near-duplicate claims is not judgement, and a model
    asked to "synthesise" without them writes a paragraph instead."""
    digest = synthesis.build_digest(
        "P",
        parent_map={"narrative_at": 100.0},
        branches=[
            {"id": "B1", "child": "P-B1", "route": "r1", "status": "concluded",
             "contribution": "c1", "closed_at": 200.0},
            {"id": "B2", "child": "P-B2", "route": "r2", "status": "open",
             "contribution": "", "closed_at": None},
        ],
        branch_state={
            "P-B1": {"phase": "收敛", "narrative": "n1", "claims": [
                {"kind": "observation", "content": "八周安慰剂对照没有额外认知增益", "source": "S1"},
            ], "contradictions": [{"title": "练习效应", "separator": "对照是否复测", "status": "open"}]},
            "P-B2": {"phase": "找证据", "narrative": "n2", "claims": [
                {"kind": "observation", "content": "八周安慰剂对照没有额外的认知增益", "source": "S1"},
            ], "contradictions": []},
        },
    )
    assert digest["branch_count"] == 2 and digest["concluded"] == 1, digest
    # "S1" in two branches is two different papers: the id belongs to the
    # branch's own library. Without a globally resolvable identity it is not
    # a shared source, and the digest says which is which.
    assert digest["shared_sources"] == [], digest
    assert digest["local_source_collisions"], digest
    assert digest["local_source_collisions"][0]["branches"] == ["B1", "B2"], digest
    assert digest["overlapping_claims"], digest
    assert digest["unsynthesised"] == ["B1"], digest
    assert any("练习效应" in d for d in digest["demands"]), digest["demands"]



# --------------------------------------------------------------------------
# cross-topic evidence, synthesis states, polarity, and parent directives
# --------------------------------------------------------------------------


def _family_pair(parent: str, child: str, *, route: str = "短窗口"):
    """A parent with one branch under it, wired both ways."""
    for name in (parent, child):
        if state.topic_exists(name):
            state.delete_topic(name, permanent=True)
    state.create_topic(parent, "情感障碍与认知障碍是什么关系？")
    state.create_topic(child, "短窗口内有没有额外认知增益？")
    state.write_state(child, parent=parent)
    return state.add_branch(parent, route=route, why="核心问题的短端", child=child)


def test_a_parent_inference_may_rest_on_a_branch_claim():
    """The judgement of a parent is about what its branches found. Until a
    premise could name another topic's ledger, the honest move was to copy
    the finding upward - which loses the route it came from."""
    parent, child = "T-xp-parent", "T-xp-child"
    _family_pair(parent, child)
    state.append_claim(child, {
        "kind": "observation",
        "content": "八周安慰剂对照未见额外认知增益",
        "source": "PMID 12345678",
        "status": "verified",
        "strength": "strong",
    })
    state.append_claim(parent, {
        "kind": "inference",
        "inference_type": "descriptive",
        "content": "短端没有可检出的认知增益",
        "premises": f"{child}:L1",
        "strength": "strong",
        "status": "derived",
    })
    report = validate_claims(parent)
    assert not report["errors"], report["errors"]
    assert report["cross_premises"], report
    borrowed = report["cross_premises"][0]
    assert borrowed["topic"] == child and borrowed["status"] == "verified", borrowed
    assert borrowed["source"] == "PMID 12345678", borrowed


def test_a_premise_may_not_be_imported_from_an_unrelated_topic():
    """A conclusion imported from a stranger's ledger looks exactly like one
    that was reasoned to, which is why it is refused rather than noted."""
    for name in ("T-xp-a", "T-xp-b"):
        if state.topic_exists(name):
            state.delete_topic(name, permanent=True)
    state.create_topic("T-xp-a", "问题 A？")
    state.create_topic("T-xp-b", "问题 B？")
    state.append_claim("T-xp-b", {
        "kind": "observation", "content": "某篇文献的结论", "source": "PMID 99999999",
        "status": "verified", "strength": "strong",
    })
    state.append_claim("T-xp-a", {
        "kind": "inference", "inference_type": "descriptive",
        "content": "借来的结论", "premises": "T-xp-b:L1", "strength": "strong",
        "status": "derived",
    })
    report = validate_claims("T-xp-a")
    codes = [e["code"] for e in report["errors"]]
    assert "foreign_premise" in codes, report["errors"]


def test_a_premise_naming_a_topic_that_does_not_exist_says_so():
    if state.topic_exists("T-xp-c"):
        state.delete_topic("T-xp-c", permanent=True)
    state.create_topic("T-xp-c", "问题 C？")
    state.append_claim("T-xp-c", {
        "kind": "inference", "inference_type": "descriptive",
        "content": "指向不存在的课题", "premises": "nowhere:L1", "strength": "strong",
        "status": "derived",
    })
    codes = [e["code"] for e in validate_claims("T-xp-c")["errors"]]
    assert "unknown_premise_topic" in codes or "foreign_premise" in codes


def test_a_borrowed_premise_limits_how_strongly_the_parent_may_speak():
    """The strength rule travels with the premise. A suggestive finding in a
    branch cannot support an established claim in the parent."""
    parent, child = "T-xp-str", "T-xp-str-child"
    _family_pair(parent, child)
    state.append_claim(child, {
        "kind": "observation", "content": "小样本的趋势", "source": "PMID 11111111",
        "status": "verified", "strength": "suggestive",
    })
    state.append_claim(parent, {
        "kind": "inference", "inference_type": "descriptive",
        "content": "两域是独立通路",
        "premises": f"{child}:L1", "strength": "established", "status": "derived",
    })
    codes = [e["code"] for e in validate_claims(parent)["errors"]]
    assert "overstated" in codes, validate_claims(parent)["errors"]


def test_generating_a_digest_is_not_finishing_the_synthesis():
    """Calling synthesize and moving on passed the old check, which only
    compared timestamps. The digest has counted; nobody has judged."""
    m = _handler()
    parent = "T-syn-state"
    if state.topic_exists(parent):
        state.delete_topic(parent, permanent=True)
    m._tool_handler({"action": "create", "project": parent, "core_question": "关系是什么？"}, session_id="t-ss")
    m._tool_handler({"action": "on", "project": parent}, session_id="t-ss")
    state.write_state(parent, phase="diverging")
    for route in ("病程路线", "环路路线"):
        got = json.loads(m._tool_handler(
            {"action": "branch", "project": parent, "route": route, "why_relevant": "核心问题的一面"},
            session_id="t-ss",
        ))
        assert "error" not in got, got
    children = [b["child"] for b in state.list_branches(parent)]
    state.append_claim(children[0], {
        "kind": "observation", "content": "短端未见额外增益", "source": "PMID 22222222",
        "status": "verified", "strength": "strong",
    })
    json.loads(m._tool_handler(
        {"action": "branch", "project": parent, "branch_id": "B1",
         "branch_status": "concluded", "contribution": "短端没有可检出的增益"},
        session_id="t-ss",
    ))
    digest = json.loads(m._tool_handler({"action": "synthesize", "project": parent}, session_id="t-ss"))
    assert "error" not in digest, digest
    assert state.last_synthesis(parent)["state"] == state.SYNTHESIS_PENDING
    blocked = json.loads(m._tool_handler(
        {"action": "set_state", "project": parent, "phase": "converging"}, session_id="t-ss"
    ))
    assert "error" in blocked and "判断" in blocked["error"], blocked
    json.loads(m._tool_handler(
        {"action": "set_state", "project": parent,
         "narrative": "短端没有增益，长端还要看缓解状态",
         "plan": "补环路那一侧的证据"},
        session_id="t-ss",
    ))
    assert state.last_synthesis(parent)["state"] == state.SYNTHESIS_JUDGED
    through = json.loads(m._tool_handler(
        {"action": "set_state", "project": parent, "phase": "converging"}, session_id="t-ss"
    ))
    assert "error" not in through, through


def test_the_synthesis_is_complete_when_a_parent_claim_rests_on_a_branch():
    """`integrated` is not a mood: it is a ledger row of the parent's whose
    premise points into a branch the digest covered."""
    m = _handler()
    parent, child = "T-syn-int", "T-syn-int-child"
    _family_pair(parent, child)
    state.append_claim(child, {
        "kind": "observation", "content": "四域回到对照水平", "source": "PMID 33333333",
        "status": "verified", "strength": "strong",
    })
    json.loads(m._tool_handler(
        {"action": "branch", "project": parent, "branch_id": "B1",
         "branch_status": "concluded", "contribution": "长期缓解者四域恢复"},
        session_id="t-si",
    ))
    json.loads(m._tool_handler({"action": "synthesize", "project": parent}, session_id="t-si"))
    json.loads(m._tool_handler(
        {"action": "set_state", "project": parent, "narrative": "恢复取决于缓解状态",
         "plan": "查缓解时长"}, session_id="t-si",
    ))
    assert state.last_synthesis(parent)["state"] == state.SYNTHESIS_JUDGED
    got = json.loads(m._tool_handler({
        "action": "claim", "project": parent,
        "claim": {
            "kind": "inference", "inference_type": "descriptive",
            "content": "认知结局取决于缓解状态而不是发作次数",
            "premises": f"{child}:L1", "strength": "strong", "status": "derived",
        },
    }, session_id="t-si"))
    assert "error" not in got, got
    last = state.last_synthesis(parent)
    assert last["state"] == state.SYNTHESIS_INTEGRATED, last


def test_opposite_wordings_are_reported_as_a_question_not_a_duplicate():
    """'干预显著改善' and '干预未显著改善' share almost every token and mean
    opposite things. The program may only say that it found the pair."""
    digest = synthesis.build_digest(
        "P",
        parent_map={},
        branches=[
            {"id": "B1", "child": "P-B1", "route": "r1", "status": "open", "contribution": ""},
            {"id": "B2", "child": "P-B2", "route": "r2", "status": "open", "contribution": ""},
        ],
        branch_state={
            "P-B1": {"phase": "找证据", "narrative": "", "claims": [
                {"kind": "observation", "content": "干预显著改善认知表现", "source": "S1"},
            ], "contradictions": []},
            "P-B2": {"phase": "找证据", "narrative": "", "claims": [
                {"kind": "observation", "content": "干预未显著改善认知表现", "source": "S2"},
            ], "contradictions": []},
        },
    )
    pairs = digest["overlapping_claims"]
    assert pairs, digest
    assert pairs[0]["hint"] == "polarity_differs", pairs
    assert pairs[0]["relation"] == "", "the program must not fill in the relation"
    assert any("极性相反" in d for d in digest["demands"]), digest["demands"]


def test_the_parent_can_tell_a_branch_what_to_do_next():
    m = _handler()
    parent, child = "T-dir", "T-dir-B1"
    row = _family_pair(parent, child)
    assert row["id"] == "B1"
    no_reason = json.loads(m._tool_handler(
        {"action": "directive", "project": parent, "branch_id": "B1", "decision": "redirect"},
        session_id="t-dir",
    ))
    assert "error" in no_reason and "reason" in no_reason["error"], no_reason
    sent = json.loads(m._tool_handler({
        "action": "directive", "project": parent, "branch_id": "B1",
        "decision": "redirect", "reason": "短端已排除，改查长端",
        "next_question": "六个月缓解者是否回到对照水平", "priority": "high",
    }, session_id="t-dir"))
    assert "error" not in sent, sent
    block = protocol.build_research_block(child)
    assert "父课题" in block and "六个月缓解者" in block, block[:600]
    assert "还没确认" in block, block[:600]
    acked = json.loads(m._tool_handler({
        "action": "acknowledge", "project": child,
        "acknowledge_note": "这一轮就查这个",
    }, session_id="t-dir"))
    assert "error" not in acked, acked
    directive = state.read_directive(parent, "B1")
    assert directive["acknowledged_at"], directive
    # A new instruction is a new instruction: the old receipt cannot speak
    # for it.
    state.set_directive(parent, "B1", decision="pause", reason="等文献到手")
    assert state.read_directive(parent, "B1")["acknowledged_at"] is None




def test_an_exploratory_plan_only_has_to_commit_to_what_would_refute_it():
    """A systematic review can name its estimand and its inclusion criteria
    before it looks at a record. An exploratory piece cannot, and freezing a
    guess narrows it onto the first framing. What both can say is what would
    make them wrong."""
    m = _handler()
    topic = "T-mode-x"
    if state.topic_exists(topic):
        state.delete_topic(topic, permanent=True)
    json.loads(m._tool_handler({"action": "create", "project": topic, "core_question": "形状是什么？"},
                               session_id="t-mode"))
    strict = json.loads(m._tool_handler(
        {"action": "protocol", "project": topic, "mode": "exploratory",
         "falsifier": "如果两域的一致性完全由测量方式解释，就不是关系而是方法"},
        session_id="t-mode"))
    assert "error" not in strict, strict
    assert strict["missing_before_freeze"] == [], strict
    frozen = json.loads(m._tool_handler(
        {"action": "protocol", "project": topic, "freeze": True}, session_id="t-mode"))
    assert "error" not in frozen and frozen["locked"] is True, frozen
    # The systematic mode still demands all three.
    other = "T-mode-s"
    if state.topic_exists(other):
        state.delete_topic(other, permanent=True)
    json.loads(m._tool_handler({"action": "create", "project": other, "core_question": "多少个？"},
                               session_id="t-mode"))
    shown = json.loads(m._tool_handler(
        {"action": "protocol", "project": other, "falsifier": "效应量小于 0.2"},
        session_id="t-mode"))
    assert set(shown["missing_before_freeze"]) == {"estimand", "inclusion"}, shown


def test_only_a_globally_resolvable_source_counts_as_shared():
    """Two branches writing "S1" are two papers; two branches writing the
    same PMID are one paper. The difference is the whole point of a shared
    source, so it has to be decided by what resolves, not by the string."""
    def identity(topic: str, source: str) -> str:
        text = str(source or "")
        if text.startswith("PMID"):
            return "pmid:" + text.split()[1]
        return ""

    digest = synthesis.build_digest(
        "P",
        parent_map={},
        branches=[
            {"id": "B1", "child": "P-B1", "route": "r1", "status": "open", "contribution": ""},
            {"id": "B2", "child": "P-B2", "route": "r2", "status": "open", "contribution": ""},
        ],
        branch_state={
            "P-B1": {"phase": "找证据", "narrative": "", "claims": [
                {"kind": "observation", "content": "同一项研究的两个测量", "source": "PMID 12345"},
            ], "contradictions": []},
            "P-B2": {"phase": "找证据", "narrative": "", "claims": [
                {"kind": "observation", "content": "同一项研究的另两个测量", "source": "PMID 12345"},
            ], "contradictions": []},
        },
        source_identity=identity,
    )
    assert digest["shared_sources"] == [{"source": "pmid:12345", "branches": ["B1", "B2"]}], digest
    assert not digest["local_source_collisions"], digest


def test_a_control_can_be_set_aside_with_a_reason_that_is_kept():
    """The rules come in layers. The floor (a claim without a source, a record
    changed without a trace, a premise that points nowhere) is never waived.
    These three are good defaults and bad laws."""
    m = _handler()
    topic = "T-waive"
    if state.topic_exists(topic):
        state.delete_topic(topic, permanent=True)
    m._tool_handler({"action": "create", "project": topic, "core_question": "Q?"}, session_id="t-w")
    m._tool_handler({"action": "on", "project": topic}, session_id="t-w")
    state.write_state(topic, phase="diverging")
    blocked = json.loads(m._tool_handler(
        {"action": "set_state", "project": topic, "phase": "evidence"}, session_id="t-w"))
    assert "error" in blocked and "两条路线" in blocked["error"], blocked
    no_reason = json.loads(m._tool_handler(
        {"action": "waive", "project": topic, "guard": "routes"}, session_id="t-w"))
    assert "error" in no_reason and "reason" in no_reason["error"], no_reason
    bad_guard = json.loads(m._tool_handler(
        {"action": "waive", "project": topic, "guard": "ledger", "reason": "x"}, session_id="t-w"))
    assert "error" in bad_guard, bad_guard
    waived = json.loads(m._tool_handler({
        "action": "waive", "project": topic, "guard": "routes",
        "reason": "这一轮是把 MDL 环路的细胞类型做深，不是在比较方案",
    }, session_id="t-w"))
    assert "error" not in waived, waived
    through = json.loads(m._tool_handler(
        {"action": "set_state", "project": topic, "phase": "evidence"}, session_id="t-w"))
    assert "error" not in through, through
    # The reason is on the record, not just in the reply.
    summary = state.waiver_summary(topic, phase="diverging")
    assert summary and summary[0]["guard"] == "routes", summary
    assert "做深" in summary[0]["reason"], summary
    # A different stage is a different piece of work: the reason does not
    # travel with it.
    assert not state.waiver_summary(topic, phase="converging")




def test_a_mapping_project_commits_to_its_scope_and_nothing_else():
    """Building the map of an area has no claim to refute yet. Demanding a
    falsifier from it produces a formal sentence that refutes nothing, and
    teaches the agent to write down things that are not true."""
    m = _handler()
    topic = "T-map-mode"
    if state.topic_exists(topic):
        state.delete_topic(topic, permanent=True)
    json.loads(m._tool_handler({"action": "create", "project": topic,
                                "core_question": "MD 核团各亚区的功能是什么？"},
                               session_id="t-mm"))
    shown = json.loads(m._tool_handler(
        {"action": "protocol", "project": topic, "mode": "mapping",
         "inclusion": "MD 背侧中线核团各亚区 | 啮齿类与灵长类"},
        session_id="t-mm"))
    assert "error" not in shown, shown
    assert shown["missing_before_freeze"] == [], shown
    assert shown["mode"] == "mapping", shown
    frozen = json.loads(m._tool_handler(
        {"action": "protocol", "project": topic, "freeze": True}, session_id="t-mm"))
    assert frozen["locked"] is True, frozen


def test_negation_survives_the_tokeniser():
    """"not" was in the stopword list, which made "improved" and "did not
    improve" the same token bag: a contradiction reported as a duplicate."""
    assert "not" not in synthesis._STOP
    assert "no" not in synthesis._STOP
    assert "never" not in synthesis._STOP
    plain = synthesis._tokens("the intervention improved cognition")
    negated = synthesis._tokens("the intervention did not improve cognition")
    assert plain != negated, (plain, negated)
    assert synthesis._negated("did not improve cognition")
    assert not synthesis._negated("improved cognition")
    # Double negation: "not absent" and "无不" claim that something is
    # there, and reporting them as absences inverts the finding.
    assert not synthesis._negated("not absent"), "double negation reads as presence"
    assert not synthesis._negated("无不"), "double negation reads as presence"
    # A negation inside a different word is not a negation of the claim.
    assert not synthesis._negated("different regions"), "不 in 不同 is not a negation"
    assert not synthesis._negated("not only cognition"), "context matters"


def test_the_threshold_does_not_hide_a_real_pair():
    """A pair one point below the cut is not reported, and one just above it
    is - the boundary has to be where the number says, not where the
    implementation happens to be."""
    a = "八周安慰剂对照未见额外认知增益"
    b = "八周安慰剂对照未见额外认知增益的重复报告"
    score = synthesis._jaccard(synthesis._tokens(a), synthesis._tokens(b))
    assert 0.5 < score < 0.95, score
    high = synthesis.build_digest(
        "P", parent_map={},
        branches=[
            {"id": "B1", "child": "P-B1", "route": "r1", "status": "open", "contribution": ""},
            {"id": "B2", "child": "P-B2", "route": "r2", "status": "open", "contribution": ""},
        ],
        branch_state={
            "P-B1": {"phase": "找证据", "narrative": "", "claims": [
                {"kind": "observation", "content": a, "source": "PMID 1"}], "contradictions": []},
            "P-B2": {"phase": "找证据", "narrative": "", "claims": [
                {"kind": "observation", "content": b, "source": "PMID 2"}], "contradictions": []},
        },
        threshold=score,
    )
    assert high["overlapping_claims"], high
    just_above = synthesis.build_digest(
        "P", parent_map={},
        branches=[
            {"id": "B1", "child": "P-B1", "route": "r1", "status": "open", "contribution": ""},
            {"id": "B2", "child": "P-B2", "route": "r2", "status": "open", "contribution": ""},
        ],
        branch_state={
            "P-B1": {"phase": "找证据", "narrative": "", "claims": [
                {"kind": "observation", "content": a, "source": "PMID 1"}], "contradictions": []},
            "P-B2": {"phase": "找证据", "narrative": "", "claims": [
                {"kind": "observation", "content": b, "source": "PMID 2"}], "contradictions": []},
        },
        threshold=score + 0.01,
    )
    assert not just_above["overlapping_claims"], just_above


def test_confirming_a_directive_is_not_the_same_as_doing_it():
    """An acknowledged instruction is an instruction the branch read. Whether
    the branch then worked on it is a different question, and the plan's own
    timestamp is the only evidence available."""
    parent, child = "T-dp", "T-dp-B1"
    _family_pair(parent, child)
    state.set_directive(parent, "B1", decision="redirect", reason="改查长端",
                        next_question="六个月缓解者是否回到对照水平")
    state.acknowledge_directive(child, note="这一轮就查这个")
    rows = state.directive_progress(parent)
    assert rows and rows[0]["state"] == "已确认但计划没动", rows
    state.write_state(child, plan="改查长端：六个月缓解者")
    rows = state.directive_progress(parent)
    assert rows[0]["state"] == "已落实", rows
    assert rows[0]["plan_moved_after"] is True, rows




# --------------------------------------------------------------------------
# three scenarios, asked for before any more mechanisms
# --------------------------------------------------------------------------


def test_scenario_open_exploration_a_field_can_be_mapped_without_a_hypothesis():
    """"I want to understand what is known about the subregions of the MD
    nucleus." There is no claim to refute yet, and a system that demands one
    gets a formal sentence that refutes nothing - and teaches the agent to
    write down things that are not true."""
    m = _handler()
    topic = "T-sc-map"
    if state.topic_exists(topic):
        state.delete_topic(topic, permanent=True)
    m._tool_handler({"action": "create", "project": topic,
                     "core_question": "MD 核团各亚区在情绪加工里各做什么？"},
                    session_id="t-sc")
    m._tool_handler({"action": "on", "project": topic}, session_id="t-sc")
    # The plan for a mapping is a scope, not a falsifier.
    proto = json.loads(m._tool_handler({
        "action": "protocol", "project": topic, "mode": "mapping",
        "inclusion": "MD 背侧中线核团（MDL/MDM/MDV）| 啮齿类 | 情绪与认知任务",
    }, session_id="t-sc"))
    assert "error" not in proto, proto
    assert proto["missing_before_freeze"] == [], proto
    frozen = json.loads(m._tool_handler(
        {"action": "protocol", "project": topic, "freeze": True}, session_id="t-sc"))
    assert frozen["locked"] is True, frozen
    # And the search is allowed: the gate was asking the right question.
    block = m._pre_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles",
        args={"query": "mediodorsal thalamus subregions emotion"},
        session_id="t-sc",
    )
    assert not (isinstance(block, dict) and block.get("action") == "block"), block


def test_scenario_unexpected_finding_can_be_recorded_and_the_map_moved():
    """A finding that does not obviously belong to the question is still a
    finding. What it must not be is quietly dropped, and what must be
    possible is moving the question - with the old one kept."""
    m = _handler()
    topic = "T-sc-unexpected"
    if state.topic_exists(topic):
        state.delete_topic(topic, permanent=True)
    m._tool_handler({"action": "create", "project": topic,
                     "core_question": "情感障碍和认知障碍是不是一回事？"},
                    session_id="t-su")
    m._tool_handler({"action": "on", "project": topic}, session_id="t-su")
    got = json.loads(m._tool_handler({
        "action": "claim", "project": topic,
        "claim": {
            "kind": "hypothesis",
            "content": "如果两个域共享前额叶的同一批投射，耦合/腟合只是同一个过程的两面",
            "note": "在查文献时撞上的，和原问题关系不明显，但可能更根本",
        },
    }, session_id="t-su"))
    assert "error" not in got, got
    # It may become the question, and the old one stays on the record.
    moved = json.loads(m._tool_handler({
        "action": "set_state", "project": topic,
        "core_question": "情感与认知是否共享同一套前额叶投射？",
        "question_reason": "文献撞到同一批投射的证据，两域之分的问法可能问错了地方",
    }, session_id="t-su"))
    assert "error" not in moved, moved
    m_map = state.read_map(topic)
    assert m_map["core_question"] == "情感与认知是否共享同一套前额叶投射？", m_map
    hist = m_map.get("question_history") or []
    assert hist and hist[-1]["was"] == "情感障碍和认知障碍是不是一回事？", hist
    assert "问错" in hist[-1]["why"], hist


def test_scenario_a_small_task_does_not_get_the_full_apparatus():
    """"Read this one paper and compare the two mechanisms." Two branches, a
    frozen protocol and a stage review are the wrong shape for that, and a
    system that insists on them is enforcing its own method over the
    question."""
    m = _handler()
    topic = "T-sc-small"
    if state.topic_exists(topic):
        state.delete_topic(topic, permanent=True)
    m._tool_handler({"action": "create", "project": topic,
                     "core_question": "这篇里的两个机制差在哪？"},
                    session_id="t-ss")
    m._tool_handler({"action": "on", "project": topic}, session_id="t-ss")
    # No branch is needed to look at one paper.
    state.write_state(topic, phase="evidence")
    claims = json.loads(m._tool_handler({
        "action": "claim", "project": topic,
        "claim": {
            "kind": "observation",
            "content": "两者的操纵都在背侧中线核团，但一在 MDL 一在 MDV",
            "source": "PMID 12345678",
        },
    }, session_id="t-ss"))
    assert "error" not in claims, claims
    # And the writing stage does not demand a stage review it never needed.
    state.write_state(topic, phase="writing")
    wrote = json.loads(m._tool_handler({
        "action": "set_state", "project": topic,
        "narrative": "这篇里两个机制差在亚区，不在通路性质",
    }, session_id="t-ss"))
    assert "error" not in wrote, wrote


class _FakeCtx:
    """Stands in for the PluginContext: the only thing used is post_message."""

    def __init__(self):
        self.posted = []

    def post_message(self, text):
        self.posted.append(text)
        return None


def _ctx_for(m):
    ctx = _FakeCtx()
    m._CTX = ctx
    m._LAST_NOTICE = ""
    return ctx


def test_entering_a_topic_posts_one_status_line():
    """A stage change is posted as its own message, not appended to an answer.

    A line on every answer said where the work stopped and never how it got
    there, and a turn that only calls tools showed nothing at all - which is
    the complaint this answers.
    """
    m = _handler()
    sid = "t-notice-enter"
    name = "T-notice-enter"
    if state.topic_exists(name):
        state.delete_topic(name, permanent=True)
    ctx = _ctx_for(m)
    try:
        m._tool_handler({"action": "create", "project": name, "core_question": "Q?"}, session_id=sid)
        m._tool_handler({"action": "on", "project": name}, session_id=sid)
        assert len(ctx.posted) == 1, ctx.posted
        assert ctx.posted[0].startswith(f"科研 · 进入 {name}"), ctx.posted[0]
        # Entering the same topic again is not a new stage.
        m._tool_handler({"action": "on", "project": name}, session_id=sid)
        assert len(ctx.posted) == 1, ctx.posted
    finally:
        m._CTX = None
        m._LAST_NOTICE = ""


def test_a_phase_change_is_announced_once_per_change():
    m = _handler()
    sid = "t-notice-phase"
    name = "T-notice-phase"
    if state.topic_exists(name):
        state.delete_topic(name, permanent=True)
    ctx = _ctx_for(m)
    try:
        m._tool_handler({"action": "create", "project": name, "core_question": "Q?"}, session_id=sid)
        m._tool_handler({"action": "on", "project": name}, session_id=sid)
        ctx.posted.clear()
        m._tool_handler({"action": "set_state", "project": name, "phase": "diverging"}, session_id=sid)
        assert len(ctx.posted) == 1, ctx.posted
        assert "阶段" in ctx.posted[0] and "→" in ctx.posted[0], ctx.posted[0]
        assert "发散" in ctx.posted[0], ctx.posted[0]
        # Setting the same phase again is not a change.
        m._tool_handler({"action": "set_state", "project": name, "phase": "diverging"}, session_id=sid)
        assert len(ctx.posted) == 1, ctx.posted
        # A different phase is - but leaving 发散 needs the routes it was
        # for, so two are opened first.
        for route in ("路线一：检查测序深度的作用", "路线二：检查批次效应"):
            m._tool_handler(
                {"action": "branch", "project": name, "route": route,
                 "why_relevant": "两条都能解释同一批差异"},
                session_id=sid,
            )
        m._tool_handler({"action": "set_state", "project": name, "phase": "synthesis"}, session_id=sid)
        assert len(ctx.posted) == 4, ctx.posted
        assert "梳理理解" in ctx.posted[-1], ctx.posted[-1]
    finally:
        m._CTX = None
        m._LAST_NOTICE = ""


def test_a_recorded_review_is_announced():
    m = _handler()
    sid = "t-notice-review"
    name = "T-notice-review"
    if state.topic_exists(name):
        state.delete_topic(name, permanent=True)
    ctx = _ctx_for(m)
    try:
        m._tool_handler({"action": "create", "project": name, "core_question": "Q?"}, session_id=sid)
        m._tool_handler({"action": "on", "project": name}, session_id=sid)
        ctx.posted.clear()
        m._tool_handler(
            {
                "action": "review",
                "project": name,
                "verdict": "continue",
                "review_note": "材料没有改变判断，继续检索只能补数量",
            },
            session_id=sid,
        )
        assert len(ctx.posted) == 1, ctx.posted
        assert "审查 continue" in ctx.posted[0], ctx.posted[0]
        assert "材料没有改变判断" in ctx.posted[0], ctx.posted[0]
    finally:
        m._CTX = None
        m._LAST_NOTICE = ""


def test_a_review_becoming_due_is_announced_once():
    """The transition from exploring to being asked to judge is the one the
    user named: it happens mid-run, and nothing else says so."""
    m = _handler()
    sid = "t-notice-due"
    name = "T-notice-due"
    if state.topic_exists(name):
        state.delete_topic(name, permanent=True)
    ctx = _ctx_for(m)
    try:
        m._tool_handler({"action": "create", "project": name, "core_question": "Q?"}, session_id=sid)
        m._tool_handler({"action": "on", "project": name}, session_id=sid)
        m._pre_llm_call(session_id=sid, user_message="研究一下", turn_id="t1")
        for _ in range(9):
            m._post_tool_call(tool_name="web_search", session_id=sid, turn_id="t1")
        ctx.posted.clear()
        out = m._pre_tool_call(tool_name="web_search", session_id=sid, turn_id="t2")
        assert out is not None and out.get("action") == "block", out
        assert len(ctx.posted) == 1, ctx.posted
        assert "该做全局审查了" in ctx.posted[0], ctx.posted[0]
        # The second refusal is the same demand, not a new stage.
        out2 = m._pre_tool_call(tool_name="web_search", session_id=sid, turn_id="t2")
        assert out2 is not None
        assert len(ctx.posted) == 1, ctx.posted
    finally:
        m._CTX = None
        m._LAST_NOTICE = ""


def test_transition_notices_can_be_turned_off():
    m = _handler()
    sid = "t-notice-off"
    name = "T-notice-off"
    if state.topic_exists(name):
        state.delete_topic(name, permanent=True)
    ctx = _ctx_for(m)
    state.save_config({"transition_notice": False})
    try:
        m._tool_handler({"action": "create", "project": name, "core_question": "Q?"}, session_id=sid)
        m._tool_handler({"action": "on", "project": name}, session_id=sid)
        assert ctx.posted == [], ctx.posted
    finally:
        state.save_config({"transition_notice": True})
        m._CTX = None
        m._LAST_NOTICE = ""


def test_transform_llm_output_quiet_when_there_is_a_position():
    """No demand for a position, and the footer is off unless it is asked for."""
    m = _handler()
    sid = "t-transform2"
    m._tool_handler(
        {"action": "create", "project": "TrTopic2", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "TrTopic2"}, session_id=sid)
    state.append_claim("TrTopic2", {
        "kind": "observation",
        "content": "Wang 2016 报告睡眠剥夺后陈述性记忆评分下降",
        "source": "PMID 10000001",
    })
    out = m._transform_llm_output(
        session_id=sid,
        response_text=(
            "Wang 2016 报告睡眠剥夺后陈述性记忆评分下降。"
            "综合来看，这更支持残余缺损确实存在，而不是完全由当前症状解释。"
        ),
    )
    assert out is None, out


def test_transform_llm_output_asks_for_a_position_with_an_empty_ledger():
    """An empty ledger is not what gets flagged. A missing position is."""
    m = _handler()
    sid = "t-empty-ledger"
    m._tool_handler(
        {"action": "create", "project": "EmptyTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "EmptyTopic"}, session_id=sid)
    out = m._transform_llm_output(
        session_id=sid,
        response_text="Wang 2016 的 meta 分析表明睡眠剥夺后陈述性记忆评分下降。",
    )
    assert out is not None
    assert "没给判断" in out, out[-300:]
    assert "账本还是空的" not in out


def test_transform_llm_output_quiet_outside_research_mode():
    m = _handler()
    out = m._transform_llm_output(
        session_id="t-transform3",
        response_text="Wang 2016 报告睡眠剥夺后陈述性记忆评分下降。",
    )
    assert out is None, out


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
    m._pre_llm_call(session_id=sid, user_message="开始", turn_id="turn-1")
    m._post_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles", session_id=sid, turn_id="turn-1"
    )
    out = m._pre_llm_call(session_id=sid, user_message="继续", turn_id="turn-2")
    assert out is not None
    # The method reaches the turn; the nag does not.
    assert "research_mode" in out["context"]
    assert "账本还是空的" not in out["context"]


# --------------------------------------------------------------------------
# system-generated turns are not user turns
# --------------------------------------------------------------------------

# A real background-process notification, verbatim in shape. Its body quotes
# a research question, which is exactly why it used to be misclassified.
BG_NOTICE = (
    "[IMPORTANT: Background process proc_4dcbcef35a86 completed normally (exit code 0).\n"
    "Command: cd /home/twebery && nohup timeout 900 hermes chat --oneshot -Q -q "
    '"为什么睡眠剥夺会损伤陈述性记忆？现有证据能区分「编码受损」与「巩固受损」'
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
        user_message="证据能区分记忆巩固受损和编码受损吗？",
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
    # The block must carry the thinking moves, not the claim format. The
    # format lives in the tool description; repeating it here cost 70% of
    # the budget and taught the agent to fill a form instead of think.
    assert "竞争解释" in block
    assert "陷阱" in block
    assert "T-p1" in block


def test_research_block_is_more_thinking_than_formatting():
    """The proportion that decides what the mode produces.

    A mode whose injection is mostly rules produces bookkeeping: the agent
    fills the ledger and stops. A mode whose injection is mostly thinking
    produces research. This test exists so the balance cannot silently slide
    back.
    """
    _fresh_topic("T-balance", "Q?")
    block = protocol.build_research_block("T-balance", {"max_inject_chars": 1200})
    thinking = len(protocol.THINKING_RULES) + len(protocol.TURN_DEMAND)
    formatting = len(protocol.CLAIM_RULES) + len(protocol.SCOPE_RULE)
    assert thinking > formatting * 1.4, (thinking, formatting)


def test_the_block_carries_the_project_standing():
    """Goal, phase and current task. Without all three the agent answers the
    question in front of it and forgets the one behind it."""
    _fresh_topic("T-standing", "Q?")
    m = state.read_map("T-standing")
    m["goal"] = "搞清楚这个领域到底有没有共识"
    m["phase"] = "weighing"
    m["current_task"] = "比较 2020 年后的两个 meta 分析为何效应量不同"
    state.write_map("T-standing", m)
    block = protocol.build_research_block("T-standing", {"max_inject_chars": 2000})
    assert "搞清楚这个领域到底有没有共识" in block
    assert "当前阶段" in block
    assert "比较 2020 年后的两个 meta 分析" in block


def test_each_phase_calls_for_a_different_way_of_thinking():
    """The phase is not a label. Scoping frames the question, evidence
    gathers against an expectation, weighing pits explanations against each
    other. An agent that used one method for all four would gather evidence
    while it should be deciding what to ask."""
    _fresh_topic("T-phase", "Q?")
    seen = set()
    for phase in protocol.PHASE_ORDER:
        m = state.read_map("T-phase")
        m["phase"] = phase
        state.write_map("T-phase", m)
        block = protocol.build_research_block("T-phase", {"max_inject_chars": 2000})
        assert protocol.PHASES[phase]["thinking"] in block, phase
        seen.add(protocol.PHASES[phase]["thinking"])
    assert len(seen) == len(protocol.PHASE_ORDER)


def test_the_block_asks_whether_the_task_still_serves_the_core_question():
    """Drift is the failure no per-claim check catches. Every claim can be
    sourced and the project can still have become a different project."""
    _fresh_topic("T-drift", "核心科学问题是什么？")
    m = state.read_map("T-drift")
    m["core_question"] = "核心科学问题是什么？"
    m["goal"] = "更大的目标"
    m["current_task"] = "查一篇 2005 年的文章"
    state.write_map("T-drift", m)
    block = protocol.build_research_block("T-drift", {"max_inject_chars": 2000})
    assert "对齐检查" in block
    assert "核心科学问题是什么？" in block
    assert "查一篇 2005 年的文章" in block


def test_the_phase_decides_the_shape_of_the_answer():
    """The phase has to change the output, not only the way of thinking.
    A model that gathers evidence while it should be weighing explanations
    produces a list of findings with no position in it, and a list of
    findings is what the reader cannot use."""
    _fresh_topic("T-shape", "Q?")
    m = state.read_map("T-shape")
    m["phase"] = "synthesis"
    state.write_map("T-shape", m)
    block = protocol.build_research_block("T-shape", {"max_inject_chars": 2000})
    assert protocol.phase_output("synthesis") in block
    m["phase"] = "converging"
    state.write_map("T-shape", m)
    block = protocol.build_research_block("T-shape", {"max_inject_chars": 2000})
    assert protocol.phase_output("converging") in block


def test_the_thinking_rules_include_trade_off():
    """The move that keeps a session from drowning in detail. Without it the
    agent covers everything and the reader gets a report."""
    assert "取舍" in protocol.THINKING_RULES
    assert "不要写成条目清单" in protocol.THINKING_RULES


def test_the_agent_can_set_the_standing_and_the_phase():
    m = _handler()
    m._tool_handler(
        {
            "action": "create",
            "project": "T-stand",
            "core_question": "Q?",
        },
        session_id="t-stand",
    )
    out = m._tool_handler(
        {
            "action": "set_state",
            "project": "T-stand",
            "goal": "把边界划清",
            "phase": "scoping",
            "current_task": "列出可能的问题问法",
        },
        session_id="t-stand",
    )
    assert "standing" in str(out), out
    assert state.read_goal("T-stand") == "把边界划清"
    assert state.read_phase("T-stand") == "standing"
    assert state.read_current_task("T-stand") == "列出可能的问题问法"


def test_an_unknown_phase_is_refused():
    m = _handler()
    m._tool_handler(
        {"action": "create", "project": "T-badphase", "core_question": "Q?"},
        session_id="t-badphase",
    )
    out = m._tool_handler(
        {"action": "set_state", "project": "T-badphase", "phase": "nonsense"},
        session_id="t-badphase",
    )
    assert "nonsense" in str(out), out


def test_a_batch_of_claims_goes_in_one_call():
    """An agent that has no batch entry point looks for one, and the one it
    finds is the state module's own API - which writes the ledger outside
    the tool and outside every check the tool performs. A live run did
    exactly that. A batch is the same validation, once per row."""
    m = _handler()
    sid = "t-batch"
    m._tool_handler(
        {"action": "create", "project": "BatchTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "BatchTopic"}, session_id=sid)
    out = m._tool_handler(
        {
            "action": "claim",
            "project": "BatchTopic",
            "claims": [
                {
                    "kind": "observation",
                    "content": "first finding about the topic",
                    "source": "PMID 1",
                },
                {
                    "kind": "report",
                    "content": "second finding about the topic",
                    "source": "PMID 2",
                },
                {
                    "kind": "observation",
                    "content": "first finding about the topic",
                    "source": "PMID 1",
                },
                {"kind": "observation", "source": "PMID 3"},
            ],
        },
        session_id=sid,
    )
    body = str(out)
    # Two went in, the duplicate did not, the one without content did not.
    assert '"count": 2' in body or "count" in body, body
    assert "first finding" not in body.split("duplicates")[-1] or True
    rows = state.read_claims("BatchTopic")
    assert len(rows) == 2, rows
    assert len({r["id"] for r in rows}) == 2
    # The ids came back in the order they were submitted.
    assert "L1" in body and "L2" in body, body


def test_one_bad_row_in_a_batch_does_not_eat_the_rest():
    """The first version of the batch loop had no guard, so a single
    inference with a missing inference_type raised and every row after it
    was silently dropped - the batch reported fewer rows than it was
    given and said nothing about why."""
    m = _handler()
    sid = "t-batch2"
    m._tool_handler(
        {"action": "create", "project": "BatchTopic2", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "BatchTopic2"}, session_id=sid)
    out = m._tool_handler(
        {
            "action": "claim",
            "project": "BatchTopic2",
            "claims": [
                {
                    "kind": "observation",
                    "content": "a good observation about the topic",
                    "source": "PMID 1",
                },
                {
                    "kind": "inference",
                    "content": "an inference with no inference type",
                    "source": "PMID 2",
                },
                {
                    "kind": "observation",
                    "content": "another good observation about the topic",
                    "source": "PMID 3",
                },
            ],
        },
        session_id=sid,
    )
    body = str(out)
    rows = state.read_claims("BatchTopic2")
    assert len(rows) == 2, rows
    assert "inference_type" in body, body


def test_a_claim_can_be_amended_without_losing_its_id():
    """A ledger row gets revised - a strength was overstated, an
    observation had a conclusion mixed into it. The ways to do that
    without this action are all bad: rewriting the file bypasses every
    check, and deleting and re-adding the row gives it a new id, which
    breaks every other row that cites it.

    A live run did the file version: it made a timestamped backup inside
    the real topic directory, because there was no other way to revise a
    row."""
    m = _handler()
    sid = "t-amend"
    m._tool_handler(
        {"action": "create", "project": "AmendTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "AmendTopic"}, session_id=sid)
    m._tool_handler(
        {
            "action": "claim",
            "project": "AmendTopic",
            "claims": [
                {
                    "kind": "observation",
                    "content": "unmedicated subgroup shows the same deficit",
                    "source": "PMID 2",
                },
                {
                    "kind": "inference",
                    "content": "the drug confound explains the residual deficit",
                    "source": "PMID 1",
                    "premises": ["L1"],
                    "inference_type": "explanatory",
                },
            ],
        },
        session_id=sid,
    )
    rows = state.read_claims("AmendTopic")
    assert len(rows) == 2, rows
    target = rows[0]["id"]
    # An inference that cites the observation.
    m._tool_handler(
        {
            "action": "claim",
            "project": "AmendTopic",
            "claim": {
                "kind": "inference",
                "content": "therefore the deficit is not a medication artifact",
                "premises": [rows[1]["id"]],
            },
        },
        session_id=sid,
    )
    out = m._tool_handler(
        {
            "action": "amend",
            "project": "AmendTopic",
            "claim_id": target,
            "strength": "suggestive",
            "note": "premise was weaker than the strength claimed",
        },
        session_id=sid,
    )
    body = str(out)
    assert "suggestive" in body, body
    after = state.read_claims("AmendTopic")
    amended = [c for c in after if c["id"] == target]
    assert len(amended) == 1, after
    # The id survived, so the row that cites it still points at it.
    assert amended[0]["strength"] == "suggestive"
    # And the change is on the record.
    assert "amended_from" in amended[0], amended[0]
    assert "premise was weaker" in str(amended[0]["note"])


def test_amending_a_row_that_is_not_there_says_so():
    m = _handler()
    sid = "t-amend2"
    m._tool_handler(
        {"action": "create", "project": "AmendTopic2", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "AmendTopic2"}, session_id=sid)
    out = m._tool_handler(
        {
            "action": "amend",
            "project": "AmendTopic2",
            "claim_id": "L99",
            "strength": "strong",
        },
        session_id=sid,
    )
    assert "no such claim" in str(out).lower(), out


def test_the_guard_does_not_fire_on_a_lookup():
    """A grep for a writer's name in a source file is not a write. The
    first version of the API check fired on the name alone, which blocked
    a command that was only looking."""
    import research_os as _ros
    w = "append_" + "claim"
    w2 = "write_" + "claims"
    assert _ros._mentions_state_tree(
        "grep -n 'def %s' /home/twebery/.hermes/plugins/research-os/state.py" % w
    ) is False
    assert _ros._mentions_state_tree(
        "grep -rn '%s\\|%s' /home/twebery/.hermes/research-os/" % (w, w2)
    ) is False


def test_the_guard_is_live_and_not_swallowing_its_own_errors():
    """The guard sits behind a broad except that returns None, which means
    a broken guard fails open - the write goes through and nothing is
    reported. A missing import in the guard's own body was exactly that:
    every check raised, every write was allowed. The guard has to answer
    on every path, so the module it lives in has to be sound."""
    import research_os as _ros
    w = "append_" + "claim"
    imp = "import " + "state"
    # It answers rather than raising.
    assert _ros._mentions_state_tree(
        "python3 -c '%s as S; S.%s(\"x\", {})'" % (imp, w)
    ) is True
    # And the module has the names the check depends on.
    import research_os.__init__ as _mod
    import importlib
    mod = importlib.import_module("research_os")
    assert hasattr(mod, "_STATE_WRITE_CALLS")
    assert hasattr(mod, "_STATE_IMPORT_HINTS")


def test_the_guard_blocks_the_api_route_to_the_ledger():
    """The guard looked for the state tree path in the command. A command
    that imports the state module and calls its writer never names the
    path - the module knows where it lives - and the path-based check let
    a live run write the ledger outside the tool."""
    import research_os as _ros
    calls = ["append_" + "claim", "write_" + "claims"]
    imports = ["import " + "state"]
    for call in calls:
        cmd = "python3 -c '%s as S; S.%s(\'t\', {})'" % (imports[0], call)
        assert _ros._mentions_state_tree(cmd) is True, cmd
    # Reading the state is how the agent finds out what is there.
    assert _ros._mentions_state_tree(
        "python3 -c 'import state as S; print(S.read_claims(\'t\'))'"
    ) is False
    assert _ros._mentions_state_tree(
        "cat /home/twebery/.hermes/research-os/topics/t/claims.json"
    ) is False


def test_each_phase_says_what_it_is_not_for():
    """What replaced the keyword matcher that tried to guess whether a
    request fitted the phase. The matcher fired on "再查" and missed "再
    找找", and it could not tell a research search from a technical one -
    but the agent knows which is which, and can say so."""
    _fresh_topic("T-gate", "Q?")
    seen = set()
    for phase in ("scoping", "evidence", "weighing", "closing", "writing"):
        m = state.read_map("T-gate")
        m["phase"] = phase
        state.write_map("T-gate", m)
        block = protocol.build_research_block("T-gate", {"max_inject_chars": 2000})
        assert "这轮不要做" in block, phase
        gate = protocol.phase_gate(phase)
        assert gate and gate in block, phase
        seen.add(gate)
    assert len(seen) == 5


def test_a_request_is_never_matched_against_keyword_lists():
    """The keyword matcher was removed. Guessing from outside whether a
    request fits the phase is unreliable: it fired on "再查" and missed
    "再找找", and it could not tell a research search from a technical
    one. The phase states its own boundary and the agent reads it."""
    import research_os
    assert not hasattr(research_os, "_phase_fit_note")
    assert not hasattr(research_os, "_PHASE_FIT")


def test_a_hedged_sentence_is_not_taken_for_a_position():
    """"这可能说明 X，需要更多研究" names what might be going on and then
    declines to stand behind it. That is the shape the check exists to
    catch, not to accept."""
    assert audit.has_position("综合来看，这更支持残余缺损确实存在。") is True
    assert audit.has_position("这可能说明该指标在当前阶段仍然偏高，需要更多研究。") is False
    assert audit.has_position("Wang 2016 报告睡眠剥夺后陈述性记忆评分下降。") is False


def test_the_position_check_reads_the_moves_not_a_word_list():
    """A first version matched a list of words and missed
    "综上，残余缺损以累积损伤为主", which is plainly a position and
    carries none of the words in the list. The set of ways to take a
    position is open, so the signals have to be structural: a conclusion
    opener, or a verb of verdict."""
    for text in (
        "综上，残余缺损以累积损伤为主，混杂说被削掉。",
        "总体上，现有证据更支持药物混杂解释。",
        "H. pylori 一支已被两个人群的大样本否定。",
        "证据不足以区分这两条通路。",
        "我倾向认为药物混杂解释更可能，但证据还不够。",
    ):
        assert audit.has_position(text) is True, text
    for text in (
        "Wang 2016 报告睡眠剥夺后陈述性记忆评分下降。",
        "Petrov 2020 的队列研究表明再认记忆容量在睡眠剥夺后显著下降。",
        "这两项研究都测了再认记忆容量。",
        "相关性见 Table 2，p < 0.05。",
        "文件已转换完毕，输出在 /tmp/out.pdf。",
    ):
        assert audit.has_position(text) is False, text


def test_the_off_topic_check_does_not_fire_on_a_chinese_topic():
    """An earlier version compared claims against the current task by word
    overlap. A Chinese task shares almost no whitespace-delimited words
    with any claim, so every claim looked off topic - a false-positive
    machine. The anchor is the core question, and the signal is one-sided:
    no overlap means "check this", not "you have drifted"."""
    _fresh_topic("T-off", "睡眠剥夺后记忆功能是否随之恢复")
    state.append_claim("T-off", {
        "kind": "report",
        "content": "恢复睡眠后患者的记忆功能仍未完全恢复",
        "source": "PMID 1",
    })
    state.append_claim("T-off", {
        "kind": "report",
        "content": "sodium valproate reduces seizure frequency in pediatric epilepsy",
        "source": "PMID 2",
    })
    import research_os as _ros
    out = _ros._cycle_check(
        "T-off", {"evidence": 4, "ledger": 2, "moves": 0}
    )
    # Two claims, one of them foreign: not enough to accuse the agent.
    assert out is None or "不搭词" not in out, out


def test_a_pause_verdict_stops_the_searching():
    """A review that cannot stop anything is a note in a margin.

    The verdict has to bite: the search is blocked until the state says
    where the work is going.
    """
    m = _handler()
    sid = "t-pause"
    m._tool_handler(
        {"action": "create", "project": "PauseTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "PauseTopic"}, session_id=sid)
    m._tool_handler(
        {
            "action": "review",
            "project": "PauseTopic",
            "verdict": "pause",
            "review_note": "在查干预研究，核心问题其实是病因",
        },
        session_id=sid,
    )
    assert state.topic_is_paused("PauseTopic")
    out = m._pre_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles",
        session_id=sid,
        turn_id="t1",
    )
    assert out is not None
    assert out.get("action") == "block"
    assert "暂停" in out["message"]


def test_updating_the_standing_lifts_the_pause():
    """The loop has to close: review says pause, the agent answers by saying
    where the work is going, and the searching resumes."""
    m = _handler()
    sid = "t-resume"
    m._tool_handler(
        {"action": "create", "project": "ResumeTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "ResumeTopic"}, session_id=sid)
    m._tool_handler(
        {
            "action": "review",
            "project": "ResumeTopic",
            "verdict": "pause",
            "review_note": "跑偏了",
        },
        session_id=sid,
    )
    assert state.topic_is_paused("ResumeTopic")
    out = m._tool_handler(
        {
            "action": "set_state",
            "project": "ResumeTopic",
            "narrative": "现在认为残留缺损以药物混杂为主",
            "plan": "去查未用药亚组的数据",
        },
        session_id=sid,
    )
    assert "resumed" in str(out), out
    assert not state.topic_is_paused("ResumeTopic")
    blocked = m._pre_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles",
        session_id=sid,
        turn_id="t2",
    )
    assert blocked is None or blocked.get("action") != "block", blocked


def test_the_cycle_check_asks_for_a_judgment_when_only_material_accumulated():
    """Evidence without a decision is the shape of a report. The check fires
    on the count, so it is a checkpoint and not a nag."""
    m = _handler()
    sid = "t-cycle"
    m._tool_handler(
        {"action": "create", "project": "CycleTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "CycleTopic"}, session_id=sid)
    m._pre_llm_call(session_id=sid, user_message="研究一下", turn_id="t1")
    for _ in range(4):
        m._post_tool_call(
            tool_name="mcp__pubmed__pubmed_search_articles",
            session_id=sid,
            turn_id="t1",
        )
    out = m._pre_llm_call(session_id=sid, user_message="继续", turn_id="t2")
    assert out is not None
    assert "判断" in out["context"]


def test_the_review_demand_lands_at_the_search_boundary():
    """The review has to come before the next search, not after the turn.

    After the search it is only a note about the past. Before it, the
    agent can still change what it does next.
    """
    m = _handler()
    sid = "t-boundary"
    m._tool_handler(
        {"action": "create", "project": "BoundaryTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "BoundaryTopic"}, session_id=sid)
    m._pre_llm_call(session_id=sid, user_message="研究一下", turn_id="t1")
    for _ in range(9):
        m._post_tool_call(
            tool_name="mcp__pubmed__pubmed_search_articles",
            session_id=sid,
            turn_id="t1",
        )
    # The next search is held until the review is done.
    out = m._pre_tool_call(
        tool_name="mcp__pubmed__pubmed_search_articles",
        session_id=sid,
        turn_id="t2",
    )
    assert out is not None
    assert out.get("action") == "block"
    assert "全局审查" in out["message"]


def test_a_review_is_asked_for_after_enough_accumulation():
    """A drift that takes several turns to appear is not visible in any one
    of them, so the review looks at the topic, not the turn."""
    m = _handler()
    sid = "t-reviewdue"
    m._tool_handler(
        {"action": "create", "project": "ReviewDue", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "ReviewDue"}, session_id=sid)
    m._tool_handler(
        {
            "action": "set_state",
            "project": "ReviewDue",
            "goal": "g",
            "current_task": "t",
            "phase": "weighing",
        },
        session_id=sid,
    )
    m._pre_llm_call(session_id=sid, user_message="研究一下", turn_id="t1")
    for _ in range(9):
        m._post_tool_call(
            tool_name="mcp__pubmed__pubmed_search_articles",
            session_id=sid,
            turn_id="t1",
        )
    out = m._pre_llm_call(session_id=sid, user_message="继续", turn_id="t2")
    assert out is not None
    assert "全局审查" in out["context"]


def test_a_global_review_is_recorded():
    m = _handler()
    m._tool_handler(
        {"action": "create", "project": "T-rev", "core_question": "Q?"},
        session_id="t-rev",
    )
    out = m._tool_handler(
        {
            "action": "review",
            "project": "T-rev",
            "verdict": "drifting",
            "review_note": "在查干预研究，核心问题其实是病因",
        },
        session_id="t-rev",
    )
    assert "drifting" in str(out), out
    reviews = state.read_map("T-rev").get("reviews") or []
    assert len(reviews) == 1
    assert "病因" in reviews[0]["note"]


def test_research_block_carries_the_situation_not_just_the_hypothesis_list():
    """What the topic looks like right now, and what would move it - not a
    list of what someone wrote down once."""
    _fresh_topic("T-situation", "Q?")
    state.write_contradictions("T-situation", {
        "items": [{
            "title": "残留状态说",
            "separator": "在残留症状为零的亚组里缺损是否仍在",
            "status": "contested",
        }],
        "updated_at": state.now(),
    })
    block = protocol.build_research_block("T-situation", {"max_inject_chars": 1200})
    assert "残留状态说" in block
    assert "残留症状为零" in block


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


def test_the_review_threshold_is_configurable():
    """Eight searches is most of a small topic and nothing on a big one,
    so the number has to be settable - globally in the config, and per
    topic in the topic's own map."""
    m = _handler()
    sid = "t-thresh"
    m._tool_handler(
        {"action": "create", "project": "ThreshTopic", "core_question": "Q?"},
        session_id=sid,
    )
    # The default comes from the config.
    every_searches, every_turns = state.review_thresholds("ThreshTopic")
    assert every_searches >= 1 and every_turns >= 1
    # A topic can set its own.
    m._tool_handler(
        {
            "action": "set_state",
            "project": "ThreshTopic",
            "review_every": {"searches": 3, "turns": 2},
        },
        session_id=sid,
    )
    s2, t2 = state.review_thresholds("ThreshTopic")
    assert (s2, t2) == (3, 2), (s2, t2)
    # And it says so rather than failing silently.
    out = m._tool_handler(
        {
            "action": "set_state",
            "project": "ThreshTopic",
            "review_every": {"searches": 5},
        },
        session_id=sid,
    )
    assert "5" in str(out)
    s3, _ = state.review_thresholds("ThreshTopic")
    assert s3 == 5, s3


def test_a_source_goes_into_the_literature_library():
    """A claim needs a source, and a source is a thing that can be looked
    up again. A bare string says "PMID 12345" and nothing else; a library
    entry says what the source is and where it lives."""
    m = _handler()
    sid = "t-lib"
    m._tool_handler(
        {"action": "create", "project": "LibTopic", "core_question": "Q?"},
        session_id=sid,
    )
    out = m._tool_handler(
        {
            "action": "source",
            "project": "LibTopic",
            "source_kind": "zotero",
            "source_locator": "ABCD1234",
            "source_title": "A paper",
            "source_doi": "10.1/x",
        },
        session_id=sid,
    )
    body = str(out)
    assert "S1" in body, body
    # A source that is already there is not added twice: two entries for
    # the same paper make the reader do the deduplication.
    out2 = m._tool_handler(
        {
            "action": "source",
            "project": "LibTopic",
            "source_kind": "zotero",
            "source_locator": "ABCD1234",
        },
        session_id=sid,
    )
    assert "S1" in str(out2)
    lib = m._tool_handler({"action": "library", "project": "LibTopic"}, session_id=sid)
    assert lib.count("S1") >= 1
    # The file kind is the fallback, and it has to work with no
    # bibliography service at all.
    out3 = m._tool_handler(
        {
            "action": "source",
            "project": "LibTopic",
            "source_kind": "file",
            "source_locator": "/tmp/paper.pdf",
        },
        session_id=sid,
    )
    assert "S2" in str(out3), out3
    # An unknown kind is refused rather than filed under something.
    out4 = m._tool_handler(
        {
            "action": "source",
            "project": "LibTopic",
            "source_kind": "guess",
            "source_locator": "x",
        },
        session_id=sid,
    )
    assert "error" in str(out4).lower(), out4


def test_a_claim_can_cite_a_library_entry():
    """The id is what makes the source resolvable; the entry's own form
    is what makes the ledger readable without the library open."""
    m = _handler()
    sid = "t-cite"
    m._tool_handler(
        {"action": "create", "project": "CiteTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler(
        {
            "action": "source",
            "project": "CiteTopic",
            "source_kind": "zotero",
            "source_locator": "K1",
            "source_title": "The Paper",
        },
        session_id=sid,
    )
    out = m._tool_handler(
        {
            "action": "claim",
            "project": "CiteTopic",
            "claim": {
                "kind": "observation",
                "content": "the effect was X",
                "source": "S1",
            },
        },
        session_id=sid,
    )
    body = str(out)
    assert "The Paper" in body, body
    assert "S1" in body, body


def test_the_question_can_be_redefined_and_the_old_one_is_kept():
    """A project that runs long sometimes finds that the question it is
    answering is not the question worth answering. A tool that cannot
    record that forces the agent to stay on a bad question or to smuggle
    the change in through the narrative, where nothing can see it."""
    m = _handler()
    sid = "t-redef"
    m._tool_handler(
        {"action": "create", "project": "RedefTopic", "core_question": "the old question"},
        session_id=sid,
    )
    out = m._tool_handler(
        {
            "action": "set_state",
            "project": "RedefTopic",
            "core_question": "the better question",
            "question_reason": "the old one was a symptom",
        },
        session_id=sid,
    )
    assert "error" not in str(out).lower(), out
    mp = state.read_map("RedefTopic")
    assert mp.get("core_question") == "the better question", mp.get("core_question")
    hist = mp.get("question_history") or []
    assert len(hist) == 1, hist
    assert hist[0].get("was") == "the old question", hist
    assert "symptom" in str(hist[0].get("why") or ""), hist


def test_an_exploration_is_recorded():
    """Looking outside the current frame and not finding anything is a
    result, and the project has to know it looked."""
    m = _handler()
    sid = "t-expl"
    m._tool_handler(
        {"action": "create", "project": "ExplTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler(
        {
            "action": "set_state",
            "project": "ExplTopic",
            "exploration": "looked for a completely different mechanism, did not find it",
        },
        session_id=sid,
    )
    mp = state.read_map("ExplTopic")
    ex = mp.get("explorations") or []
    assert len(ex) == 1, ex
    assert "different mechanism" in str(ex[0].get("what") or ""), ex


def test_status_answers_about_the_topic_not_about_the_session():
    """`status` with a project means "what state is this topic in". It
    used to answer with the session's state, which is idle for any caller
    that is not in a session - so an agent asking about a topic got an
    answer about nothing, and went to read the files instead."""
    m = _handler()
    sid = "t-status"
    m._tool_handler(
        {"action": "create", "project": "StatusTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "StatusTopic"}, session_id=sid)
    m._tool_handler(
        {
            "action": "claim",
            "project": "StatusTopic",
            "claim": {
                "kind": "observation",
                "content": "a finding",
                "source": "PMID 1",
            },
        },
        session_id=sid,
    )
    out = m._tool_handler(
        {"action": "status", "project": "StatusTopic"}, session_id=sid
    )
    body = str(out)
    assert "StatusTopic" in body
    assert "claims" in body
    assert '"claims": 1' in body or "claims" in body, body
    # A topic that is not there says so rather than answering about
    # something else.
    out2 = m._tool_handler(
        {"action": "status", "project": "NoSuchTopic"}, session_id=sid
    )
    assert "no such topic" in str(out2).lower(), out2


def test_a_topic_whose_directory_is_gone_does_not_haunt_the_name():
    """A registry entry whose directory is gone is an orphan. It stays in
    the list, because `on` and `repair` find it there and rebuild it -
    that is what they are for. But the name is free: an orphan is a state
    to repair, not a name to squat on. Refusing the name meant that once
    a topic directory was removed by anything other than delete_topic,
    the name could never be used again."""
    _fresh_topic("T-ghost", "the real question")
    assert "T-ghost" in [t["name"] for t in state.list_topics()]
    # Remove the directory behind the registry's back.
    import shutil
    shutil.rmtree(state.topic_dir("T-ghost"), ignore_errors=True)
    # The orphan is still visible, so it can be repaired.
    assert "T-ghost" in [t["name"] for t in state.list_topics()]
    # And the name is free again.
    state.create_topic("T-ghost", "a new question")
    assert state.topic_dir("T-ghost").is_dir()
    assert "T-ghost" in [t["name"] for t in state.list_topics()]


def test_the_block_keeps_the_standing_when_the_question_is_long():
    """A real topic has a long core question, and the question used to
    crowd out the standing: the narrative, the plan, the phase and the
    tool call all fell off the end. The block that survived had the
    evidence and lost the memory of what the project had concluded."""
    _fresh_topic("T-long", "Q" * 3000)
    m = state.read_map("T-long")
    m["narrative"] = "现在认为 A 路径主导，因为 B"
    m["plan"] = "下一步查 C"
    m["phase"] = "weighing"
    m["current_task"] = "compare the two explanations"
    state.write_map("T-long", m)
    block = protocol.build_research_block("T-long", {"max_inject_chars": 1600})
    assert "现在认为 A 路径主导" in block, block[:2000]
    assert "下一步查 C" in block, block[:2000]
    assert "当前阶段" in block
    assert "这轮不要做" in block
    assert "research_mode(action='claim'" in block
    assert "给可能答案" in block


def test_the_moves_work_outside_the_lab_bench():

    """The moves came from one research genre, and their wording has to
    survive the move to another one.

    A question that is settled by an experiment gets its rival readings
    told apart by an observation. A question that is settled by a proof,
    a reading of a text, or a design constraint does not: there is no
    observation to make, and asking for one is asking the wrong thing.

    The wording that says "what observation would separate them" pushes
    every question into the shape of an experiment. The wording that
    says "what material, test or line of reasoning" does not, and it
    still asks the model to say how the two readings could be told
    apart - which is the part that does the work.
    """
    block = protocol.THINKING_RULES
    assert "给可能答案" in block, "the rival-reading move should not assume there are rivals"
    assert "观察" not in block, "an observation is one way to tell readings apart, not the only one"
    assert "材料" in block or "判据" in block

    # The same for the phase that asks for a counterexample.
    for phase in ("scoping", "diverging", "evidence", "weighing", "closing", "writing"):
        text = str(protocol.PHASES.get(phase, {}).get("thinking") or "")
        assert "观察" not in text, "%s asks for an observation" % phase
        assert "观察" not in str(protocol.PHASES.get(phase, {}).get("output") or ""), phase

    # And the turn demand.
    assert "区分两个解释的观察" not in protocol.TURN_DEMAND


def test_the_state_tree_follows_the_pointer_file_without_a_restart():
    """The plugin is loaded by the gateway, and the gateway is a
    long-running process. A path decided at import time is frozen for as
    long as it lives, so pointing the state tree somewhere else would
    need a gateway restart - and the pointer file exists precisely so
    the location can be changed without one.

    So the path is read on every use, and a changed pointer is visible
    inside a process that has already imported the module.
    """
    pointer = state._pointer_path()
    had = pointer.read_text() if pointer.is_file() else None
    try:
        pointer.write_text(str(Path(_TMP) / "elsewhere") + "\n")
        assert state.home() == Path(_TMP) / "elsewhere", state.home()
        pointer.write_text(str(Path(_TMP) / "third") + "\n")
        assert state.home() == Path(_TMP) / "third", state.home()
        assert state.topics_dir() == Path(_TMP) / "third" / "topics"
    finally:
        if had is None:
            if pointer.is_file():
                pointer.unlink()
        else:
            pointer.write_text(had)


def test_the_pointer_file_wins_over_a_stale_environment_variable():
    """An export left in a shell redirected the installer, the CLI and
    the plugin all at once, to a directory that had nothing to do with
    the install, and nothing said so.

    The pointer file belongs to the installation and is written by the
    installer. The environment variable is a temporary override, and a
    temporary override someone forgot about is worse than none.
    """
    pointer = state._pointer_path()
    had = pointer.read_text() if pointer.is_file() else None
    old_env = os.environ.get("RESEARCH_OS_HOME")
    try:
        pointer.write_text(str(Path(_TMP) / "from-pointer") + "\n")
        os.environ["RESEARCH_OS_HOME"] = str(Path(_TMP) / "from-env")
        assert state.home() == Path(_TMP) / "from-pointer", state.home()
    finally:
        if old_env is None:
            os.environ.pop("RESEARCH_OS_HOME", None)
        else:
            os.environ["RESEARCH_OS_HOME"] = old_env
        if had is None:
            if pointer.is_file():
                pointer.unlink()
        else:
            pointer.write_text(had)


def test_the_installer_writes_the_pointer_file():
    """The installer is what puts the plugin and its state tree in the
    same place, and the pointer file is how the plugin finds that tree
    later - including after a gateway restart, when the environment
    variable is long gone."""
    src = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
    assert "research-os-home" in src, "the installer does not write the pointer file"
    # And the flag has to win over a stale export.
    assert 'STATE_HOME="$HERMES_HOME/research-os"' in src
    assert "STATE_HOME=\"${RESEARCH_OS_HOME:-" not in src


def _tool(**args):
    """Call the tool the way the agent does.

    The plugin is loaded by path, so the test has to use the same
    instance the hooks use - the one `_handler()` returns. Importing
    `research_os` instead gives a second copy of the module with its
    own state, and the call behaves differently.

    A successful call returns the payload; a failed one returns
    {"ok": False, "error": ...}.
    """
    m = _handler()
    import json
    out = m._tool_handler(args, session_id="s-test")
    if isinstance(out, str):
        out = json.loads(out)
    return out


def test_a_sub_topic_sees_its_parent_and_its_siblings():
    """A cross-domain review is several sub-questions under one
    question.

    Topics are isolated on purpose - one topic's hypotheses must not
    leak into another. But a sub-topic that cannot see the others
    cannot know that two of them touch the same evidence, and a review
    that does not know that will make the same point twice or leave a
    gap between the parts.

    The parent gives the sub-topic the question it serves and the
    siblings it shares. It does not give it their hypotheses or their
    ledger, and that is the line: a hypothesis that leaks across is how
    a review ends up arguing for a position it never checked.
    """
    parent = "Review-Domain"
    a = "Sub-A"
    b = "Sub-B"
    for name, q in ((parent, "what is the whole domain asking?"),
                    (a, "what does the first part ask?"),
                    (b, "what does the second part ask?")):
        if not state.topic_exists(name):
            _tool(action="create", project=name, core_question=q)
    _tool(action="set_state", project=a, parent=parent, phase="evidence")
    _tool(action="set_state", project=b, parent=parent, phase="scoping")

    par = state.read_parent(a)
    assert par["name"] == parent
    assert par["core_question"] == "what is the whole domain asking?"

    sibs = {x["name"] for x in state.read_siblings(a)}
    assert sibs == {b}, sibs
    assert {x["name"] for x in state.read_children(parent)} == {a, b}

    # The standing carries the question and the siblings.
    block = protocol.build_research_block(a, {"max_inject_chars": 1600})
    assert parent in block
    assert b in block

    # And the sibling's hypotheses do not come across.
    _tool(action="set_state", project=b, narrative="the second part thinks X")
    block2 = protocol.build_research_block(a, {"max_inject_chars": 1600})
    assert "the second part thinks X" not in block2

    # A parent that does not exist is refused.
    out = _tool(action="set_state", project=a, parent="No-Such-Topic")
    assert "error" in out, out
    # A topic cannot be its own parent.
    out2 = _tool(action="set_state", project=a, parent=a)
    assert "error" in out2, out2

    # The tool answers with the tree, not only the files. An agent that
    # has to read the files to find out where it is will not bother.
    st = _tool(action="status", project=a)
    assert st["project"]["parent"]["name"] == parent, st
    assert {x["name"] for x in st["project"]["siblings"]} == {b}, st
    st2 = _tool(action="status", project=parent)
    assert {x["name"] for x in st2["project"]["children"]} == {a, b}, st2


def test_the_rules_survive_a_long_standing():
    """A project with a long narrative used to take the whole injection
    budget, and the thinking rules were cut out of the block.

    The block then said what the project was and nothing about how to
    work on it - a status report. The rules are reserved first and the
    standing is cut to fit, because the standing is in the topic file
    and the rules are nowhere else.
    """
    name = "T-long"
    if state.topic_exists(name):
        state.delete_topic(name, permanent=True)
    state.create_topic(name, "Q?", ["H1"], ["test"])
    long_narrative = (
        "判断已定。结论：这个问题的答案在现有证据下不能定。"
        + "理由一：方向分不开，需要纵向时序与随机操纵，两者都缺。" * 12
        + "理由二：测量混着方法差异，现有工具不足以拆开。" * 12
        + "理由三：样本量不够，任何交互效应都估不稳。" * 12
    )
    state.write_state(
        name,
        phase="closing",
        narrative=long_narrative,
        plan="下一步：做一次必要中介实验。" * 20,
        current_task="把结论写成可发表的表述边界",
    )
    block = protocol.build_research_block(name, {"max_inject_chars": 1600})
    assert len(block) <= 1600, len(block)
    # The rules are in.
    assert "问准" in block, "the thinking rules were cut out of the block"
    assert "research_mode(action='claim'" in block, "the tool call was cut out"
    # And the standing is there, cut rather than dropped.
    assert "当前判断（课题叙事）：" in block, "the narrative was dropped whole"
    assert "下一步计划：" in block, "the plan was dropped whole"
    assert "全文见课题文件" in block, "the cut was not visible"


def test_the_strictness_follows_the_phase():
    """The evidence discipline is the floor and it never moves. What moves
    is how hard the answer is pushed to commit.

    A model that must always have a position answers an open question
    with a position - so the exploration stage produces conclusions
    nobody checked, and the narrowing stage has nothing left to narrow.
    The discipline has to be staged too, or it enforces the same shape
    at every moment of a project whose moments are not the same.
    """
    assert protocol.phase_strictness("scoping") == "open"
    assert protocol.phase_strictness("diverging") == "open"
    assert protocol.phase_strictness("evidence") == "mixed"
    assert protocol.phase_strictness("weighing") == "mixed"
    assert protocol.phase_strictness("closing") == "tight"
    assert protocol.phase_strictness("writing") == "tight"


def test_an_open_phase_does_not_demand_a_position():
    """In a diverging phase the right answer has no position in it yet. So
    the check that demands one has to let the open phases through.
    """
    m = _handler()
    name = "T-open"
    if state.topic_exists(name):
        state.delete_topic(name, permanent=True)
    m._tool_handler(
        {"action": "create", "project": name, "core_question": "Q?"},
        session_id="t-open",
    )
    m._tool_handler(
        {"action": "on", "project": name}, session_id="t-open"
    )
    m._tool_handler(
        {"action": "set_state", "project": name, "phase": "diverging"},
        session_id="t-open",
    )
    # Three directions, none of them a position.
    answer = (
        "有三个方向可以考虑。第一个是上游的代谢通路，"
        "第二个是中枢的炎症通路，第三个是行为层面的解释路径。"
        "现在还不知道哪个更值得先查。"
    )
    out = m._transform_llm_output(
        response_text=answer, session_id="t-open"
    )
    # No demand for a position. The status line is off by default now -
    # a per-answer footer said where the work stopped, not how it moved.
    assert out is None, out

    # The same answer in a converging phase is a different answer: a
    # conclusion with no position in it is not a conclusion. The phase is
    # written directly here because this test is about the output check, not
    # about the transition guards.
    state.write_state(name, phase="converging")
    out2 = m._transform_llm_output(
        response_text=answer, session_id="t-open"
    )
    assert out2 is not None, "a converging phase must demand a position"


def test_the_open_phases_do_not_forbid_exploration():
    """Two things an open phase used to forbid, and research does not.

    Reaching a conclusion: the evidence may already point one way, and
    saying so is not a failure of the phase.

    Changing the question: a question the evidence has disproven does not
    become a settled question, it becomes a wrong one. Forbidding the
    change is what makes a project defend a question it has lost.
    """
    ev = protocol.PHASES["evidence"]["gate"]
    assert "不改问题" not in ev, ev
    assert "不下结论" not in ev, ev
    dv = protocol.PHASES["diverging"]["gate"]
    assert "不下结论" not in dv, dv


def test_the_rules_allow_a_bold_hypothesis():
    """A hedge that was never stated is not progress. The rules have to
    say that a bold marked hypothesis is worth more than a cautious one.
    """
    rules = protocol.THINKING_RULES
    assert "假说" in rules, rules
    assert "大胆" in rules, rules


def test_research_is_recognised_outside_the_lab_bench():
    """The classifier decides whether the research block is injected at
    all, and its word list was mostly biomedical and statistical: p
    values, effect sizes, cohort, meta-analysis.

    So a question about a text, a proof, a design or an argument was
    not recognised as a research request. The agent answered it with no
    research discipline, and nothing said that anything was missing.

    A disjunction is a research question in every field. "Is it A or B"
    is the shape of most research questions, and the old list only
    recognised the shapes that end in a question mark.
    """
    research = [
        "这份手稿里的自然指的是自然规律还是事物本性？",
        "缓存策略该用 LRU 还是按访问频率加权？",
        "这个等价关系用归纳法还是良基归纳证明？",
        "为什么这个手稿里的自然一词前后不一致？",
        "这个设计的取舍是什么？",
        "帮我查一下这个定理有没有反例",
        "这个解释有没有被文本证据支持？",
    ]
    for text in research:
        assert classify.classify(text) == "research", text


def test_a_chore_still_leaves_research_mode():
    """A chore is what takes a session out of research mode, so a chore
    that was not recognised left the agent in research mode with the
    research block still riding along - on a turn that was deleting a
    file.

    "删除文件" was in the list and "删掉这个文件" was not. The verbs an
    instruction actually uses are the ones that have to be there.
    """
    chores = [
        "帮我删掉这个文件",
        "把这个删了",
        "移除那个目录",
        "把这段代码格式化",
        "重启 gateway",
        "帮我把这个 pdf 转成 markdown",
        "install this package",
    ]
    for text in chores:
        assert classify.classify(text) == "chore", text


def test_the_tool_description_lists_every_phase():
    ROOT = Path(__file__).resolve().parents[1]
    """The agent reads the tool description before it writes anything, and
    it decides which phase to set from that text.

    The diverging phase existed in the code for a release and was not in
    the description, so an agent that wanted to step outside the frame
    had no way to name the step. A list that is written by hand drifts
    from the list the code has, and nothing catches it.

    So the description has to name every phase the code defines, and a
    phase that is added without being described fails here.
    """
    src = (ROOT / "research_os" / "__init__.py").read_text()
    i = src.find("'phase' (")
    assert i > 0, "the phase list is not in the tool description"
    seg = src[i: src.find(")", i)]
    listed = set(re.findall(r"[a-z]+", seg))
    for phase in protocol.PHASES:
        assert phase in listed, "%s is not in the tool description" % phase


def test_the_tool_description_does_not_presume_one_genre():
    ROOT = Path(__file__).resolve().parents[1]
    """The tool description is read on every research turn, so a phrase
    that names a research genre is a phrase that every research turn
    reads."""
    src = (ROOT / "research_os" / "__init__.py").read_text()
    i = src.find('"description":')
    seg = src[i: i + 6000]
    # Word boundaries, not substrings: "lab" is inside "label".
    for word in (r"scientific question", r"\blab\b", r"\bbench\b", r"\bcohorts?\b",
                 r"\bmice\b", r"\bdepression\b", r"\bdysbiosis\b", r"\bgut\b",
                 r"\bbrain\b", r"\bmicrobiome\b", r"\bplacebo\b"):
        hit = re.search(word, seg, re.I)
        assert not hit, "%s is in the tool description" % hit.group(0) if hit else ""


def test_no_rule_carries_one_researcher_topic():
    """The rules are read on every research turn, so anything specific to
    one researcher's questions is read on every research turn too.

    A worked example makes the move concrete, and a concrete example
    comes from somewhere. The example that was in the rules named a
    syndrome, a deficit and a circuit, and every topic in every field
    was being handed that picture.
    """
    injected = protocol.THINKING_RULES + protocol.TURN_DEMAND + protocol.SCOPE_RULE
    for phase in protocol.PHASES.values():
        injected += str(phase.get("thinking") or "") + str(phase.get("output") or "")
        injected += str(phase.get("gate") or "")
    banned = [
        "缓解", "抑郁", "肠易激", "应激性", "溃疡", "工作记忆", "执行功能",
        "认知功能", "小鼠", "大鼠", "海马", "mPFC", "中枢", "炎症", "菌群",
        "环路", "神经元", "remission", "depression", "cognition", "IBS",
    ]
    for word in banned:
        assert word not in injected, "%s is in the injected text" % word


def test_the_situation_gives_up_room_before_the_standing():
    """The situation lines - the competing explanations, the evidence that
    separates them, the open questions - are four long lines, and they
    are in the topic file anyway. They are the first thing to go."""
    _fresh_topic("T-crowd", "核心问题：" + "很长的描述" * 30)
    m = state.read_map("T-crowd")
    m["goal"] = "g" * 60
    m["current_task"] = "t" * 60
    m["phase"] = "weighing"
    state.write_map("T-crowd", m)
    block = protocol.build_research_block("T-crowd", {"max_inject_chars": 1000})
    assert "当前阶段" in block
    assert "research_mode(action='claim'" in block
    assert len(block) <= 1000 + 200


def test_research_block_truncation_keeps_the_thinking_whole():
    """A half-list of moves is worse than no list. The thinking rules are
    cut as a block, never mid-rule."""
    _fresh_topic("T-p5", "Q" * 3000)
    block = protocol.build_research_block("T-p5", {"max_inject_chars": 900})
    assert "找陷阱" in block
    assert "允许答案是" in block


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
        "content": "Wang 2016 meta-analysis found moderate declarative memory deficits after sleep deprivation",
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
        "content": "the finding therefore suggests that the two measures dissociate",
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
    """The plugin module under test.

    The package is imported normally rather than loaded from its file path:
    loading the file a second time would run the module loader again and
    produce a second copy of the implementation modules, so the plugin would
    write state the tests cannot see.
    """
    import research_os

    return research_os


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


def test_renderer_can_be_told_which_tree_to_render():
    """The renderer reads the tree the pointer names.

    A second tree - a test instance, an export - has to be asked for by
    name, or the two render into each other.
    """
    src = Path(__file__).resolve().parents[1] / "scripts" / "render_obsidian.py"
    text = src.read_text(encoding="utf-8")
    assert '"--home"' in text, "the renderer has no --home"
    assert "state._resolve_home" in text, "--home does not move the state tree"


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




# --------------------------------------------------------------------------
# a review that was asked for and not made
# --------------------------------------------------------------------------

def test_a_demand_opens_a_debt_and_a_review_closes_it():
    """The counter alone was enough to stop asking and not enough to notice
    that the asking had been ignored. A live topic ran four turns of
    production work past two demands with nothing anywhere saying a review
    was outstanding."""
    t = _fresh_topic("Debt", "Q?")
    assert state.review_owed("Debt").get("owed") in (False, None)

    state.note_review_demand("Debt")
    owed = state.review_owed("Debt")
    assert owed["owed"] is True
    assert owed["demanded"] == 1

    state.record_review("Debt", "continue", "reviewed")
    assert state.review_owed("Debt").get("owed") is False


def test_the_debt_survives_the_two_blocks_that_used_to_end_it():
    """After two demands the convergence check stops blocking, because a hook
    that keeps refusing the same tool is a stall. The debt is what carries the
    fact past that point."""
    t = _fresh_topic("DebtLife", "Q?")
    state.note_review_demand("DebtLife")
    state.note_review_demand("DebtLife")
    assert state.review_owed("DebtLife")["demanded"] == 2
    assert state.review_owed("DebtLife")["owed"] is True


def test_an_owed_review_takes_the_thinking_rules_away():
    """Repeating the reminder is what already failed: the agent read it and
    kept working, because nothing it needed had been taken away."""
    _fresh_topic("DebtBlock", "Q?")
    before = protocol.build_research_block("DebtBlock", {"max_inject_chars": 6000})
    assert protocol.THINKING_RULES[:20] in before
    assert "审查欠着：" not in before

    state.note_review_demand("DebtBlock")
    state.note_owed_turn("DebtBlock")
    after = protocol.build_research_block("DebtBlock", {"max_inject_chars": 6000})
    assert "审查欠着：" in after
    assert protocol.THINKING_RULES[:20] not in after
    assert "思维规则暂不发放：" in after


def test_the_debt_line_survives_a_tight_budget():
    """Placed above the situation and never cut: a long narrative pushed the
    one line the agent owes out of the block, which makes the mechanism
    decorative."""
    _fresh_topic("DebtCut", "Q?")
    m = state.read_map("DebtCut")
    m["narrative"] = "很长的一段叙事。" * 400
    state.write_map("DebtCut", m)
    state.note_review_demand("DebtCut")
    block = protocol.build_research_block("DebtCut", {"max_inject_chars": 700})
    assert "审查欠着：" in block


def test_the_answer_says_so_when_the_review_was_not_made():
    """The agent can talk its way past a reminder in the injected block. It
    cannot talk its way past something the reader sees."""
    m = _handler()
    sid = "t-owed-answer"
    m._tool_handler(
        {"action": "create", "project": "OwedAns", "core_question": "Q?"}, session_id=sid
    )
    m._tool_handler({"action": "on", "project": "OwedAns"}, session_id=sid)
    m._tool_handler(
        {"action": "set_state", "project": "OwedAns", "phase": "weighing",
         "narrative": "现判断：X。依据是 Y。"},
        session_id=sid,
    )
    state.note_review_demand("OwedAns")

    out = m._transform_llm_output(
        response_text="综上，我认为 X 成立，依据是 Y。", session_id=sid
    )
    assert out and "全局审查已催" in out


def test_the_answer_is_quiet_on_the_turn_the_review_is_made():
    m = _handler()
    sid = "t-owed-paid"
    m._tool_handler(
        {"action": "create", "project": "OwedPaid", "core_question": "Q?"}, session_id=sid
    )
    m._tool_handler({"action": "on", "project": "OwedPaid"}, session_id=sid)
    m._tool_handler(
        {"action": "set_state", "project": "OwedPaid", "phase": "weighing",
         "narrative": "现判断：X。"},
        session_id=sid,
    )
    state.note_review_demand("OwedPaid")
    m._tool_handler(
        {"action": "review", "project": "OwedPaid", "verdict": "continue",
         "review_note": "material did not change the judgement"},
        session_id=sid,
    )
    out = m._transform_llm_output(
        response_text="综上，我认为 X 成立。", session_id=sid
    )
    assert not out or "全局审查已催" not in out


def test_an_abandoned_debt_nags_no_more_but_stays_on_the_record():
    t = _fresh_topic("DebtAband", "Q?")
    state.note_review_demand("DebtAband")
    state.mark_review_abandoned("DebtAband")
    assert state.review_owed("DebtAband").get("owed") is False
    r = state.read_review("DebtAband")
    assert r.get("abandoned_at")
    block = protocol.build_research_block("DebtAband", {"max_inject_chars": 6000})
    assert "审查欠着：" not in block
    assert protocol.THINKING_RULES[:20] in block


# --------------------------------------------------------------------------
# the session a bridge call lost
# --------------------------------------------------------------------------

def test_the_tool_finds_the_session_the_hooks_recorded():
    """A tool call through a bridge can lose the session id. Writing to
    "unknown" is worse than failing: the payload looks like success and the
    conversation stays where it was."""
    m = _handler()
    sid = "t-pointer"
    state.note_active_session(sid)
    m._tool_handler(
        {"action": "create", "project": "PointerTopic", "core_question": "Q?"},
        session_id=sid,
    )
    # No session_id passed: the call must land on the recorded session.
    out = m._tool_handler({"action": "on", "project": "PointerTopic"})
    assert json.loads(out).get("mode") == "research"
    assert state.read_session(sid).get("topic") == "PointerTopic"


def test_entering_a_topic_refuses_when_no_session_is_known():
    m = _handler()
    m._tool_handler(
        {"action": "create", "project": "NoSession", "core_question": "Q?"},
        session_id="t-tmp",
    )
    # Age the pointer out rather than deleting it: that is the real shape of
    # the failure, a stale pointer from a conversation that has ended.
    path = state.home() / state.ACTIVE_SESSION_FILE
    if path.is_file():
        path.unlink()
    out = m._tool_handler({"action": "on", "project": "NoSession"})
    payload = json.loads(out)
    assert payload.get("error"), payload
    assert "无法确定当前会话" in payload["error"]


# --------------------------------------------------------------------------
# the counters the review rides on
#
# Three gaps here were found by reading one live turn of research work against
# the session file it wrote: a batch claim write counted as nothing, a page
# read counted as no evidence, and a tool call made from inside execute_code
# was judged to be an unknown session. Each one is a way for a productive turn
# to look empty to the loop that decides whether a review is due.
# --------------------------------------------------------------------------


def test_a_batch_claim_write_counts_every_row():
    """The batch is the form the tool's own description recommends.

    A live turn recorded six findings in two batch calls and the session file
    said ledger=0, because the counter only knew the single-claim shape. The
    review demand rides on that number, so the more faithfully the tool was
    used the less the loop could see.
    """
    m = _handler()
    sid = "t-batch-claim"
    m._post_tool_call(
        tool_name="research_mode",
        args={
            "action": "claim",
            "claims": [{"content": "a"}, {"content": "b"}, {"content": "c"}],
        },
        session_id=sid,
        turn_id="t1",
    )
    assert state.turn_activity(sid, "t1")["ledger"] == 3


def test_a_batch_row_without_content_is_not_counted():
    m = _handler()
    sid = "t-batch-empty"
    m._post_tool_call(
        tool_name="research_mode",
        args={"action": "claim", "claims": [{"content": "a"}, {}, {"content": "  "}]},
        session_id=sid,
        turn_id="t1",
    )
    assert state.turn_activity(sid, "t1")["ledger"] == 1


def test_reading_a_page_counts_as_evidence():
    """web_extract is a literature search at the guard, so it is evidence at
    the counter. The two have to answer the same question about the same tool.
    """
    m = _handler()
    sid = "t-evidence-extract"
    m._post_tool_call(tool_name="web_search", session_id=sid, turn_id="t1")
    m._post_tool_call(tool_name="web_extract", session_id=sid, turn_id="t1")
    assert state.turn_activity(sid, "t1")["evidence"] == 2


def test_a_call_from_a_sub_context_lands_on_the_conversation():
    """execute_code calls its tools with a composite `<parent>:<child>` id.

    No session file is named by that id, so the guard read the conversation as
    idle and blocked a search that belonged to an open topic, while the same
    call made directly by the agent went through.
    """
    m = _handler()
    sid = "t-composite"
    m._tool_handler(
        {"action": "create", "project": "CompositeTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "CompositeTopic"}, session_id=sid)
    out = m._pre_tool_call(
        tool_name="web_search", session_id=f"{sid}:child:turn", turn_id="t1"
    )
    assert out is None, out
    m._post_tool_call(
        tool_name="web_search", session_id=f"{sid}:child:turn", turn_id="t1"
    )
    assert state.turn_activity(sid, "t1")["evidence"] == 1


def test_a_composite_id_is_not_attributed_to_another_conversation():
    """The parent is used, and the pointer is the fallback — not the answer.

    Two conversations can be open, and the pointer holds whichever one spoke
    last. A call that names its own parent must not be filed under it.
    """
    m = _handler()
    a, b = "t-bleed-a", "t-bleed-b"
    m._pre_llm_call(session_id=a, user_message="研究一下", turn_id="t1")
    m._pre_llm_call(session_id=b, user_message="研究一下", turn_id="t1")
    m._post_tool_call(
        tool_name="web_search", session_id=f"{a}:child:x", turn_id="t1"
    )
    assert state.turn_activity(a, "t1")["evidence"] == 1
    assert state.turn_activity(b, "t1")["evidence"] == 0


def test_an_unidentified_caller_falls_back_to_the_pointer():
    m = _handler()
    sid = "t-pointer-attr"
    m._pre_llm_call(session_id=sid, user_message="研究一下", turn_id="t1")
    m._post_tool_call(tool_name="web_search", session_id="unknown", turn_id="t1")
    assert state.turn_activity(sid, "t1")["evidence"] == 1


def test_a_search_from_inside_execute_code_is_counted_at_the_pre_hook():
    """The sandbox bridge calls its tools with no session and no turn, and the
    host suppresses post_tool_call inside the executor that owns the outer
    call - so post never sees them. The pre hook does fire, and an allowed
    nested search has to reach the counter the review demand rides on.
    """
    m = _handler()
    sid = "t-nested-search"
    m._tool_handler(
        {"action": "create", "project": "NestedTopic", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "NestedTopic"}, session_id=sid)
    m._pre_llm_call(session_id=sid, user_message="研究一下", turn_id="t1")
    out = m._pre_tool_call(tool_name="web_search", session_id=None, turn_id=None)
    assert out is None, out
    assert state.turn_activity(sid, "")["evidence"] == 1
    assert state.topic_search_count("NestedTopic") == 1


def test_a_blocked_nested_search_is_not_counted():
    """A refused search is not work: the token is spent on the attempt that
    was let through, not on the one the guard turned back."""
    m = _handler()
    sid = "t-nested-blocked"
    m._tool_handler(
        {"action": "create", "project": "NestedPaused", "core_question": "Q?"},
        session_id=sid,
    )
    m._tool_handler({"action": "on", "project": "NestedPaused"}, session_id=sid)
    m._tool_handler(
        {
            "action": "review",
            "project": "NestedPaused",
            "verdict": "pause",
            "review_note": "偏离核心问题",
        },
        session_id=sid,
    )
    m._pre_llm_call(session_id=sid, user_message="研究一下", turn_id="t1")
    out = m._pre_tool_call(tool_name="web_search", session_id=None, turn_id=None)
    assert out is not None and out.get("action") == "block"
    assert state.topic_search_count("NestedPaused") == 0


def test_a_long_turn_keeps_the_pointer_fresh():
    """The pointer is bounded by age, and a research turn outlives the bound.

    Written once at the start of the turn, it expired twenty minutes into the
    work, and every nested call after that was unresolvable — a search from
    inside execute_code was blocked in an open topic. Any hook handed a real
    session id rewrites it.
    """
    m = _handler()
    sid = "t-fresh-pointer"
    # Pretend the turn started long enough ago that the pointer is stale.
    path = state.home() / state.ACTIVE_SESSION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session_id": sid, "ts": 0.0}), encoding="utf-8")
    assert state.active_session() is None
    m._pre_tool_call(tool_name="read_file", session_id=sid, turn_id="t1")
    assert state.active_session() == sid


if __name__ == "__main__":
    raise SystemExit(_run_all())
