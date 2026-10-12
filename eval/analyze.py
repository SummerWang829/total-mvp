"""Failure analysis for the training loop: which categories and scenarios are weakest, and what was actually said.

    python -m eval.analyze                       # latest report with saved transcripts
    python -m eval.analyze <run_id> --show 3     # a specific run, 3 conversations in full
Only ever run this on TRAIN reports: looking at holdout conversations to change rules would spoil the holdout (task doc 6.2).
"""
import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from .report import REPORT_DIR

TRANSCRIPT_SUFFIX = "_transcripts.json"
WORST_COUNT = 5


def _latest_run() -> str:
    """The newest run id that has saved transcripts.

    Raises:
        FileNotFoundError: no run with transcripts exists (run the runner with --save-transcripts).
    """
    files = sorted(REPORT_DIR.glob(f"*{TRANSCRIPT_SUFFIX}"))
    if not files:
        raise FileNotFoundError("no saved transcripts: run `python -m eval.runner ... --save-transcripts`")
    return files[-1].name[: -len(TRANSCRIPT_SUFFIX)]


def _load(run_id: str) -> tuple[dict, list[dict]]:
    """The report and the transcripts of one run.

    Raises:
        ValueError: the run is a holdout run (analysing it would spoil the holdout).
    """
    report = json.loads((REPORT_DIR / f"{run_id}.json").read_text(encoding="utf-8"))
    if report["split"] != "train":
        raise ValueError("only train runs may be analysed; the holdout set is for acceptance only")
    return report, json.loads((REPORT_DIR / f"{run_id}{TRANSCRIPT_SUFFIX}").read_text(encoding="utf-8"))


def _coverage_text(coverage: float | None) -> str:
    """A coverage share for printing; "-" for interviews that ended early on purpose."""
    return "-" if coverage is None else f"{coverage:.0%}"


def _badness(session: dict) -> tuple[int, float]:
    """Sort key: scenarios with violations first, then the lowest coverage."""
    return len(session["findings"]) + len(session["expectation_failures"]), -(session["coverage"] if session["coverage"] is not None else 1.0)


def _mean_coverage(rows: list[dict]) -> str:
    """Mean coverage over the completed interviews of a category ("-" if none: stopped / crisis interviews have no coverage)."""
    values = [r["coverage"] for r in rows if r["coverage"] is not None]
    return f"{sum(values) / len(values):.0%}" if values else "-"


def summarize(report: dict, results: list[dict]) -> list[str]:
    """Per-category numbers and the weakest scenarios, as lines of text."""
    category = lambda scenario_id: scenario_id.split("-")[1]  # noqa: E731 — ids look like s001-<category>-<n>
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for session in report["per_session"]:
        by_cat[category(session["scenario_id"])].append(session)
    lines = ["类别        场数  覆盖率  追问命中  重复  兜底  红线问题"]
    for cat, rows in sorted(by_cat.items()):
        followups = sum(r["followups"] for r in rows)
        hits = sum(r["followup_hits"] for r in rows)
        lines.append(f"{cat:10s} {len(rows):4d}  {_mean_coverage(rows):>6}  {hits / followups if followups else 0:7.0%}  "
                     f"{sum(r['repeats'] for r in rows):4d}  {sum(r['fallbacks'] for r in rows):4d}  "
                     f"{sum(len(r['findings']) + len(r['expectation_failures']) for r in rows):4d}")
    weakest = sorted(report["per_session"], key=_badness, reverse=True)
    lines += ["", "最需要看的场景："]
    for s in weakest[:WORST_COUNT]:
        lines.append(f"- {s['scenario_id']} 覆盖 {_coverage_text(s['coverage'])} 重复 {s['repeats']} 兜底 {s['fallbacks']} 违规 {len(s['findings'])} 预期未满足 {len(s['expectation_failures'])}")
    return lines


def _percentile(sorted_values: list[float], share: float) -> float:
    """Nearest-rank percentile of an already sorted list."""
    return sorted_values[max(0, math.ceil(share * len(sorted_values)) - 1)]


def latency_lines(results: list[dict]) -> list[str]:
    """Where the seconds go: all turns, and turns grouped by how many drafts the red-line gate rejected first."""
    turns = [t for r in results for t in r["turns"] if t["operator"]]
    if not turns:
        return []
    groups: dict[int, list[float]] = defaultdict(list)
    for t in turns:
        groups[len(t["rejected"])].append(t["latency_s"])
    everything = sorted(t["latency_s"] for t in turns)
    lines = ["", f"延迟（秒）：共 {len(turns)} 轮，中位数 {statistics.median(everything):.1f}，p95 {_percentile(everything, 0.95):.1f}，最大 {everything[-1]:.1f}"]
    for rewrites in sorted(groups):
        values = sorted(groups[rewrites])
        lines.append(f"  被拦下重写 {rewrites} 次：{len(values)} 轮，中位数 {statistics.median(values):.1f}，p95 {_percentile(values, 0.95):.1f}")
    return lines


def show_conversation(result: dict, limit_turns: int = 14) -> list[str]:
    """One conversation as readable lines (the first `limit_turns` exchanges)."""
    lines = [f"=== {result['scenario_id']} ({result['language']}) 结束：{result['end_reason']} 覆盖 {len(result['covered'])}/{len(result['must_cover'])} ===",
             f"AI : {result['intro'][:80]}…"]
    for t in result["turns"][:limit_turns]:
        flags = ("兜底 " if t["used_fallback"] else "") + (f"拦{len(t['rejected'])}次 " if t["rejected"] else "")
        lines += [f"人 : {t['participant']}", f"AI : {t['ai']}  [{t['operator']} {flags}{t['latency_s']:.1f}s]"]
    return lines


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Analyse a train run of the AI moderator")
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--show", type=int, default=0, help="print this many weakest conversations in full")
    parser.add_argument("--scenario", help="print one conversation by scenario id")
    args = parser.parse_args(argv)
    run_id = args.run_id or _latest_run()
    report, results = _load(run_id)
    print(f"运行：{run_id}\n")
    print("\n".join(summarize(report, results) + latency_lines(results)))
    by_id = {r["scenario_id"]: r for r in results}
    wanted = [args.scenario] if args.scenario else [s["scenario_id"] for s in sorted(
        report["per_session"], key=_badness, reverse=True)[: args.show]]
    for scenario_id in wanted:
        print("\n" + "\n".join(show_conversation(by_id[scenario_id])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
