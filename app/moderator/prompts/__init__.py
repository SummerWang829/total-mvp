"""Versioned prompts (v001.json, v002.json, ...). Every eval report records the version; a new version never overwrites an old one."""
import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..constants import OPERATORS_ADDED_IN, Operator

PROMPT_DIR = Path(__file__).parent
CURRENT_PROMPT_VERSION = "v008"  # v008 (2026-10-17): facts and summary are read in a background thread (v007 slowed every reply: p95 5.1 s vs 2.7 s); v007 (2026-10-17): the participant's screening answers as background (PARTICIPANT_PROFILE); v006 (2026-10-17): long-interview memory (key facts from the analysis call, rolling summary prompt, recalled older lines); v005 (2026-10-13): varied move-on bridges, fresh_angle without a label; v004 (2026-10-13): fresh_angle last try + move-on wording for participants with nothing more to say; v003 (2026-10-12): questions only (no commands), no "是不是", reword instead of repeating, explore instruction; v001/v002 stay on disk for comparison

ACTIVE_PROMPT_VERSION = os.environ.get("MODERATOR_PROMPT_VERSION") or CURRENT_PROMPT_VERSION  # evaluations compare versions: MODERATOR_PROMPT_VERSION=v005 python -m eval.runner ...


class PromptSet(BaseModel):
    """One version of the moderator's prompts."""

    model_config = ConfigDict(frozen=True)

    version: str = Field(pattern=r"^v\d{3}$")
    analyze: str = Field(min_length=50)
    speak: str = Field(min_length=50)
    remember: str = ""  # v008+: pulls the facts out of one answer, in the background (empty in older versions: facts come from the analysis call, if at all)
    summarize: str = ""  # v006+: refreshes the running summary of a long interview (empty in older versions: no summary call)
    operators: dict[Operator, str]


def load_prompts(version: str | None = None) -> PromptSet:
    """Read and validate one prompt version.

    Raises:
        FileNotFoundError: no such version.
        pydantic.ValidationError: the file is malformed or misses an operator.
        ValueError: the file's version does not match its name.
    """
    version = version or ACTIVE_PROMPT_VERSION
    data = json.loads((PROMPT_DIR / f"{version}.json").read_text(encoding="utf-8"))
    prompts = PromptSet.model_validate(data)
    if prompts.version != version:
        raise ValueError(f"{version}.json declares version {prompts.version}")
    required = {op for op in Operator if OPERATORS_ADDED_IN.get(op, "v001") <= version}  # an old version never knew the operators added after it
    if set(prompts.operators) != required:
        raise ValueError("every operator needs guidance in the prompt file")
    return prompts
