"""The project's languages and interview formats follow the sub-projects (step 1 of the wizard)."""
import pytest

from app import formats, plans


def _cfg(subs: list[dict]) -> dict:
    cfg = plans.default_config()
    cfg["subs"] = subs
    return cfg


def test_project_languages_are_every_language_a_sub_project_runs_in():
    cfg = plans.validate(_cfg([
        {"id": "s1", "name": "新加坡", "lang": "英语", "langs": ["英语"], "design": "iq"},
        {"id": "s2", "name": "香港", "lang": "中文", "langs": ["中文", "英语"], "design": "fq"},
    ]))
    assert plans.sub_langs(cfg) == ["中文", "英语"]


def test_formats_are_per_method_and_follow_the_sub_projects_that_run_that_method():
    cfg = plans.validate(_cfg([
        {"id": "s1", "name": "新加坡", "lang": "英语", "design": "iq", "fmt": {"depth": ["audio"]}},
        {"id": "s2", "name": "香港", "lang": "中文", "design": "fq", "fmt": {"group": ["text"]}},
    ]))
    assert cfg.fmt == {"depth": ["audio"], "group": ["text"]}


def test_a_method_nobody_runs_has_no_formats_and_an_unset_sub_project_is_video():
    cfg = plans.validate(_cfg([{"id": "s1", "name": "新加坡", "lang": "英语", "design": "q"}]))
    assert "depth" not in cfg.fmt and "group" not in cfg.fmt
    cfg = plans.validate(_cfg([{"id": "s1", "name": "新加坡", "lang": "英语", "design": "i"}]))
    assert cfg.fmt == {"depth": ["video"]}


def test_a_format_that_does_not_exist_is_refused():
    with pytest.raises(ValueError):
        plans.validate(_cfg([{"id": "s1", "name": "新加坡", "lang": "英语", "design": "i", "fmt": {"depth": ["hologram"]}}]))
    assert formats.FORMATS["video"]
