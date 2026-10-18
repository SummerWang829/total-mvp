"""Rule library for what participants type or say (contact details, abuse, hate, threats, sexual harassment): hide it, flag it, tell the team.

Used by every text entrance of every project: the AI interviewer's chat, the live captions, transcripts and the pre-interview answers.
"""
from .constants import CONDUCT, SEVERE, STRIKES_BEFORE_ENDING
from .rules import Checked, check, reload

__all__ = ["CONDUCT", "SEVERE", "STRIKES_BEFORE_ENDING", "Checked", "check", "reload"]
