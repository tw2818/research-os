"""Intent classification: is this turn research, or a chore?

The hook uses this to decide whether to inject the research protocol. It is
deliberately conservative: a false positive (research protocol injected into a
"convert this pdf" request) is far more annoying than a false negative, so
anything unclear resolves to "not research".
"""

from __future__ import annotations

import re
from typing import Any

# Verbs/nouns that mark a genuine research question.
RESEARCH_MARKERS = (
    # question-shaped research asks
    "为什么", "为何", "怎么会", "有什么证据", "证据支持", "支持吗", "有没有研究",
    "文献", "论文", "研究", "假设", "机制", "解释", "理论", "模型", "假说",
    "综述", "证据等级", "meta分析", "系统综述", "随机对照", "cohort", "队列",
    "p值", "效应量", "显著", "样本量", "功效", "统计", "偏倚", "混杂",
    "prevalence", "incidence", "mechanism", "hypothesis", "evidence", "literature",
    "etiology", "pathophysiology", "meta-analysis", "cohort study", "randomized",
    # research actions
    "查一下", "查查", "找文献", "找论文", "核实", "验证", "证伪", "排除",
    "为什么认为", "证据链", "推论", "推理", "关联", "因果",
    # The fields the list above came from are not the only fields. A
    # question about a text, a proof, a design or an argument is
    # research too, and the list used to have no word for any of them -
    # so "which proof strategy holds" was not a research request, and
    # the agent answered it without any of the research discipline.
    "论证", "文本", "版本", "校勘", "注释", "引文", "原文", "手稿", "词义",
    "证明", "定理", "引理", "反例", "公理", "命题", "论证",
    "策略", "选型", "权衡", "取舍", "折中", "设计", "架构", "取舍",
    "解读", "释义", "考订", "真伪", "年代", "作者",
    "argument", "textual", "reading", "proof", "theorem", "counterexample",
    "edition", "manuscript", "annotation", "interpret", "hermeneut",
    "trading off", "trade-off", "design choice", "which one",
)

# Markers for a chore: an operation, not an inquiry.
CHORE_MARKERS = (
    "转成", "转换", "格式化", "导入", "导出", "重命名", "移动", "复制", "删除文件",
    "安装", "卸载", "升级", "下载", "上传", "压缩", "解压", "打包",
    "装一下", "装个", "整理一下", "整理", "归档", "归类", "分类一下", "排一下",
    "改一下", "改改", "修一下", "修修", "跑一下", "跑一遍", "执行", "启动", "重启",
    "配置", "设置一下", "打开", "关闭", "截图", "打印",
    # The verbs an instruction actually uses. "删除文件" was in the list
    # but "删掉这个文件" was not, and the difference is not cosmetic:
    # a chore is what takes a session out of research mode, so a delete
    # that was not recognised left the agent in research mode with the
    # research block still riding along.
    "删掉", "删除", "移除", "去掉", "删了", "移走", "复制一份", "另存",
    "convert", "rename", "install", "uninstall", "download", "upload", "format",
    "run ", "execute", "start ", "restart", "config", "fix the", "debug the",
    "delete", "remove", "move ", "copy ", "save as", "rename ",
)

# A research question that is *about* a chore stays a chore.
CHORE_OBJECT = (
    "这个pdf", "这个文件", "这个脚本", "这个仓库", "这张图", "这个表格",
    "这个项目", "这个目录", "这段代码", "这份文档",
)

# The shapes of a question. "还是" is in the list because "is it A or B"
# is a question in every field, and the old list only recognised the
# shapes that end in a question mark or a modal particle - so "是自然
# 规律还是事物本性" was not a question at all.
_QUESTION_MARK = re.compile(
    r"[?？]|吗|呢|如何|怎么|是不是|有没有|该不该|行不行|还是|两者|哪个|"
    r"which|what|why|how|whether"
)

