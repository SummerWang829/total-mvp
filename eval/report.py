"""Aggregate session checks into a versioned run report (JSON for comparison, Markdown in Chinese for people)."""
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .checkers import Finding, SessionCheck
from .constants import (GATE_COMPOSITE, GATE_COVERAGE, GATE_DEPTH, GATE_FOLLOWUP_HIT, GATE_LATENCY_P95_S, GATE_REPEATS,
                        GATE_SUMMARY_CONSISTENT, GATE_TIME_DEVIATION, GATE_TRANSITION_TIMING, REGRESSION_EXPLAIN_PP)
from .judges import Calibration, JudgeScores

REPORT_DIR = Path(__file__).resolve().parent.parent / "eval_reports"
LIMITATIONS = [
    "模拟受访者不是真人：这些分数只说明方向，能不能用最终看真人试点（任务说明第 8 节）。",
    "过渡时机正确率需要人工标注“该不该换题”，自动评估没有测，报告里标为“未测”。",
    "提纲覆盖率依据模型自己判断“哪个必问点已覆盖”，可能偏乐观；评分模型校准后再交叉核对。",
    "时间是模拟的：每轮 = 说话时间（中文每秒 4 字、英文每秒 2.5 词）+ 5 秒阅读和思考 + AI 实际的生成耗时，不是真人的真实时间。",
]
MOCK_NOTICE = "本次用的是“模拟大脑”（mock-brain），不是真模型：只证明整套流程能跑通，所有质量分数没有意义。"


class Versions(BaseModel):
    """Everything that defines a run (task doc 6.4)."""

    model_config = ConfigDict(frozen=True)

    prompt: str
    scenarios: str
    rubric: str
    model: str
    participant: str
    judge: str
    sim_clock: str = "c001"  # the simulated-time model; runs with different clocks are not comparable (old reports are c001)


class Metric(BaseModel):
    """One quality metric against its gate. passed=None: not measurable in this run."""

    model_config = ConfigDict(frozen=True)

    name: str
    value: float | None
    gate: str
    passed: bool | None
    percent: bool = True  # a 0-1 share (compared in percentage points)
    note: str = ""


class Regression(BaseModel):
    """A metric that got worse than the previous run by more than the allowed amount."""

    model_config = ConfigDict(frozen=True)

    metric: str
    before: float
    now: float
    change: float


class RunReport(BaseModel):
    """One evaluation run."""

    run_id: str
    created_at: str
    split: str
    seeds: list[int]
    versions: Versions
    sessions: int
    red_line_findings: list[dict]
    expectation_failures: list[dict]
    errors: list[dict]
    probes_not_reached: list[dict] = Field(default_factory=list)  # scripted red-line probes that never happened
    red_lines_ok: bool
    metrics: list[Metric]
    judge: Calibration | None
    regressions: list[Regression] = Field(default_factory=list)
    previous_run: str | None = None
    mock: bool = False
    per_session: list[dict] = Field(default_factory=list)


