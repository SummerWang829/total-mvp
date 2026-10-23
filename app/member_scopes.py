"""Which research stages a project member takes part in (深访, 座谈会, 定量, 舆情, 异步 AI 研究): the choices come from the project's plan, a person can have several,
and the default follows their role (the same ticks the plan wizard gives when it adds a person)."""
import json

from sqlalchemy.orm import Session

from . import methods as methods_mod
from . import plans
from .models import Project, ProjectPlan, User

LABELS = {"depth": "深访", "group": "座谈会", "quant": "定量", "listening": "舆情", "async": "异步 AI 研究"}
ORDER = ("depth", "group", "quant", "listening", "async")


def stage_options(db: Session, project: Project) -> list[dict]:
    """The stages this project has, in its research order, each with the sub-projects (countries) that run it: [{"key", "label", "where": [names]}]."""
    plan = db.get(ProjectPlan, project.id)
    where: dict[str, list[str]] = {}
    order: list[str] = []
    if plan is not None and plan.version > 0:
        cfg = plans.validate(json.loads(plans.live_json(plan) or "{}"))
        for s in cfg.subs:
            for k in plans.DESIGN_STAGES[s.design][1]:
                where.setdefault(k, []).append(s.name)
                if k not in order:
                    order.append(k)
            for flag, k in ((s.listening, "listening"), (s.async_ai, "async")):
                if flag:
                    where.setdefault(k, []).append(s.name)
        if "async" not in where and "async" in methods_mod.of(project):
            where["async"] = []
        order += [k for k in ("listening", "async") if k in where and k not in order]
    else:
        order = [k for k in ORDER if k in methods_mod.of(project) and k != "async"] + (["async"] if "async" in methods_mod.of(project) else [])
    return [{"key": k, "label": LABELS[k], "where": where.get(k, [])} for k in order]


def default_scopes(user: User, options: list[dict]) -> list[str]:
    """The stages a person gets when added: by role (a project manager gets none, to be ticked by hand), limited to the stages the project has."""
    have = {o["key"] for o in options}
    kind = user.research_kind or "all"
    if user.role in ("admin", "viewer", "support"):
        want = set(ORDER)
    elif user.role == "researcher":
        want = {"quant", "listening"} if kind == "quant" else {"depth", "group", "listening", "async"} if kind == "qual" else set(ORDER)
    elif user.role in ("moderator", "observer"):
        want = {"depth", "group"}
    else:
        want = set()
    return [k for k in ORDER if k in want and k in have]


def clean(raw: list, options: list[dict]) -> list[str]:
    """The chosen stages in the standard order; only stages the project has. Raises ValueError for one it does not."""
    have = {o["key"] for o in options}
    chosen = {str(x) for x in raw or []}
    if chosen - have:
        raise ValueError("项目里没有这个研究阶段")
    return [k for k in ORDER if k in chosen]
