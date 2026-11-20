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

from app import (App, NoDirectionMatched, NoInterests, NoModuleNamed, UnknownDegree,
                 UnknownModule, UnknownYear)
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
        "modules.prerequisites", "modules.unlocks", "modules.options", "modules.recommend"]


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


# ── Computing and JMC ──────────────────────────────────────────────────────


def test_computing_options_for_a_year():
    data = call("modules.options", {"degree": "G400", "year": 3}).data
    assert data["degree"].startswith("G400 Computing")
    assert any(m.startswith("COMP60010") for m in data["core"])
    assert data["options"]


def test_a_bare_subject_means_that_departments_default_degree():
    assert call("modules.options", {"degree": "computing", "year": 1}).data[
        "degree"].startswith("G400")


def test_jmc_by_its_nickname():
    data = call("modules.options", {"degree": "JMC MEng", "year": 4}).data
    assert data["degree"].startswith("GG41")


def test_computing_prerequisites_and_unlocks():
    data = call("modules.prerequisites", {"module": "COMP60021"}).data
    assert any(m.startswith("COMP50002") for m in data["essential"])
    back = call("modules.unlocks", {"module": "COMP50002"}).data
    assert any(m.startswith("COMP60021") for m in back["essential_for"])


def test_an_either_or_prerequisite():
    data = call("modules.prerequisites", {"module": "COMP70118"}).data
    assert any(m.startswith("one of:") and "COMP60037" in m and "COMP70015" in m
               for m in data["essential"])


def test_recommend_for_a_computing_career():
    data = call("modules.recommend", {"interests": "cybersecurity",
                                      "degree": "G401", "year": 4}).data
    assert data["interests_matched"] == ["Security and Privacy"]
    assert any(m.startswith("COMP70004") for m in data["recommended"])


def test_modules_not_running_are_never_recommended():
    catalogue = Catalogue.load()
    resting = {c for c, m in catalogue.modules.items() if m.get("not_running")}
    assert resting
    for interests in ("machine learning", "security", "software engineering", "robotics"):
        data = call("modules.recommend", {"interests": interests, "year": 4}).data
        assert not any(m.split()[0] in resting for m in data["recommended"])


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
    """Titles may repeat (a year 3 module and its year 4 form, or two modules
    the department gave one name); an alias is a nickname and must not."""
    titles = {m["title"].lower() for data in _files() for m in data["modules"].values()}
    seen: dict[str, str] = {}
    for data in _files():
        for code, module in data["modules"].items():
            for name in {n.lower() for n in module.get("aliases", [])} - titles:
                assert seen.setdefault(name, code) == code, f"{name!r}: {seen[name]} and {code}"


def test_each_module_is_defined_in_one_file_only():
    seen: dict[str, str] = {}
    for name, data in zip(DATA_FILES, _files()):
        for code in data["modules"]:
            assert seen.setdefault(code, name) == name, f"{code} in {seen[code]} and {name}"


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


def test_every_stream_has_keywords_and_every_keyword_a_stream():
    for data in _files():
        assert set(data["stream_keywords"]) == set(data["streams"])
        assert data["default_degree"] in data["degrees"]


# ── modules.recommend ──────────────────────────────────────────────────────


def test_recommend_for_a_career():
    data = call("modules.recommend", {"interests": "quant at a hedge fund",
                                      "degree": "G1GH", "year": 3}).data
    assert "Financial Mathematics" in data["interests_matched"]
    assert data["degree"].startswith("G1GH")
    assert any("MATH60012" in m for m in data["recommended"])


def test_missing_prerequisites_are_listed_apart():
    data = call("modules.recommend", {"interests": "machine learning research",
                                      "taken": "Probability for Statistics",
                                      "degree": "G1G3", "year": 3}).data
    assert any(m.startswith("MATH60049") and "MATH50011" in m
               for m in data["needs_prerequisites"])
    assert not any(m.startswith("MATH60049") for m in data["recommended"])


def test_a_year_2_elective_is_recommended_for_where_it_leads():
    data = call("modules.recommend", {"interests": "pure maths phd in number theory",
                                      "degree": "G100", "year": 2}).data
    assert data["recommended"][0].startswith("MATH50005")   # Groups and Rings


def test_modules_already_taken_are_left_out_and_unknown_ones_named():
    data = call("modules.recommend", {"interests": "number theory",
                                      "taken": "Galois Theory, Underwater Basket Weaving"}).data
    assert not any(m.startswith("MATH60037") for m in data["recommended"])
    assert data["not_recognised"] == ["Underwater Basket Weaving"]


def test_recommend_without_interests_or_with_none_known_says_what_to_type():
    with pytest.raises(NoInterests):
        call("modules.recommend", {})
    with pytest.raises(NoDirectionMatched) as failure:
        call("modules.recommend", {"interests": "knitting"})
    assert "knitting" in failure.value.user_message


def test_recommend_picked_by_name_behaves_the_same():
    inputs = {"interests": "climate science", "degree": "G1F3"}
    assert call("modules.recommend", inputs, explicit=True).data == \
        call("modules.recommend", inputs).data
