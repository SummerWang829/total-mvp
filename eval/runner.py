"""One command for the whole evaluation (task doc 9, Sprint 3):

    python -m eval.runner                      # train split, model from .env (mock brain if no model is set)
    python -m eval.runner --split holdout      # acceptance only: never change rules because of it
    python -m eval.runner --brain mock --participant rule --limit 20   # offline pipeline check

Writes eval_reports/<run>.json and .md. Exit code 1 when any red line failed (so CI stops on it).
"""
import argparse
import json
import logging
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from app.config import settings
from app.moderator.knowledge import InterviewGuide
from app.moderator.prompts import CURRENT_PROMPT_VERSION
from app.moderator.providers import ModeratorChatProvider, OpenAICompatModeratorChat

from .checkers import SessionCheck, check_session
from .config import judge_model, participant_model
from .constants import RUBRIC_VERSION, SCENARIO_SET_VERSION, SIM_CLOCK_VERSION, Split
from .judges import Calibration, Judge, JudgeScores, ModelJudge, calibrate
from .mock_brain import MockBrain
from .models import Persona, Scenario, load_guide, load_personas
from .report import RunReport, Versions, build_report, write_report
from .scenarios.generate import load_scenarios
from .session import SessionResult, run_session
from .simulator import ModelParticipant, Participant, RuleBasedParticipant

log = logging.getLogger("eval")
MOCK = "mock"


def _brain(kind: str) -> ModeratorChatProvider:
    """The moderator's model: the scripted mock brain, or the model configured in .env."""
    return MockBrain() if kind == MOCK else OpenAICompatModeratorChat(settings)


def _participant(kind: str, persona: Persona, scenario: Scenario, guide: InterviewGuide) -> Participant:
    """A rule-based or model-played simulated participant."""
    if kind == "rule":
        return RuleBasedParticipant(persona, scenario.intensity, guide, scenario.seed)
    return ModelParticipant(OpenAICompatModeratorChat(participant_model().as_settings()), persona, scenario.intensity, guide, scenario.seed)


def _guide_for(scenario: Scenario) -> InterviewGuide:
    """The scenario's guide, shortened to the scenario's duration when it has one."""
    guide = load_guide(scenario.guide_id)
    return guide.model_copy(update={"duration_min": scenario.duration_min}) if scenario.duration_min else guide


def run_one(scenario: Scenario, brain: str, participant: str, judge: Judge | None) -> tuple[SessionResult, SessionCheck, JudgeScores | None]:
    """Run, check and (optionally) judge one scenario."""
    guide = _guide_for(scenario)
    persona = load_personas()[scenario.persona]
    result = run_session(scenario, guide, _brain(brain), _participant(participant, persona, scenario, guide))
    check = check_session(result, scenario, guide)
    return result, check, judge.score(result, guide) if judge else None


def _pick(scenarios: list[Scenario], category: str | None, per_category: int | None, limit: int | None) -> list[Scenario]:
    """Select scenarios: one category, the first N of each category (a quick stratified sample), and/or the first N overall."""
    chosen = [s for s in scenarios if not category or s.category.value == category]
    if per_category:
        seen: Counter[str] = Counter()
        sample = []
        for s in chosen:
            if seen[s.category.value] < per_category:
                seen[s.category.value] += 1
                sample.append(s)
        chosen = sample
    return chosen[:limit]


def run(split: Split | None, seeds: list[int], brain: str, participant: str, use_judge: bool, limit: int | None, workers: int,
        category: str | None, per_category: int | None = None, results_out: list[SessionResult] | None = None) -> RunReport:
    """Run the selected scenarios for every seed and build the report.

    Raises:
        FileNotFoundError: the scenario set has not been generated.
    """
    scenarios = _pick(load_scenarios(split), category, per_category, limit)
    jobs = [s.model_copy(update={"seed": s.seed + offset, "id": s.id if len(seeds) == 1 else f"{s.id}@{offset}"}) for offset in seeds for s in scenarios]
    judge = ModelJudge(OpenAICompatModeratorChat(judge_model().as_settings())) if use_judge else None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(lambda s: run_one(s, brain, participant, judge), jobs))
    if results_out is not None:
        results_out.extend(r for r, _, _ in outcomes)  # for --save-transcripts: the failure analysis needs the actual conversations
    checks = [c for _, c, _ in outcomes]
    judged = {c.scenario_id: j for _, c, j in outcomes if j}
    calibration: Calibration | None = calibrate({k: v.composite for k, v in judged.items()}) if judge else None
    model_name = MockBrain.name if brain == MOCK else f"{settings.moderator_model}"
    versions = Versions(prompt=CURRENT_PROMPT_VERSION, scenarios=SCENARIO_SET_VERSION, rubric=RUBRIC_VERSION, model=model_name,
                        participant="rule-based" if participant == "rule" else participant_model().model,
                        judge=judge_model().model if judge else "none", sim_clock=SIM_CLOCK_VERSION)
    return build_report(checks, judged, calibration, versions, split.value if split else "all", seeds, mock=brain == MOCK)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    p = argparse.ArgumentParser(description="Evaluate the AI moderator on the scenario set")
    p.add_argument("--split", choices=[s.value for s in Split] + ["all"], default=Split.TRAIN.value)
    p.add_argument("--seed", type=int, action="append", help="repeat for several seeds (stability check uses 3)")
    p.add_argument("--brain", choices=[MOCK, "model"], default=MOCK if settings.moderator_provider == MOCK else "model")
    p.add_argument("--participant", choices=["rule", "model"], default=None)
    p.add_argument("--judge", action="store_true", help="also score with the judge model (EVAL_JUDGE_*)")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--category", default=None)
    p.add_argument("--save-transcripts", action="store_true", help="also write every conversation next to the report (for failure analysis)")
    p.add_argument("--per-category", type=int, default=None, help="quick sample: the first N scenarios of each category")
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args(argv)
    participant = args.participant or ("rule" if args.brain == MOCK else "model")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    split = None if args.split == "all" else Split(args.split)
    results: list[SessionResult] = []
    report = run(split, args.seed or [0], args.brain, participant, args.judge, args.limit, args.workers, args.category, args.per_category, results)
    json_path, md_path = write_report(report)
    if args.save_transcripts:
        json_path.with_name(json_path.stem + "_transcripts.json").write_text(json.dumps([r.model_dump(mode="json") for r in results], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{report.sessions} 场；红线{'全部通过' if report.red_lines_ok else '失败'}；报告：{md_path}")
    return 0 if report.red_lines_ok else 1


if __name__ == "__main__":
    sys.exit(main())
