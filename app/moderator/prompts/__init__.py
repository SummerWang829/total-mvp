"""Versioned prompts (v001.json, v002.json, ...). Every eval report records the version; a new version never overwrites an old one."""
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..constants import OPERATORS_ADDED_IN, Operator

PROMPT_DIR = Path(__file__).parent
CURRENT_PROMPT_VERSION = "v006"  # v006 (2026-10-17): long-interview memory (key facts from the analysis call, rolling summary prompt, recalled older lines); v005 (2026-10-13): varied move-on bridges, fresh_angle without a label; v004 (2026-10-13): fresh_angle last try + move-on wording for participants with nothing more to say; v003 (2026-10-12): questions only (no commands), no "是不是", reword instead of repeating, explore instruction; v001/v002 stay on disk for comparison


class PromptSet(BaseModel):
    """One version of the moderator's prompts."""

    model_config = ConfigDict(frozen=True)

    version: str = Field(pattern=r"^v\d{3}$")
    analyze: str = Field(min_length=50)
    speak: str = Field(min_length=50)
    summarize: str = ""  # v006+: refreshes the running summary of a long interview (empty in older versions: no summary call)
    operators: dict[Operator, str]


def load_prompts(version: str = CURRENT_PROMPT_VERSION) -> PromptSet:
    """Read and validate one prompt version.

    Raises:
        FileNotFoundError: no such version.
        pydantic.ValidationError: the file is malformed or misses an operator.
        ValueError: the file's version does not match its name.
    """
    data = json.loads((PROMPT_DIR / f"{version}.json").read_text(encoding="utf-8"))
    prompts = PromptSet.model_validate(data)
    if prompts.version != version:
        raise ValueError(f"{version}.json declares version {prompts.version}")
    required = {op for op in Operator if OPERATORS_ADDED_IN.get(op, "v001") <= version}  # an old version never knew the operators added after it
    if set(prompts.operators) != required:
        raise ValueError("every operator needs guidance in the prompt file")
    return prompts
