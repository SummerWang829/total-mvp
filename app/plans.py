"""The project plan (立项向导): what a project is set up to do, checked against the compliance list before it can be published.

A plan belongs to one project and goes draft -> review -> approved -> published (locked). The wizard (app/static/wizard.js) edits the plan's
`config`; this module owns what a valid config looks like, the defaults, and the compliance list that is judged from it (the same list as the
original 星桐 design: 14 requirements, plus 4 more when interviews are used).

Projects that were made before plans existed have no plan; they count as published.
"""
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import methods as methods_mod

STATUSES = ("draft", "review", "approved", "published")
DESIGNS = {"ql": "先定性，后定量", "qt": "先定量，后定性", "sw": "定性、定量、定性"}
TYPES = ("使用习惯与态度", "概念测试", "视频与广告测试", "售后与体验", "产品与包装测试")
SOURCES = ("pool", "rec", "panel")
PLACEHOLDERS = ("purpose", "unsubscribe")
MAX_ROWS = 60
MAX_VALUES = 40
MAX_VARIABLES = 8
RETENTION_DAYS = 180  # fixed by the platform (the consent text says so), shown in the wizard but not chosen there
DEFAULT_TQ = "您好，邀请您参加一项约 8 分钟的调研。目的：{{purpose}}。退订请回复：{{unsubscribe}}。"
DEFAULT_TL = "您好，邀请您参加一场 45 分钟的线上访谈。目的：{{purpose}}。同意录制后可预约时间，退订：{{unsubscribe}}。"
COUNTRY_VAR = "国家"
EXTRA_VARS = {"年龄段": ["18-24", "25-34", "35 岁以上"], "城市线级": ["一线", "二线", "其他"]}
LANG_CHOICES = ("中文", "英语", "马来语", "泰米尔语")


class Sub(BaseModel):
    """One sub-project: a country (or one all-in-one sub-project) with its execution language, responsible person and research order."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=40)
    lang: str = Field(default="英语", max_length=20)
    pm: str = Field(default="", max_length=40)  # a user id, or empty
    design: Literal["ql", "qt", "sw"] = "ql"


class Member(BaseModel):
    """One team member on the plan: which methods they take part in (their access follows their account role when the plan is published)."""

    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1, max_length=40)
    q: bool = False
    i: bool = False
    f: bool = False


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=40)


class Row(BaseModel):
    """One line of a quota table: a value of each chosen variable and its targets."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=40)
    vs: list[str] = Field(default_factory=list, max_length=2)
    t: int = Field(default=0, ge=0, le=1_000_000)  # survey completes
    i: int = Field(default=0, ge=0, le=100_000)  # interviews
    f: int = Field(default=0, ge=0, le=10_000)  # group discussions


