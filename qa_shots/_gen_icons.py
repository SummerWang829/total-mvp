"""Generate app/static/ui_icons.css: one modern line-icon set for the side navigation (masks keyed on the link target)."""
import pathlib
from urllib.parse import quote

HEAD = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'>"
I = {
    "grid": "<rect x='3.5' y='3.5' width='7' height='7' rx='2'/><rect x='13.5' y='3.5' width='7' height='7' rx='2'/><rect x='3.5' y='13.5' width='7' height='7' rx='2'/><rect x='13.5' y='13.5' width='7' height='7' rx='2'/>",
    "library": "<path d='M4 6.5A2.5 2.5 0 0 1 6.5 4H11v15H6.5A2.5 2.5 0 0 0 4 21.5z'/><path d='M20 6.5A2.5 2.5 0 0 0 17.5 4H13v15h4.5a2.5 2.5 0 0 1 2.5 2.5z'/>",
    "clipboard": "<rect x='5' y='4.5' width='14' height='17' rx='2.5'/><path d='M9 3h6v3H9z'/><path d='M8.5 11h7M8.5 15h4.5'/>",
    "gauge": "<path d='M4.5 16a7.5 7.5 0 1 1 15 0'/><path d='M12 16l4-5'/><path d='M4 20h16'/>",
    "users": "<circle cx='9' cy='8' r='3.2'/><path d='M3.5 19.5a5.5 5.5 0 0 1 11 0'/><circle cx='17' cy='9' r='2.4'/><path d='M16.5 14.3a4.6 4.6 0 0 1 4 5.2'/>",
    "database": "<ellipse cx='12' cy='6' rx='7.5' ry='3'/><path d='M4.5 6v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6'/><path d='M4.5 12v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6'/>",
    "file": "<path d='M6 3.5h8l4 4V19a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 5 19V5a1.5 1.5 0 0 1 1-1.5z'/><path d='M14 3.5V8h4'/><path d='M8.5 12.5h7M8.5 16h5'/>",
    "sparkles": "<path d='M12 3.5l1.9 5 5 1.9-5 1.9-1.9 5-1.9-5-5-1.9 5-1.9z'/><path d='M19 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z'/>",
    "undo": "<path d='M4 12a8 8 0 1 0 2.6-5.9'/><path d='M4 4v4.5h4.5'/>",
    "video": "<rect x='3.5' y='5' width='17' height='14' rx='2.5'/><path d='M9.5 9.5l5 2.5-5 2.5z'/>",
    "eye": "<path d='M3.5 12.5C6 7.5 9 5 12 5s6 2.5 8.5 7.5C18 17.5 15 20 12 20s-6-2.5-8.5-7.5z'/><circle cx='12' cy='12.5' r='3'/>",
    "usercheck": "<circle cx='9.5' cy='8' r='3.2'/><path d='M3.5 19.5a6 6 0 0 1 12 0'/><path d='M16 11.5l2 2 3.5-3.5'/>",
    "radio": "<circle cx='12' cy='12' r='2'/><path d='M8 8a5.7 5.7 0 0 0 0 8M16 8a5.7 5.7 0 0 1 0 8M5 5.5a9.5 9.5 0 0 0 0 13M19 5.5a9.5 9.5 0 0 1 0 13'/>",
    "chat": "<path d='M20.5 12a8 8 0 0 1-11.7 7.1L3.5 20.5l1.4-4.6A8 8 0 1 1 20.5 12z'/><path d='M8.5 12h.01M12 12h.01M15.5 12h.01'/>",
    "bars": "<path d='M4 20.5h16'/><rect x='5.5' y='11' width='3.2' height='7' rx='1'/><rect x='10.4' y='6' width='3.2' height='12' rx='1'/><rect x='15.3' y='9' width='3.2' height='9' rx='1'/>",
    "people": "<circle cx='8' cy='9' r='2.8'/><circle cx='16.5' cy='9' r='2.8'/><path d='M3 19a5 5 0 0 1 10 0M12 19a5 5 0 0 1 9 0'/>",
    "logout": "<path d='M9.5 4h-3A2.5 2.5 0 0 0 4 6.5v11A2.5 2.5 0 0 0 6.5 20h3'/><path d='M15 8l4 4-4 4M19 12H9.5'/>",
}


