"""Module Navigator: prerequisites, follow-on modules and option rules.

The data is the catalogue in `modules.py`, loaded from the JSON files beside
this one. No network: everything is answered from the published module
guides, which were read in advance. Every answer names the academic year it
comes from, because module guides change every year.
"""

from __future__ import annotations

from pathlib import Path

from pippa_agentapp_sdk import (
    AgentAppError,
    Call,
    Manifest,
    Result,
    load_manifest_file,
    validate_manifest,
)

from modules import Catalogue

MANIFEST_PATH = Path(__file__).with_name("manifest.yaml")

#: Without a degree, the plain Mathematics BSc: its year 3 options are the
#: ones every maths degree shares, and the specialist rules are extra.
DEFAULT_DEGREE = "G100"
DEFAULT_YEAR = 3
#: Saying only "maths" names no particular degree, so it gets the default.
GENERIC_DEGREE = {"maths", "math", "mathematics", "imperial maths", "imperial mathematics"}

TERM_NAMES = {1: "autumn", 2: "spring", 3: "summer"}

CHECK = "Unofficial: confirm with your department before you register."


class NoModuleNamed(AgentAppError):
    user_message = "Tell me which module you mean, by its code (like MATH60028) or its title."
    retryable = False


class UnknownModule(AgentAppError):
    user_message = "I could not find that module in the Mathematics module guides I have."
    retryable = False


class UnknownDegree(AgentAppError):
    user_message = (
        "I only know the Imperial Mathematics degrees so far: G100, G103, G104, "
        "G102, G125, G1F3, G1G3 and G1GH."
    )
    retryable = False


class UnknownYear(AgentAppError):
    user_message = "I have module choices for second and third year only."
    retryable = False


class App:
    def __init__(self) -> None:
        self._manifest = validate_manifest(load_manifest_file(MANIFEST_PATH))
        self.catalogue = Catalogue.load()

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    def handle(self, call: Call) -> Result:
        if call.outcome_id == "modules.prerequisites":
            return self._prerequisites(call)
        if call.outcome_id == "modules.unlocks":
            return self._unlocks(call)
        if call.outcome_id == "modules.options":
            return self._options(call)
        raise AgentAppError(f"this app cannot do {call.outcome_id!r}")

    # ── Outcomes ──────────────────────────────────────────────────────────

    def _prerequisites(self, call: Call) -> Result:
        code = self._module(call)
        module = self.catalogue.modules[code]
        found = self.catalogue.prerequisites(code)
        data = {
            "module": self.catalogue.label(code),
            "academic_year": self.catalogue.academic_year[code],
            "term": self._terms(module.get("terms") or []),
            "ects": float(module["ects"]),
            **found,
        }
        if module.get("notes"):
            data["notes"] = module["notes"]
        if found["essential"]:
            sentence = f"{module['title']} needs {len(found['essential'])} module(s) first."
        elif found["useful"]:
            sentence = f"{module['title']} has no essential prerequisites, only useful ones."
        else:
            sentence = f"The guide lists no prerequisites for {module['title']}."
        return Result(data=data, narrative=f"{sentence} {CHECK}")

    def _unlocks(self, call: Call) -> Result:
        code = self._module(call)
        found = self.catalogue.unlocks(code)
        title = self.catalogue.modules[code]["title"]
        total = len(found["essential_for"]) + len(found["useful_for"])
        sentence = (f"{title} leads on to {total} later module(s)." if total
                    else f"No later module in the guides lists {title} as a prerequisite.")
        return Result(
            data={
                "module": self.catalogue.label(code),
                "academic_year": self.catalogue.academic_year[code],
                **found,
            },
            narrative=f"{sentence} {CHECK}",
        )

    def _options(self, call: Call) -> Result:
        text = (call.inputs.get("degree") or "").strip()
        degree = self.catalogue.find_degree(text) if text else DEFAULT_DEGREE
        if degree is None and text.lower().strip(" .") in GENERIC_DEGREE:
            degree, text = DEFAULT_DEGREE, ""
        if degree is None:
            raise UnknownDegree(f"no degree matches {text!r}")
        year = call.inputs.get("year") or DEFAULT_YEAR
        term = call.inputs.get("term")
        found = self.catalogue.options(degree, year, term)
        if found is None:
            raise UnknownYear(f"no plan for {degree} year {year}")
        rules = found["rules"]
        if not text:
            rules = [f"No degree given, so this is for {degree}; name your degree "
                     "for its specialist rules.", *rules]
        if term in TERM_NAMES:
            rules = [f"Only modules taught in the {TERM_NAMES[term]} term are listed.", *rules]
        info = self.catalogue.degrees[degree]
        return Result(
            data={
                "degree": f"{degree} {info['title']}",
                "year": year,
                "academic_year": info["academic_year"],
                "core": found["core"],
                "options": found["options"],
                "rules": rules,
            },
            narrative=(f"{len(found['core'])} core and {len(found['options'])} optional "
                       f"module(s) for {degree} year {year}. {CHECK}"),
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _module(self, call: Call) -> str:
        text = (call.inputs.get("module") or "").strip()
        if not text:
            raise NoModuleNamed("no module in the message")
        code, suggestions = self.catalogue.find(text)
        if code:
            return code
        error = UnknownModule(f"no module matches {text!r}")
        if suggestions:
            names = "; ".join(self.catalogue.label(c) for c in suggestions[:5])
            error.user_message = f"'{text}' could mean more than one module: {names}. Which one?"
        else:
            error.user_message = (f"I could not find '{text}' in the Imperial Mathematics "
                                  "module guides I have.")
        raise error

    @staticmethod
    def _terms(terms: list[int]) -> str:
        if not terms:
            return "not stated in the guide"
        return " and ".join(TERM_NAMES.get(t, str(t)) for t in terms)
