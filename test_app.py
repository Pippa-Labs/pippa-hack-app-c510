"""Tests for Module Navigator, through LocalRunner so the manifest is enforced.

Two kinds: the outcomes answer as the manifest promises, and the data files
hold together (every code referenced exists, no prerequisite loops, no alias
claimed twice). The second kind is what catches a typo in a hand-built
catalogue before a student is told something wrong.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pippa_agentapp_sdk import LocalRunner, OutputShapeViolation

from app import App, NoModuleNamed, UnknownDegree, UnknownModule, UnknownYear
from modules import DATA_FILES, Catalogue

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).parent


def call(outcome: str, inputs: dict, *, explicit: bool = False):
    return LocalRunner(App()).call(
        outcome, inputs, granted_secrets={}, now=NOW, request_id="test-1",
        explicit=explicit,
    ).result


def test_the_manifest_is_valid():
    app = App()
    assert app.manifest.app_id == "hack.app_c510"
    assert [o.id for o in app.manifest.outcomes] == [
        "modules.prerequisites", "modules.unlocks", "modules.options"]


# ── modules.prerequisites ──────────────────────────────────────────────────


def test_prerequisites_by_code():
    data = call("modules.prerequisites", {"module": "MATH60028"}).data
    assert data["module"].startswith("MATH60028 Probability Theory 1")
    assert any("MATH50006" in m for m in data["essential"])
    assert data["academic_year"] == "2026-27"
    assert data["ects"] == 7.5
    assert data["term"] == "autumn"


def test_prerequisites_by_bare_number_and_title():
    by_number = call("modules.prerequisites", {"module": "60028"}).data
    by_title = call("modules.prerequisites", {"module": "probability theory 1"}).data
    assert by_number == by_title


def test_prerequisites_by_alias():
    data = call("modules.prerequisites", {"module": "Galois Theory"}).data
    assert any("MATH50005" in m for m in data["essential"])


def test_bpes_modules_exclude_each_other():
    data = call("modules.prerequisites", {"module": "BUSI60045"}).data
    assert any("BUSI60043" in m for m in data["excluded_with"])
    assert any("BUSI60040" in m for m in data["excluded_with"])


def test_the_math7_form_is_excluded():
    data = call("modules.prerequisites", {"module": "MATH60037"}).data
    assert any(m.startswith("MATH70037") for m in data["excluded_with"])


def test_no_module_is_a_readable_error():
    with pytest.raises(NoModuleNamed):
        call("modules.prerequisites", {"module": ""})
    with pytest.raises(NoModuleNamed):
        call("modules.prerequisites", {})


def test_an_unknown_module_is_named_back():
    with pytest.raises(UnknownModule) as failure:
        call("modules.prerequisites", {"module": "underwater basket weaving"})
    assert "underwater basket weaving" in failure.value.user_message


def test_an_ambiguous_title_asks_which_one():
    with pytest.raises(UnknownModule) as failure:
        call("modules.prerequisites", {"module": "fluid dynamics"})
    assert "MATH60001" in failure.value.user_message
    assert "MATH60002" in failure.value.user_message


# ── modules.unlocks ────────────────────────────────────────────────────────


def test_unlocks_is_the_reverse_of_prerequisites():
    data = call("modules.unlocks", {"module": "lebesgue"}).data
    assert data["module"].startswith("MATH50006")
    assert any("MATH60028" in m for m in data["essential_for"])


def test_a_final_year_module_unlocks_nothing_but_still_answers():
    data = call("modules.unlocks", {"module": "MATH60150"}).data
    assert data["essential_for"] == [] and data["useful_for"] == []


# ── modules.options ────────────────────────────────────────────────────────


def test_options_for_a_degree_and_year():
    data = call("modules.options", {"degree": "G1G3", "year": 3}).data
    assert data["degree"].startswith("G1G3")
    assert data["year"] == 3
    assert data["core"] == []
    assert any("MATH60049" in m for m in data["options"])
    assert any("MATH60043" in r for r in data["rules"])


def test_options_filtered_to_one_term():
    data = call("modules.options", {"degree": "G100", "year": 3, "term": 1}).data
    assert any("MATH60028" in m for m in data["options"])       # autumn
    assert not any("MATH60031" in m for m in data["options"])   # spring
    assert "autumn" in data["rules"][0]


def test_options_without_a_degree_default_and_say_so():
    data = call("modules.options", {}).data
    assert data["degree"].startswith("G100")
    assert data["year"] == 3
    assert "G100" in data["rules"][0]


def test_options_for_plain_maths_default_too():
    data = call("modules.options", {"degree": "maths", "year": 2}).data
    assert data["degree"].startswith("G100")
    assert data["core"]


def test_an_unknown_degree_and_year_are_refused():
    with pytest.raises(UnknownDegree):
        call("modules.options", {"degree": "Medicine"})
    with pytest.raises(UnknownYear):
        call("modules.options", {"degree": "G100", "year": 7})


# ── Picked with @ ──────────────────────────────────────────────────────────


def test_picked_by_name_answers_and_refuses_as_usual():
    assert call("modules.unlocks", {"module": "applied pdes"}, explicit=True).data == \
        call("modules.unlocks", {"module": "applied pdes"}).data
    with pytest.raises(NoModuleNamed):
        call("modules.prerequisites", {"module": ""}, explicit=True)


def test_the_runner_enforces_the_manifest(monkeypatch):
    from pippa_agentapp_sdk import Result

    monkeypatch.setattr(App, "handle", lambda self, c: Result(data={"surprise": 1}))
    with pytest.raises(OutputShapeViolation):
        call("modules.options", {})


# ── The data itself ────────────────────────────────────────────────────────


def _files():
    return [json.loads((ROOT / name).read_text("utf-8")) for name in DATA_FILES]


def _referenced(module: dict) -> list[str]:
    prereqs = module.get("prerequisites", {})
    required = prereqs.get("required", {})
    codes = list(required.get("all_of", [])) + list(prereqs.get("recommended", []))
    for group in required.get("any_of", []):
        codes += group
    return codes + list(module.get("excludes", []))


def test_every_referenced_code_exists():
    catalogue = Catalogue.load()
    for data in _files():
        for code, module in data["modules"].items():
            for other in _referenced(module):
                assert other in catalogue.modules, f"{code} refers to unknown {other}"
        for degree, info in data["degrees"].items():
            for year, plan in info["years"].items():
                for other in plan.get("core", []) + plan.get("optional", []):
                    assert other in catalogue.modules, f"{degree} y{year} lists unknown {other}"


def test_no_alias_names_two_modules():
    seen: dict[str, str] = {}
    for data in _files():
        for code, module in data["modules"].items():
            for name in {n.lower() for n in [module["title"], *module.get("aliases", [])]}:
                assert seen.setdefault(name, code) == code, f"{name!r}: {seen[name]} and {code}"


def test_no_prerequisite_loops():
    catalogue = Catalogue.load()

    def before(code):
        prereqs = catalogue.modules[code].get("prerequisites", {})
        required = prereqs.get("required", {})
        out = list(required.get("all_of", [])) + list(prereqs.get("recommended", []))
        for group in required.get("any_of", []):
            out += group
        return out

    state: dict[str, int] = {}

    def visit(code, path):
        if state.get(code) == 1:
            raise AssertionError("prerequisite loop: " + " -> ".join(path + [code]))
        if state.get(code) == 2:
            return
        state[code] = 1
        for other in before(code):
            visit(other, path + [code])
        state[code] = 2

    for code in catalogue.modules:
        visit(code, [])


def test_terms_years_and_ects_are_sensible():
    for data in _files():
        for code, module in data["modules"].items():
            assert set(module.get("terms") or []) <= {1, 2, 3}, code
            assert module["year"] in (1, 2, 3, 4), code
            assert module["ects"] > 0, code
            for stream in module.get("streams", []):
                assert stream in data["streams"], f"{code}: unknown stream {stream}"


def test_the_recommendation_hook_is_there_but_not_wired():
    with pytest.raises(NotImplementedError):
        Catalogue.load().recommend(["MATH50006"], "quantitative finance")
