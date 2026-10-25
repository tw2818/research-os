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
    assert "哪个解释现在更划算" in ctx
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
         "title": "缺损来自环路本身", "separator": "控制情绪后的执行功能差异是否仍存在"},
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
        {"action": "question", "project": "QTopic", "question": "缓解的定义怎么统一"},
        session_id=sid,
    ))
    assert "open_question" in out
    assert state.read_map("QTopic")["open_questions"] == ["缓解的定义怎么统一"]


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
            "另外 Smith 2019 的队列研究表明工作记忆容量在抑郁中显著下降。"
        ),
    )
    assert out is not None
    assert "没给判断" in out
    # The answer is not rewritten and the findings are not listed back.
    assert "Smith 2019" not in out.split("---")[-1]


def test_transform_llm_output_quiet_when_there_is_a_position():
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
    for phase in ("scoping", "evidence", "weighing", "closing", "writing"):
        m = state.read_map("T-phase")
        m["phase"] = phase
        state.write_map("T-phase", m)
        block = protocol.build_research_block("T-phase", {"max_inject_chars": 2000})
        assert protocol.PHASES[phase]["thinking"] in block, phase
        seen.add(protocol.PHASES[phase]["thinking"])
    assert len(seen) == 5


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
    m["phase"] = "weighing"
    state.write_map("T-shape", m)
    block = protocol.build_research_block("T-shape", {"max_inject_chars": 2000})
    assert "哪个解释现在更划算" in block
    m["phase"] = "closing"
    state.write_map("T-shape", m)
    block = protocol.build_research_block("T-shape", {"max_inject_chars": 2000})
    assert "给出结论、它的强度" in block


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
    assert "scoping" in str(out), out
    assert state.read_goal("T-stand") == "把边界划清"
    assert state.read_phase("T-stand") == "scoping"
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
    assert audit.has_position("这可能说明炎症水平在缓解期仍然偏高，需要更多研究。") is False
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
        "Smith 2019 的队列研究表明工作记忆容量在抑郁中显著下降。",
        "这两项研究都测了工作记忆容量。",
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