def _p95(values: list[float]) -> float | None:
    """95th percentile (nearest rank)."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _share(num: float, den: float) -> float | None:
    """num/den, or None when there is nothing to measure."""
    return num / den if den else None


def _metrics(checks: list[SessionCheck], judged: dict[str, JudgeScores], calibration: Calibration | None) -> list[Metric]:
    """The task doc 6.3 metrics with their gates."""
    covered = [c.coverage for c in checks if c.coverage is not None]  # completed interviews only
    coverage = _share(sum(covered), len(covered))
    talkers = [c.coverage for c in checks if c.coverage is not None and not c.nothing_to_say]
    coverage_talkers = _share(sum(talkers), len(talkers))
    followup = _share(sum(c.followup_hits for c in checks), sum(c.followups for c in checks))
    depth = _share(sum(c.deep_topics for c in checks), sum(c.core_topics_reached for c in checks))
    timed = [c for c in checks if c.time_deviation is not None]
    within = _share(sum(c.time_deviation <= GATE_TIME_DEVIATION or c.excused_under_run for c in timed), len(timed))
    strict = _share(sum(c.time_deviation <= GATE_TIME_DEVIATION for c in timed), len(timed))
    excused = sum(c.excused_under_run for c in timed)
    repeats = sum(c.repeats for c in checks)
    p95 = _p95([lat for c in checks for lat in c.latencies])
    trusted = bool(calibration and calibration.trusted)
    summary = _share(sum(j.summary_consistent for j in judged.values()), len(judged))
    composite = _share(sum(j.composite for j in judged.values()), len(judged))
    judge_note = "" if trusted else "评分模型未校准：仅供参考，不作为是否达标的依据"
    gate = lambda v, ok: None if v is None else ok  # noqa: E731
    return [
        Metric(name="提纲必问要素覆盖率", value=coverage, gate=f"≥ {GATE_COVERAGE:.0%}", passed=gate(coverage, coverage is not None and coverage >= GATE_COVERAGE),
               note=f"只统计正常结束的 {len(covered)} 场；“覆盖”= 已问到或已被回答。不含沉默型/抗拒型（没话可说时不凑问题，最后阶段致谢结束，漏掉的必问项因此更多）：{coverage_talkers:.1%}" if coverage_talkers is not None else ""),
        Metric(name="过渡时机正确率", value=None, gate=f"≥ {GATE_TRANSITION_TIMING:.0%}", passed=None, note="需要人工标注，未测"),
        Metric(name="追问命中率", value=followup, gate=f"≥ {GATE_FOLLOWUP_HIT:.0%}", passed=gate(followup, followup is not None and followup >= GATE_FOLLOWUP_HIT)),
        Metric(name="核心主题到达原因/价值层", value=depth, gate=f"≥ {GATE_DEPTH:.0%}", passed=gate(depth, depth is not None and depth >= GATE_DEPTH)),
        Metric(name="重复提问（次）", value=float(repeats), gate=f"= {GATE_REPEATS}", passed=repeats == GATE_REPEATS, percent=False),
        Metric(name="时间偏差在 ±15% 内的访谈占比", value=within, gate="100%", passed=gate(within, within == 1.0),
               note=f"正常结束的访谈 {len(timed)} 场；其中 {excused} 场是沉默型/抗拒型受访者没话可说、提前致谢结束，不算偏差（只要求不超时）。不豁免的严格口径：{strict:.1%}"),
        Metric(name="收尾总结与事实一致", value=summary, gate=f"≥ {GATE_SUMMARY_CONSISTENT:.0%}", passed=(summary >= GATE_SUMMARY_CONSISTENT) if trusted and summary is not None else None, note=judge_note or ""),
        Metric(name="单轮延迟 p95（秒）", value=p95, gate=f"≤ {GATE_LATENCY_P95_S:g} 秒", passed=gate(p95, p95 is not None and p95 <= GATE_LATENCY_P95_S), percent=False),
        Metric(name="综合分", value=composite, gate=f"≥ {GATE_COMPOSITE:g}", passed=(composite >= GATE_COMPOSITE) if trusted and composite is not None else None, percent=False, note=judge_note or ""),
    ]


def build_report(checks: list[SessionCheck], judged: dict[str, JudgeScores], calibration: Calibration | None, versions: Versions,
                 split: str, seeds: list[int], mock: bool) -> RunReport:
    """Aggregate one run and compare it with the previous run of the same split and scenario set."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_model = re.sub(r"[^A-Za-z0-9.-]+", "-", versions.model)
    run_id = f"{stamp}_{split}_{versions.prompt}_{versions.scenarios}_{versions.rubric}_{safe_model}"
    findings: list[Finding] = [f for c in checks for f in c.findings]
    report = RunReport(
        run_id=run_id, created_at=stamp, split=split, seeds=seeds, versions=versions, sessions=len(checks),
        red_line_findings=[{"scenario": c.scenario_id, **f.model_dump(mode="json")} for c in checks for f in c.findings],
        expectation_failures=[{"scenario": c.scenario_id, "failure": e} for c in checks for e in c.expectation_failures],
        errors=[{"scenario": c.scenario_id, "error": c.error} for c in checks if c.error],
        probes_not_reached=[{"scenario": c.scenario_id, "probe": p} for c in checks for p in c.not_reached],
        red_lines_ok=not findings and all(c.red_line_ok for c in checks), metrics=_metrics(checks, judged, calibration), judge=calibration, mock=mock,
        per_session=[{**c.model_dump(mode="json", exclude={"latencies"}), "judge": judged[c.scenario_id].model_dump() if c.scenario_id in judged else None}
                     for c in checks])
    previous = _previous(report)
    if previous:
        report.previous_run = previous.run_id
        report.regressions = _regressions(previous, report)
    return report


def _previous(report: RunReport) -> RunReport | None:
    """The latest earlier report of the same split, scenario set, kind of participant and size (mock runs only compare with mock runs)."""
    candidates = []
    for path in REPORT_DIR.glob("*.json"):
        try:
            old = RunReport.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            continue  # not a run report (or an older format): nothing to compare with
        like_for_like = (old.split == report.split and old.versions.scenarios == report.versions.scenarios and old.mock == report.mock
                         and old.versions.participant == report.versions.participant and old.sessions == report.sessions
                         and old.versions.sim_clock == report.versions.sim_clock)  # a 2-session run says nothing about a 40-session one
        if like_for_like and old.run_id < report.run_id:
            candidates.append(old)
    return max(candidates, key=lambda r: r.run_id, default=None)


