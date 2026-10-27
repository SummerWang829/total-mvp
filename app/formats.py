"""How an interview is done (访谈形式): five fixed kinds. A project allows some of them per research method; candidates choose among what is allowed, the choice fills the invitation.

The first version only stores, shows and writes them into the invitation: what happens inside the room (no camera for audio only, screen sharing, voice transcription of a text chat...) comes later one by one.
"""
FORMATS = {"audio": "纯音频", "text": "文字访谈", "text_voice": "文字+语音转录", "video": "视频", "video_screen": "视频+屏幕共享"}
EN = {"audio": "Audio only", "text": "Text chat", "text_voice": "Text chat with voice transcription", "video": "Video", "video_screen": "Video with screen sharing"}
METHODS = ("depth", "group")  # the methods that are done live and so have a format
DEFAULT = ["video"]


class FormatError(ValueError):
    """A format list that cannot be used; the message is plain Chinese."""


def clean(codes: list[str] | None) -> list[str]:
    """Known codes only, once each, in the order given. Raises FormatError for an unknown one."""
    out = list(dict.fromkeys(codes or []))
    if any(c not in FORMATS for c in out):
        raise FormatError("访谈形式只能是：" + "、".join(FORMATS.values()))
    return out


def of(project_formats: dict | None, methods: list[str]) -> dict[str, list[str]]:
    """The allowed formats per live method the project uses (video when nothing was chosen); a method the project does not use has none."""
    stored = project_formats or {}
    return {m: [c for c in (stored.get(m) or DEFAULT) if c in FORMATS] or list(DEFAULT) for m in METHODS if m in methods}


def union(project_formats: dict | None, methods: list[str]) -> list[str]:
    """Every format any of the project's live methods allows (what the recruiting questionnaire may offer), in the platform's order."""
    allowed = {c for v in of(project_formats, methods).values() for c in v}
    return [c for c in FORMATS if c in allowed]


def apply(project_formats: dict | None, methods: list[str], new: dict[str, list[str]]) -> dict[str, list[str]]:
    """The stored formats after a change: only methods the project uses, each at least one format.

    Raises:
        FormatError: an unknown format, a method the project does not use, or a method left with none.
    """
    cur = of(project_formats, methods)
    for m, codes in new.items():
        if m not in cur:
            raise FormatError("项目没有这个研究方法，不能设置它的访谈形式")
        picked = clean(codes)
        if not picked:
            raise FormatError("每个方法至少要允许一种访谈形式")
        cur[m] = picked
    return cur
