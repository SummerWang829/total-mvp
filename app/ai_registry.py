"""Every place the platform calls an AI model, in one list: which page it serves, which prompt it uses, and where in the code the call is made.

The AI console (管理员 → AI 后台) reads this list; the call log (app/ai_calls.py) finds a call's feature by the (module, function) that made it.
Adding a new AI feature = one line here, otherwise its calls are counted under "other".
"""
from dataclasses import dataclass

BUILT_IN = "内置"
MODERATOR_PROMPTS = "@moderator"  # the AI moderator's four prompts are versioned files (app/moderator/prompts): the version in use is read when asked
OTHER_KEY = "other"


@dataclass(frozen=True)
class Feature:
    key: str
    domain: str
    name: str
    page: str
    sites: tuple[tuple[str, str], ...]  # (module, function) that call the model; empty = does not go through the chat model
    prompt: str = BUILT_IN  # BUILT_IN, MODERATOR_PROMPTS, or "" when the feature has no prompt (speech recognition)


FEATURES: tuple[Feature, ...] = (
    Feature("moderator.analyze", "AI 主持人", "对话分析 analyze", "房间 · AI 主持", (("app.moderator.model_calls", "analyze"),), MODERATOR_PROMPTS),
    Feature("moderator.speak", "AI 主持人", "发言 speak", "房间 · AI 主持", (("app.moderator.model_calls", "draft"),), MODERATOR_PROMPTS),
    Feature("moderator.remember", "AI 主持人", "记忆 remember", "房间 · AI 主持", (("app.moderator.model_calls", "remember"),), MODERATOR_PROMPTS),
    Feature("moderator.summarize", "AI 主持人", "小结 summarize", "房间 · AI 主持", (("app.moderator.model_calls", "summarize"),), MODERATOR_PROMPTS),
    Feature("outline.translate", "访谈页", "大纲翻译", "访谈 · 版本同步", (("app.outline_translate", "translate"),)),
    Feature("study.draft", "访谈页", "文字访谈大纲起草", "异步 AI 研究", (("app.study_draft", "draft_guide"),)),
    Feature("recruit.translate", "招募问卷", "问卷翻译", "甄别设定", (("app.recruit_translate", "translate"),)),
    Feature("recruit.insights", "招募问卷", "人群画像总结", "名单看板", (("app.recruit_insights", "summarize"),)),
    Feature("plan.suggest", "立项向导", "研究背景建议", "立项第 1 步后台", (("app.plan_suggest", "suggest"),)),
    Feature("insight.chat", "洞察", "研究助手对话", "洞察与片段 · 对话", (("app.insight_chat", "reply"),)),
    Feature("answers.tag", "洞察", "整理回答：打标签", "受访者数据", (("app.interview_answers", "add_themes"),)),
    Feature("answers.match", "洞察", "整理回答：对题", "受访者数据", (("app.interview_answers", "model_match"),)),
    Feature("interview.qa_group", "洞察", "录像切片：字幕按题归类", "受访者详情 · 录像", (("app.interview_detail_ai", "assign"),)),
    Feature("interview.translate", "洞察", "逐句自动翻译", "受访者详情 · 三语切换", (("app.interview_detail_ai", "translate"),)),
    Feature("interview.summary", "洞察", "单受访者要点总结", "受访者详情 · 要点总结", (("app.interview_detail_ai", "summarize"),)),
    Feature("interview.profile_tags", "洞察", "整体画像标签", "受访者详情 · 整体标签", (("app.interview_detail_ai", "profile_tags"),)),
    Feature("interview.quality", "洞察", "六维回答质量评分", "受访者详情 · 访问信息", (("app.interview_detail_ai", "quality"),)),
    Feature("interview.transcript_fix", "洞察", "转写纠错建议", "受访者详情 · 校对", (("app.interview_detail_ai", "fix_text"),)),
    Feature("interview.export", "洞察", "导出摘要与脱敏", "受访者详情 · 下载", (("app.interview_detail_ai", "export_brief"),)),
    Feature("report.pick", "洞察", "报告：挑对比", "报告", (("app.report_analysis", "pick"),)),
    Feature("report.note", "洞察", "报告：解读", "报告", (("app.report_analysis", "write_notes"),)),
    Feature("creation.chat", "创建访谈", "阶段式对话", "创建访谈 · 左侧对话", (("app.creation.engine", "converse"),)),
    Feature("creation.outline_spec", "创建访谈", "大纲设计规范", "创建访谈 · 大纲阶段", ()),
    Feature("creation.recruit_spec", "创建访谈", "招募筛选规范", "创建访谈 · 甄别阶段", ()),
    Feature("asr", "语音", "实时字幕 / 转写", "房间字幕、转写", (), ""),
)

_BY_SITE = {site: f.key for f in FEATURES for site in f.sites}
_BY_KEY = {f.key: f for f in FEATURES}


def feature_for_site(module: str, function: str) -> str | None:
    """The feature key for a call made from this module and function, or None if it is not in the list."""
    return _BY_SITE.get((module, function))


def feature_by_key(key: str) -> Feature | None:
    return _BY_KEY.get(key)


def prompt_version(feature: Feature) -> str:
    """The prompt version this feature uses now: the moderator's active version, "代码内置" for prompts written in the code, "" for none."""
    if feature.prompt == MODERATOR_PROMPTS:
        from .moderator.prompts import ACTIVE_PROMPT_VERSION
        return ACTIVE_PROMPT_VERSION
    return feature.prompt