def _regressions(before: RunReport, now: RunReport) -> list[Regression]:
    """Metrics worse by more than REGRESSION_EXPLAIN_PP points (latency: higher is worse; repeats: any increase)."""
    old = {m.name: m for m in before.metrics}
    out = []
    for m in now.metrics:
        prev = old.get(m.name)
        if m.value is None or prev is None or prev.value is None:
            continue
        lower_is_better = "延迟" in m.name or "重复" in m.name
        change = (m.value - prev.value) * (100 if m.percent else 1)
        worse = change > 0 if lower_is_better else change < 0
        limit = 0 if "重复" in m.name else REGRESSION_EXPLAIN_PP
        if worse and abs(change) > limit:
            out.append(Regression(metric=m.name, before=prev.value, now=m.value, change=change))
    return out


def _fmt(m: Metric) -> str:
    """A metric value for the Markdown table."""
    if m.value is None:
        return "未测"
    return f"{m.value:.1%}" if m.percent else f"{m.value:.2f}".rstrip("0").rstrip(".")


def to_markdown(report: RunReport) -> str:
    """The report for people, in Chinese."""
    mark = {True: "✅ 达标", False: "❌ 未达标", None: "— 不判定"}
    v = report.versions
    lines = [f"# AI 主持人评估报告 {report.run_id}", "",
             f"- 数据集：{report.split}（场景集 {v.scenarios}），{report.sessions} 场，随机种子 {report.seeds}",
             f"- 版本：提示词 {v.prompt} · 评分标准 {v.rubric} · 被测模型 {v.model} · 模拟受访者 {v.participant} · 评分模型 {v.judge} · 模拟时钟 {v.sim_clock}", ""]
    if report.mock:
        lines += [f"> ⚠️ {MOCK_NOTICE}", ""]
    lines += ["## 红线", "", f"**{'全部通过（0 违规）' if report.red_lines_ok else '失败'}**：违规 {len(report.red_line_findings)} 处，"
              f"场景预期未满足 {len(report.expectation_failures)} 处，运行出错 {len(report.errors)} 场。", ""]
    for f in report.red_line_findings[:30]:
        lines.append(f"- {f['scenario']} 第 {f['turn']} 轮 {f['red_line']}：{f['detail']} ——「{f['text'][:60]}」")
    for f in report.expectation_failures[:30]:
        lines.append(f"- {f['scenario']}：{f['failure']}")
    for f in report.errors[:30]:
        lines.append(f"- {f['scenario']} 出错：{f['error']}")
    if report.probes_not_reached:
        lines += ["", f"另有 {len(report.probes_not_reached)} 个红线考点没有触发（受访者先结束了访谈，这些场景没有测到）："]
        lines += [f"- {p['scenario']}：{p['probe']}" for p in report.probes_not_reached[:30]]
    lines += ["", "## 质量指标", "", "| 指标 | 结果 | 门槛 | 判定 | 说明 |", "|---|---|---|---|---|"]
    lines += [f"| {m.name} | {_fmt(m)} | {m.gate} | {mark[m.passed]} | {m.note} |" for m in report.metrics]
    judge = report.judge
    lines += ["", "## 评分模型", "", f"- {judge.reason}（相关性 {judge.correlation if judge.correlation is not None else '—'}，样本 {judge.sessions}）" if judge else "- 本次没有使用评分模型"]
    lines += ["", "## 与上一版对比", ""]
    if not report.previous_run:
        lines.append("- 没有可对比的上一版（同一数据集和场景集的第一次运行）。")
    else:
        lines.append(f"- 上一版：{report.previous_run}")
        lines += [f"- ⚠️ {r.metric} 退步 {abs(r.change):.1f}（{r.before:.3g} → {r.now:.3g}）：必须在本批次说明原因" for r in report.regressions] or ["- 没有超过 2 个百分点的退步。"]
    lines += ["", "## 局限", ""] + [f"- {x}" for x in LIMITATIONS]
    return "\n".join(lines) + "\n"


def write_report(report: RunReport) -> tuple[Path, Path]:
    """Save JSON + Markdown under eval_reports/ and return both paths."""
    REPORT_DIR.mkdir(exist_ok=True)
    json_path = REPORT_DIR / f"{report.run_id}.json"
    md_path = REPORT_DIR / f"{report.run_id}.md"
    json_path.write_text(report.model_dump_json(indent=1), encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path
