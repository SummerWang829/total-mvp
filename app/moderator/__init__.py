"""AI moderator, text version (phase A). Design: docs/AI主持人_工程与训练任务说明_v1.md.

Four layers: state machine (fsm.py, code decides the flow) -> model reasoning (engine.py) -> red-line gate before anything is said
(guardrails.py) -> humans (observer whispers, take-over, red alerts; wired into the room in Sprint 5).
Built sprint by sprint; modules that are still empty say which sprint fills them.
"""
