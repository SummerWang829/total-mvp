"""Project materials the AI moderator works from (task doc 1: a lean per-project document set, put straight into context).

Hypotheses only steer which follow-up to pick; they are never told to the participant (task doc 1, v5 row) and the
red-line gate (Sprint 2) checks that. Forbidden topics feed red line H10; brand_disclosure_allowed and product_facts feed H8.
"""
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MustCoverItem(BaseModel):
    """Something the topic must find out. Coverage of these is the "提纲必问要素覆盖率" metric (gate ≥ 95 %)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=300)


class GuideTopic(BaseModel):
    """One core topic of the discussion guide.

    Attributes:
        questions: prepared opening question(s), asked in order; follow-ups come from the engine.
        must_cover: what the topic must find out; saturation is judged against these.
        weight: share of the core time compared with the other topics (1.0 = equal).
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=120)
    questions: list[str] = Field(min_length=1)
    must_cover: list[MustCoverItem] = Field(default_factory=list)
    weight: float = Field(default=1.0, gt=0, le=10)


class Stimulus(BaseModel):
    """A material shown in the STIMULI phase, with the question(s) about it."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=300)
    questions: list[str] = Field(min_length=1)


class InterviewGuide(BaseModel):
    """Everything the moderator knows about one project. Validated once when loaded; immutable afterwards."""

    model_config = ConfigDict(frozen=True)

    research_goal: str = Field(min_length=1, max_length=1000)         # internal: may reveal hypotheses, never read out
    participant_topic: str = Field(default="", max_length=20)         # what the intro tells the participant, e.g. "护肤品选购"
    language: str = Field(pattern="^(en|zh|ms|ta)$")
    duration_min: float = Field(default=60.0, ge=10, le=180)
    warmup_questions: list[str] = Field(min_length=1)
    topics: list[GuideTopic] = Field(min_length=1)
    stimuli: list[Stimulus] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)           # internal only, never said
    forbidden_topics: list[str] = Field(default_factory=list)     # red line H10
    brand_disclosure_allowed: bool = False                        # red line H8: the client's brand stays hidden by default
    client_brand_names: list[str] = Field(default_factory=list)   # what must not be said unless disclosure is allowed
    product_facts: list[str] = Field(default_factory=list)        # the only product facts the AI may state (H8: no invented facts)

    @field_validator("warmup_questions", "hypotheses", "forbidden_topics", "client_brand_names", "product_facts")
    @classmethod
    def _no_blank_lines(cls, values: list[str]) -> list[str]:
        """Reject empty strings: an empty forbidden topic would match everything, an empty question would be said aloud."""
        if any(not v.strip() for v in values):
            raise ValueError("entries must not be blank")
        return [v.strip() for v in values]

    @model_validator(mode="after")
    def _ids_are_unique(self) -> "InterviewGuide":
        """Topic ids and must-cover ids identify progress and coverage; duplicates would merge two items into one."""
        topic_ids = [t.id for t in self.topics]
        item_ids = [i.id for t in self.topics for i in t.must_cover]
        if len(set(topic_ids)) != len(topic_ids):
            raise ValueError("topic ids must be unique")
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("must-cover item ids must be unique across the guide")
        return self

    def topic(self, topic_id: str) -> GuideTopic:
        """The topic with this id.

        Raises:
            KeyError: no such topic.
        """
        for t in self.topics:
            if t.id == topic_id:
                return t
        raise KeyError(topic_id)
