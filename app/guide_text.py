"""Turn a plain-text discussion outline (typed or uploaded as .txt in the room page) into the AI moderator's guide.

The format is meant to be written by a researcher in any text editor:

    目标: 了解新加坡消费者如何选购护肤品          (research goal; internal, never read out)
    受访者话题: 护肤品选购                        (what the participant is told the topic is)
    语言: zh                                      (en / zh / ms / ta)
    时长: 45                                      (minutes)
    ## 暖场
    - 先聊聊你平时的护肤习惯？
    ## 话题: 选购决策
    - 你上一次买护肤品是怎么选的？
    * 了解: 在哪里获得信息                        (a must-cover point)
    ## 素材: ad_film | 欧莱雅新品广告片           (id must match a material installed for the interview)
    - 看完这支广告，你的第一反应是什么？
    ## 假设 / ## 禁谈 / ## 品牌 / ## 产品事实     (one "- item" per line)

English keywords work too (goal, topic, language, minutes, warmup, topic:, stimulus:, hypotheses, forbidden, brands, facts, cover).
The result is checked by the same InterviewGuide model the engine uses; nothing here weakens those checks.
"""
import re

from pydantic import ValidationError

from .moderator.knowledge import InterviewGuide

HEADER_KEYS = {"目标": "research_goal", "goal": "research_goal", "受访者话题": "participant_topic", "topic_for_participant": "participant_topic",
               "语言": "language", "language": "language", "时长": "duration_min", "minutes": "duration_min"}
LIST_SECTIONS = {"暖场": "warmup_questions", "warmup": "warmup_questions", "假设": "hypotheses", "hypotheses": "hypotheses", "禁谈": "forbidden_topics", "forbidden": "forbidden_topics",
                 "品牌": "client_brand_names", "brands": "client_brand_names", "产品事实": "product_facts", "facts": "product_facts"}
TOPIC_WORDS, STIMULUS_WORDS = ("话题", "topic"), ("素材", "stimulus")
COVER_WORDS = ("了解", "cover")
MAX_CHARS = 20_000


class GuideTextError(ValueError):
    """The outline text cannot be turned into a valid guide; the message is written for the researcher."""


def _split_key(line: str) -> tuple[str, str] | None:
    m = re.match(r"^\s*([^:：]{1,40})[:：]\s*(.*)$", line)
    return (m.group(1).strip().lower(), m.group(2).strip()) if m else None


def parse_guide_text(text: str) -> dict:
    """Parse the outline into a guide dict and validate it.

    Args:
        text: the outline in the format described in the module docstring.

    Returns:
        The guide as a JSON-ready dict (ids are generated: t1, t2 ... and t1c1 ...).

    Raises:
        GuideTextError: too long, an unknown section, a question outside a section, or the guide fails validation.
    """
    if len(text) > MAX_CHARS:
        raise GuideTextError(f"提纲太长（最多 {MAX_CHARS} 字）")
    g: dict = {"language": "zh", "duration_min": 45.0}
    lists: dict[str, list[str]] = {}
    topics: list[dict] = []
    stimuli: list[dict] = []
    cur: dict | None = None  # the topic / stimulus whose questions are being read
    cur_list: str | None = None
    for n, raw in enumerate(text.replace("\r", "").split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("##"):
            title = line.lstrip("#").strip()
            kv = _split_key(title)
            name, rest = (kv if kv else (title.lower(), ""))
            cur, cur_list = None, None
            if name in LIST_SECTIONS:
                cur_list = LIST_SECTIONS[name]
                lists.setdefault(cur_list, [])
            elif name in TOPIC_WORDS:
                cur = {"id": f"t{len(topics) + 1}", "title": rest or f"话题 {len(topics) + 1}", "questions": [], "must_cover": []}
                topics.append(cur)
            elif name in STIMULUS_WORDS:
                sid, _, desc = (p.strip() for p in rest.partition("|"))
                if not sid:
                    raise GuideTextError(f"第 {n} 行：素材要写成“## 素材: 编号 | 描述”")
                cur = {"id": sid, "description": desc or sid, "questions": []}
                stimuli.append(cur)
            else:
                raise GuideTextError(f"第 {n} 行：不认识的小节“{title}”（可用：暖场、话题、素材、假设、禁谈、品牌、产品事实）")
        elif line[0] in "-*•" and (item := line[1:].strip()):
            if cur_list:
                lists[cur_list].append(item)
            elif cur is not None and line[0] == "*" and "must_cover" in cur:
                kv = _split_key(item)
                point = kv[1] if kv and kv[0] in COVER_WORDS else item
                cur["must_cover"].append({"id": f"{cur['id']}c{len(cur['must_cover']) + 1}", "text": point})
            elif cur is not None:
                cur["questions"].append(item)
            else:
                raise GuideTextError(f"第 {n} 行：这一行不属于任何小节，请先写“## 暖场”或“## 话题: …”")
        elif (kv := _split_key(line)) and kv[0] in HEADER_KEYS and cur is None and cur_list is None:
            g[HEADER_KEYS[kv[0]]] = kv[1]
        else:
            raise GuideTextError(f"第 {n} 行看不懂：{line[:40]}")
    g.update(lists)
    g["topics"], g["stimuli"] = topics, stimuli
    try:
        g["duration_min"] = float(g["duration_min"])
    except (TypeError, ValueError) as e:
        raise GuideTextError("时长要写数字（分钟）") from e
    try:
        return InterviewGuide.model_validate(g).model_dump(mode="json")
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(x) for x in first["loc"]) or "提纲"
        raise GuideTextError(f"提纲不完整：{where} — {first['msg']}") from e


def guide_to_text(guide: dict) -> str:
    """The outline text of a guide (the reverse of :func:`parse_guide_text`), so a drafted guide can be shown and edited as plain text.

    Every value is written on one line (line breaks and runs of spaces collapse): a research background with several lines would otherwise put its later lines
    in the outline as lines that are neither a header nor a question, and the text could not be read back.
    """
    one = lambda v: " ".join(str(v).split())  # noqa: E731
    out = [f"目标: {one(guide['research_goal'])}", f"受访者话题: {one(guide.get('participant_topic', ''))}", f"语言: {guide['language']}", f"时长: {int(guide['duration_min'])}", "", "## 暖场"]
    out += [f"- {one(q)}" for q in guide["warmup_questions"]]
    for t in guide["topics"]:
        out += ["", f"## 话题: {one(t['title'])}"] + [f"- {one(q)}" for q in t["questions"]] + [f"* 了解: {one(m['text'])}" for m in t.get("must_cover", [])]
    for st in guide.get("stimuli", []):
        out += ["", f"## 素材: {one(st['id'])} | {one(st['description'])}"] + [f"- {one(q)}" for q in st["questions"]]
    for title, key in (("假设", "hypotheses"), ("禁谈", "forbidden_topics"), ("品牌", "client_brand_names"), ("产品事实", "product_facts")):
        if guide.get(key):
            out += ["", f"## {title}"] + [f"- {one(v)}" for v in guide[key]]
    return "\n".join(out) + "\n"