class Quota(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dims: list[str] = Field(default_factory=lambda: [COUNTRY_VAR], max_length=2)
    rows: list[Row] = Field(default_factory=list, max_length=MAX_ROWS)


class Counts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: int = Field(default=0, ge=0, le=1_000_000)
    i: int = Field(default=0, ge=0, le=100_000)
    f: int = Field(default=0, ge=0, le=10_000)
    fs: int = Field(default=6, ge=3, le=10)


class IpRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: bool = True
    win: Literal[7, 30, 90] = 30


class Cons(BaseModel):
    """Confirmations a person has to give by hand (the rest of the compliance list is judged by the system)."""

    model_config = ConfigDict(extra="forbid")
    xb: bool = False  # standard contract signed / cross-border assessment done
    minor: bool = False  # the population is limited to 18+ and screened
    rec: bool = False
    recon: bool = False
    mask: bool = False
    ai: bool = False


class PlanConfig(BaseModel):
    """Everything the wizard collects. Unknown keys are refused, so a typo can never silently lose a setting."""

    model_config = ConfigDict(extra="forbid")
    type: str = TYPES[0]
    design: Literal["ql", "qt", "sw"] = "ql"
    methods: list[str] = Field(default_factory=lambda: ["depth"])
    split: Literal["country", "single"] = "country"
    subs: list[Sub] = Field(default_factory=lambda: [Sub(id="s1", name="新加坡", lang="英语")], max_length=30)
    team: list[Member] = Field(default_factory=list, max_length=80)
    pop: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    est: int | None = Field(default=None, ge=0, le=100_000_000)
    n: Counts = Field(default_factory=Counts)
    vars: dict[str, list[Value]] = Field(default_factory=dict)  # variable name -> its values; "国家" follows the sub-projects
    qt: Quota = Field(default_factory=Quota)
    lt: Quota = Field(default_factory=Quota)
    ft: Quota = Field(default_factory=Quota)
    src: Literal["pool", "rec", "panel"] = "pool"
    lead: int = Field(default=24, ge=0, le=720)
    inv: int = Field(default=3, ge=1, le=50)
    gift: int = Field(default=200, ge=0, le=100_000)
    back: int = Field(default=20, ge=0, le=200)
    ip: IpRule = Field(default_factory=IpRule)
    tq: str = Field(default=DEFAULT_TQ, max_length=2000)
    tl: str = Field(default=DEFAULT_TL, max_length=2000)
    langs: dict[str, bool] = Field(default_factory=dict)
    cons: Cons = Field(default_factory=Cons)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in TYPES:
            raise ValueError("unknown research type")
        return v

    @field_validator("methods")
    @classmethod
    def _methods(cls, v: list[str]) -> list[str]:
        try:
            return methods_mod.clean(v)
        except methods_mod.MethodsError as e:
            raise ValueError(str(e)) from e

    @field_validator("langs")
    @classmethod
    def _langs(cls, v: dict[str, bool]) -> dict[str, bool]:
        if len(v) > 20 or any(len(k) > 20 for k in v):
            raise ValueError("too many sending languages")
        return v

    @field_validator("vars")
    @classmethod
    def _vars(cls, v: dict[str, list[Value]]) -> dict[str, list[Value]]:
        if len(v) > MAX_VARIABLES or any(len(k) > 20 or len(vals) > MAX_VALUES for k, vals in v.items()):
            raise ValueError("too many variables or values")
        return v


def default_config(name: str = "") -> dict:
    """A new plan's starting point: one sub-project, the country variable, three empty quota tables."""
    del name
    cfg = PlanConfig()
    cfg.langs = {lang: lang == "英语" for lang in LANG_CHOICES}
    cfg.vars = {COUNTRY_VAR: [Value(id="s1", name="新加坡")], **{k: [Value(id=f"{k}{i}", name=n) for i, n in enumerate(vs)] for k, vs in EXTRA_VARS.items()}}
    return cfg.model_dump()


def validate(raw: dict) -> PlanConfig:
    """Parse and cross-check a config.

    Raises:
        ValueError: a field is out of range, unknown, or the tables point at variables / values that do not exist (plain Chinese message).
    """
    try:
        cfg = PlanConfig.model_validate(raw)
    except ValidationError as e:
        first = e.errors()[0]
        raise ValueError(f"设置不合格：{'.'.join(str(x) for x in first['loc'])}（{first['msg']}）") from e
    ids = [s.id for s in cfg.subs]
    if len(set(ids)) != len(ids):
        raise ValueError("子项目编号重复")
    if cfg.split == "single" and len(cfg.subs) != 1:
        raise ValueError("只有一个子项目时，不能有多个子项目")
    for name, quota in (("定量配额", cfg.qt), ("深访配额", cfg.lt), ("座谈会配额", cfg.ft)):
        for d in quota.dims:
            if d not in cfg.vars:
                raise ValueError(f"{name}用了不存在的变量：{d}")
        seen = set()
        for r in quota.rows:
            if r.id in seen:
                raise ValueError(f"{name}有重复的行")
            seen.add(r.id)
            if len(r.vs) != len(quota.dims):
                raise ValueError(f"{name}的行和变量个数对不上")
            for d, vid in zip(quota.dims, r.vs):
                if vid not in {v.id for v in cfg.vars[d]}:
                    raise ValueError(f"{name}用了不存在的取值")
    return cfg


def has_placeholder(text: str, key: str) -> bool:
    return "{{" + key + "}}" in text


def quota_sums(cfg: PlanConfig) -> dict[str, int]:
    return {"q": sum(r.t for r in cfg.qt.rows), "i": sum(r.i for r in cfg.lt.rows), "f": sum(r.f for r in cfg.ft.rows)}


def uses_interviews(cfg: PlanConfig) -> bool:
    return any(m in cfg.methods for m in ("depth", "group", "async"))


def compliance(cfg: PlanConfig) -> list[dict]:
    """The compliance list judged from the config: id, title, basis, passed, and how it is met. 14 items, +4 when interviews are used."""
    q = uses_interviews(cfg)
    langs_ok = all(cfg.langs.get(s.lang, False) for s in cfg.subs) if cfg.subs else False
    tq_ok = has_placeholder(cfg.tq, "purpose") and (not q or has_placeholder(cfg.tl, "purpose"))
    unsub_ok = has_placeholder(cfg.tq, "unsubscribe") and (not q or has_placeholder(cfg.tl, "unsubscribe"))
    items = [
        ("c1", "告知义务", "GDPR / PIPL", tq_ok, "邀请首句说明目的和控制者身份，落地页展示隐私声明"),
        ("c2", "同意有效性", "GDPR", True, "同意与服务条款分开勾选，不默认勾选（受访者同意页已这样做）"),
        ("c3", "数据最小化", "PIPL", True, "只采集研究需要的字段"),
        ("c4", "留存期限", "GDPR", True, f"项目结项后 {RETENTION_DAYS} 天自动删除个人数据，洞察结论保留（平台统一规定，同意书里写明）"),
        ("c5", "假名化存储", "PIPL", True, "研究侧、观察员和客户只见受访者编号，不见姓名和联系方式"),
        ("c6", "跨境数据传输", "数据传输法", cfg.cons.xb, "数据存放在新加坡，由中国团队访问；需要签署标准合同或完成评估（请勾选确认）"),
        ("c7", "子处理者管理", "GDPR", True, "转写、翻译、AI 模型供应商均需有处理协议（同意书里写明数据发给谁）"),
        ("c8", "数据主体权利", "GDPR / PIPL", True, "撤回、删除、导出：受访者可随时要求，管理员可立即清除"),
        ("c9", "退订机制", "Spam Act", unsub_ok, "所有触达模板必须包含退订方式"),
        ("c10", "敏感数据保护", "PIPL", True, "不采集敏感个人信息"),
        ("c11", "未成年人管控", "GDPR / PIPL", cfg.cons.minor, "人群定义含年龄 18 岁以上，并在筛选题中校验（请勾选确认）"),
        ("c12", "本地语言告知", "LOPDGDD", langs_ok, "每个子项目的执行语言都要勾选为发送语言，并有当地语言的告知文字"),
        ("c13", "处理活动记录", "GDPR / PIPL", True, "审计日志自动记录，法务可导出核对"),
        ("c14", "安全事件通报", "GDPR", True, "有通报流程和负责人（部署说明里的监控告警）"),
    ]
    if q:
        items += [
            ("q1", "录音录像同意", "定性新增", cfg.cons.rec, "进入房间前单独勾选，受访者同意页已有（请勾选确认）"),
            ("q2", "再联系同意", "定性新增", cfg.cons.recon, "问卷末尾询问是否同意再联系，决定能否被圈人邀请深访（请勾选确认）"),
            ("q3", "观察员只见编号", "定性新增", cfg.cons.mask, "观察员和客户看不到姓名和联系方式（平台已这样做，请勾选确认）"),
            ("q4", "AI 转写、翻译与摘要告知", "定性新增", cfg.cons.ai, "告知受访者录音会被 AI 处理，并说明保存期限（请勾选确认）"),
        ]
    return [{"id": i, "title": t, "basis": b, "passed": bool(ok), "how": how} for i, t, b, ok, how in items]


def blockers(cfg: PlanConfig) -> list[str]:
    """Reasons the plan cannot be submitted yet, in plain Chinese (empty when it can)."""
    failed = [c["title"] for c in compliance(cfg) if not c["passed"]]
    out = ["合规项未通过：" + "、".join(failed)] if failed else []
    sums = quota_sums(cfg)
    for key, label, target in (("q", "定量配额", cfg.n.q), ("i", "深访配额", cfg.n.i), ("f", "座谈会配额", cfg.n.f)):
        if sums[key] != target:
            out.append(f"{label}合计 {sums[key]} 与目标 {target} 不一致")
    if not cfg.subs:
        out.append("至少要有一个子项目")
    return out


def config_hash(cfg: PlanConfig) -> str:
    """A fingerprint of the config at submission time (shown to the reviewer; a change after submission changes it)."""
    return hashlib.sha256(json.dumps(cfg.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def suggest_tags(text: str, known: list[str]) -> list[str]:
    """Labels from the project's own participant labels that the description mentions (a plain match, not a guess)."""
    low = text.lower()
    return [t for t in known if t and re.search(re.escape(t.lower()), low)][:30]


def summary(config_json: str) -> dict:
    """What the project bar needs from a plan without parsing all of it: the research order and the sub-projects (id, name)."""
    try:
        raw = json.loads(config_json or "{}")
        return {"design": raw.get("design", "ql") if raw.get("design") in DESIGNS else "ql",
                "subs": [{"id": str(s.get("id", "")), "name": str(s.get("name", ""))} for s in raw.get("subs", [])][:30]}
    except (ValueError, AttributeError, TypeError):
        return {"design": "ql", "subs": []}
