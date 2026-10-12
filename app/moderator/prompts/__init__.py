"""Versioned prompts (v001.json, v002.json, ...). Every eval report records the version; a new version never overwrites an old one."""
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..constants import Operator

PROMPT_DIR = Path(__file__).parent
CURRENT_PROMPT_VERSION = "v001"


class PromptSet(BaseModel):
    """One version of the moderator's prompts."""

    model_config = ConfigDict(frozen=True)

    version: str = Field(pattern=r"^v\d{3}$")
    analyze: str = Field(min_length=50)
    speak: str = Field(min_length=50)
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
    if set(prompts.operators) != set(Operator):
        raise ValueError("every operator needs guidance in the prompt file")
    return prompts
