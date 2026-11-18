"""Stage 2 experiment (user chose option A, 2026-11-18): run the AI host's red-line checks on outline questions and see which checks make sense for questions a researcher writes.

Prints a Markdown report: per question which red lines fire; per red line how many fired. Questions come from the sample guides in eval/guides and the demo outline; a few
deliberately bad ones (marked) show what the checks catch. Pure rules, no model calls: python scripts/outline_wb_redline_trial.py [output.md]
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.moderator.guardrails import CHECKS, GuardrailContext  # noqa: E402
from app.moderator.knowledge import InterviewGuide  # noqa: E402
from app.outline_wb import demo  # noqa: E402

BAD = [("难道你不觉得这款精华很好用吗？", "诱导"), ("你平时用什么护肤品，又为什么选它，还有价格能接受吗？", "一次问两件事"), ("请告诉我你的电话号码，方便我们回访。", "要个人信息"),
       ("你有没有听说过 OpenAI 做的调研？", "问到模型/公司"), ("这款产品是我们最新研发的，你应该会喜欢，你觉得呢？", "评价/诱导"), ("请你详细描述一下你从小到大使用护肤品的全部经历，包括每一个品牌每一次购买的原因、渠道、价格、使用感受以及和家人朋友的讨论。", "太长")]


def collect() -> list[tuple[str, str]]:
    seen, out = set(), []
    for f in sorted((ROOT / "eval" / "guides").glob("*.json")):
        g = json.loads(f.read_text(encoding="utf-8"))
        qs = list(g.get("warmup_questions", [])) + [q for t in g.get("topics", []) for q in t.get("questions", [])] + [q for s in g.get("stimuli", []) for q in s.get("questions", [])]
        for q in qs:
            if q not in seen:
                seen.add(q)
                out.append((q, f"示例提纲 {f.stem}"))
    for item in demo.ITEMS:
        if item[1] in ("question", "rating") and item[3] not in seen:
            seen.add(item[3])
            out.append((item[3], "演示大纲"))
    return out


def main() -> int:
    ctxs = {lang: GuardrailContext(guide=InterviewGuide.model_validate(json.loads((ROOT / "eval" / "guides" / f).read_text(encoding="utf-8"))))
            for lang, f in (("zh", "sg_skincare_zh.json"), ("en", "sg_food_delivery_en.json"))}   # H6 (language) needs a guide in the question's own language
    rows = [(q, src, False, "") for q, src in collect()] + [(q, "故意写坏的", True, why) for q, why in BAD]
    per_line: Counter = Counter()
    lines = ["# 红线检查试验（阶段 2，用户选 A）", "", f"共 {len(rows)} 道题：{sum(1 for r in rows if not r[2])} 道来自示例提纲/演示大纲，{sum(1 for r in rows if r[2])} 道是故意写坏的。",
             "注意：示例提纲是给 AI 主持测试写的，不是真实研究员写的，所以结论只说明“检查能不能用”，不说明真实通过率。", "", "| 题 | 来源 | 触发的红线 |", "|---|---|---|"]
    for q, src, bad, why in rows:
        hits = []
        ctx = ctxs["zh" if any("一" <= c <= "鿿" for c in q) else "en"]
        for line, check in CHECKS:
            if check(q, ctx):
                hits.append(line.value)
                per_line[(line.value, bad)] += 1
        lines.append(f"| {q[:60]} | {src}{'（' + why + '）' if bad else ''} | {', '.join(hits) or '—'} |")
    lines += ["", "## 每条红线触发次数（真实题 / 故意写坏的）", "", "| 红线 | 真实题 | 故意写坏的 |", "|---|---|---|"]
    for line, _ in CHECKS:
        lines.append(f"| {line.value} | {per_line[(line.value, False)]} | {per_line[(line.value, True)]} |")
    lines += ["", "## 结论（阶段 2 接入修订质检时这样用）", "",
              "- **直接用（拦截，不能接受）：** H2 诱导、H4 个人信息、H9 建议/评价、H12 太长、H6 语言（按每道题自己的语言设置）。",
              "- **只提醒，不拦截：** H1 一次一问——“什么时候买的？为什么选它？”这类“经历 + 原因”是访谈大纲里的常见写法，示例里 2 道被提示；让研究员自己决定是否拆开。H13 问到模型或公司——研究的题目可能真的要谈某个 AI 产品，只提醒。",
              "- **不用于大纲题：** H3 AI 披露、H5 停止或危机、H7 评价受访者、H8 保密品牌、H10 禁谈话题、H11 接管后沉默、H14 不说感觉到——它们管的是 AI 主持人当场说的话和现场状态，不是写在大纲里的题。H8、H10 在项目写了“保密品牌 / 禁谈话题”时再接入。",
              "- **要另写的“题目专用”规则：** 一句里用“和/以及/还有”并列多件事；预设答案（如“最大的吸引点是什么”默认有吸引点）；封闭式问句却没有追问；一句超过 30 个字；用了受访者可能不懂的术语；与所选访谈形式不匹配（文字访谈里写“请出声说”）；与访谈对象不匹配（对象是学生却问投保）。",
              "- **局限：** 只用了 56 道现成题，不是真实研究员写的；规则只在中文和英文上试过。"]
    text = "\n".join(lines) + "\n"
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