def uri(name: str) -> str:
    return 'url("data:image/svg+xml,' + quote(HEAD + I[name] + "</svg>", safe="=:/'") + '")'


SEL = [
    ('nav.side a.nv[href$="#/projects"] .ic', "grid"), ('nav.side a.nv[href$="#/repo"] .ic', "library"),
    ('nav.side a.nv[href$="/wiz"] .ic', "clipboard"), ('nav.side a.nv[href$="/home"] .ic', "gauge"),
    ('nav.side a.nv[href$="/studies"] .ic', "sparkles"), ('nav.side a.nv[href$="/listening"] .ic', "radio"),
    ('nav.side a.nv[href$="#/team"] .ic', "users"), ('nav.side a.nv[href$="#/retention"] .ic', "database"),
    ('nav.side a.nv[href$="#/audit"] .ic', "file"), ('nav.side a.nv[href$="#/ai"] .ic', "sparkles"),
    ('nav.side a.nv[href$="#/demo"] .ic', "undo"), ('nav.side a.nv[href$="/rooms"] .ic', "video"),
    ('nav.side a.nv[href$="/pool"] .ic', "eye"), ('nav.side a.nv[href$="/insights"] .ic', "eye"),
    ('nav.side a.nv[href$="/people"] .ic', "usercheck"),
    ('nav.side .sic.k-depth', "chat"), ('nav.side .sic.k-group', "people"), ('nav.side .sic.k-quant', "bars"),
    ('nav.side #out', "logout"),
]
css = ["/* 左侧导航的现代线条图标（用户 2026-11-02 确认的效果图）：统一 1.7 线宽、圆角、24 格；由 qa_shots/_gen_icons.py 生成。 */"]
for sel, name in SEL:
    if sel.endswith("#out"):
        continue
    css.append(f"{sel}{{--ico:{uri(name)}}}")
css.append("/* 阶段（深访 / 座谈会 / 定量）：图标代替数字圆圈，数字缩成右上角的小角标，研究顺序不丢 */")
css.append("nav.side .sic{position:relative;display:inline-grid;place-items:center;width:20px;height:20px;border:0;background:transparent;border-radius:0;font-size:0;color:inherit}")
css.append("nav.side .sic::before{content:\"\";width:20px;height:20px;background:currentColor;-webkit-mask:var(--ico) center/contain no-repeat;mask:var(--ico) center/contain no-repeat}")
css.append("nav.side .sic::after{content:attr(data-n);position:absolute;right:-7px;top:-5px;min-width:13px;height:13px;border-radius:7px;background:var(--orange,#E8590C);color:#fff;font-size:9px;line-height:13px;text-align:center;font-weight:700}")
css.append(".app.rail nav.side .sic{width:22px;height:22px}.app.rail nav.side .sic::before{width:22px;height:22px}")
for sel, name in SEL:
    if sel.startswith("nav.side .sic."):
        css.append(f"{sel}{{--ico:{uri(name)}}}")
css.append("/* 退出：图标在文字前 */")
css.append(f"nav.side #out{{display:inline-flex;align-items:center;justify-content:center;gap:6px}}")
css.append(f"nav.side #out::before{{content:\"\";width:16px;height:16px;background:currentColor;-webkit-mask:{uri('logout')} center/contain no-repeat;mask:{uri('logout')} center/contain no-repeat}}")
pathlib.Path("app/static/ui_icons.css").write_text("\n".join(css) + "\n", encoding="utf-8")
print("wrote", len(css), "rules")