# The user asking to leave research mode. Hermes intercepts slash commands it
# does not know ("/research off" comes back as "Unknown command"), so the
# switch has to work in plain language. These phrasings are the ones a person
# actually uses.
_EXIT_PHRASES = (
    "退出科研模式", "退出研究模式", "退出课题", "关掉科研模式", "关闭科研模式",
    "结束科研模式", "结束研究模式", "结束这个课题", "结束课题",
    "退出研究", "退出科研", "先退出", "别做研究了", "不研究了",
    "leave research mode", "exit research mode", "stop research mode",
    "turn off research mode", "leave the topic", "exit the topic",
)

# A phrase that only reads as an exit in context. "结束" alone could mean
# "finish this analysis", so it needs a research-mode noun nearby.
_EXIT_CONTEXT = re.compile(
    r"(退出|结束|关闭|关掉|停止|不做|别做|收工|收一下).{0,6}"
    r"(科研模式|研究模式|课题|研究|科研|topic|research mode)"
    r"|(科研模式|研究模式|课题|research mode).{0,6}(退出|结束|关闭|关掉|停止)",
    re.IGNORECASE,
)


def is_exit_request(message: str) -> bool:
    """True when the user is asking to leave research mode."""
    text = (message or "").strip()
    if not text:
        return False
    low = text.lower()
    for phrase in _EXIT_PHRASES:
        if phrase in low:
            return True
    return bool(_EXIT_CONTEXT.search(text))

# System-generated turns are not user turns. Hermes injects these as user
# messages (background-process completions, async batch notices, skill-index
# updates, memory recalls). Their bodies quote the original command, so they
# carry whatever research words that command contained — classifying on the
# text alone would open research mode on a system notice. The prefixes match
# Hermes' own synthetic-prompt pattern (hermes_state_timeline._SYNTHETIC_PROMPT).
_SYNTHETIC_PREFIX = re.compile(
    r"^\s*(?:\[IMPORTANT: Background process |"
    r"\[ASYNC (?:DELEGATION )?(?:BATCH )?COMPLETE\b|"
    r"A background fan-out of \d+ subagent\(s\) you dispatched earlier has finished\.|"
    r"A background subagent you dispatched earlier has finished\.|"
    r"\[INTERNAL NOTIFICATION|"
    r"\[System: |"
    r"\[Skills?:|"
    r"\[Memory|"
    r"<memory-context>|"
    r"\[Research OS)",
    re.IGNORECASE,
)


def is_synthetic(message: str) -> bool:
    """True for a turn the system generated rather than the user typed."""
    return bool(_SYNTHETIC_PREFIX.match((message or "").lstrip()))


def classify(
    message: str,
    session_state: dict[str, Any] | None = None,
) -> str:
    """Return 'research' | 'chore' | 'idle'.

    session_state carries the current mode; a session already in research mode
    stays in research mode unless the message is clearly a chore.
    """
    text = (message or "").strip()
    if not text:
        return "idle"

    # A system-generated turn is never a research request, no matter what its
    # body says — its body quotes the original command.
    if is_synthetic(text):
        return "idle"

    low = text.lower()
    state = session_state or {}
    current = state.get("mode", "idle")

    # 1. Explicit command wins.
    if re.match(r"^\s*/research\b", low) or re.search(r"^\s*/research\s", low):
        return "research"
    if re.match(r"^\s*/chore\b", low) or re.match(r"^\s*/research\s+off\b", low):
        return "chore"

    has_research = any(m in low for m in RESEARCH_MARKERS)
    has_chore = any(m in low for m in CHORE_MARKERS)
    is_question = bool(_QUESTION_MARK.search(text))
    chore_object = any(o in low for o in CHORE_OBJECT)

    # 2. Clearly a chore about an object -> chore, even if research words appear.
    if has_chore and chore_object and not is_question:
        return "chore"

    # 3. In research mode: only leave on a clear chore.
    if current == "research":
        if has_chore and not has_research:
            return "chore"
        return "research"

    # 4. Idle: enter research only on a clear research question.
    if has_research and (is_question or has_chore):
        return "research"
    if has_research and len(text) > 40 and not has_chore:
        # A long, research-flavoured request with no operation in it.
        return "research"

    return "chore" if has_chore else "idle"
