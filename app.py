"""Module Navigator: prerequisites, follow-on modules, option rules and
recommendations for a research interest or career.

The data is the catalogue in `modules.py`, loaded from the JSON files beside
this one. No network: everything is answered from the published module
guides, which were read in advance. Every answer names the academic year it
comes from, because module guides change every year.
"""

from __future__ import annotations

import re
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

TERM_NAMES = {1: "autumn", 2: "spring", 3: "summer"}

CHECK = "Unofficial: confirm with your department before you register."


class NoModuleNamed(AgentAppError):
    user_message = "Tell me which module you mean, by its code (like MATH60028) or its title."
    retryable = False


class UnknownModule(AgentAppError):
    user_message = "I could not find that module in the Imperial module guides I have."
    retryable = False


class UnknownDegree(AgentAppError):
    user_message = "I do not know that degree yet."
    retryable = False


class NoInterests(AgentAppError):
    user_message = ("Tell me what you are interested in or the career you have in mind, "
                    "for example quant finance, machine learning research or climate science.")
    retryable = False


class NoDirectionMatched(AgentAppError):
    user_message = "I could not match that to any subject area in the module guides."
    retryable = False


class UnknownYear(AgentAppError):
    user_message = "I do not have module choices for that year of that degree."
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
        if call.outcome_id == "modules.recommend":
            return self._recommend(call)
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
        degree, text = self._degree(call, default=DEFAULT_DEGREE)
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

    def _recommend(self, call: Call) -> Result:
        interests = (call.inputs.get("interests") or "").strip()
        if not interests:
            raise NoInterests("no interests in the message")
        degree, _ = self._degree(call, default=None)
        year = call.inputs.get("year") or DEFAULT_YEAR
        if degree and str(year) not in self.catalogue.degrees[degree]["years"]:
            raise UnknownYear(f"no plan for {degree} year {year}")

        taken, unknown = [], []
        for item in self._split(call.inputs.get("taken") or ""):
            code, _ = self.catalogue.find(item)
            (taken.append(code) if code else unknown.append(item))

        found = self.catalogue.recommend(interests, taken, degree, year)
        if found is None:
            areas = ", ".join(sorted(self.catalogue.streams[s].lower()
                                     for s in self.catalogue.stream_keywords))
            error = NoDirectionMatched(f"no stream matches {interests!r}")
            error.user_message = (f"I could not match '{interests}' to a subject area. "
                                  f"I know about: {areas}.")
            raise error
        if not found["recommended"] and not found["needs_prerequisites"]:
            error = NoDirectionMatched(f"nothing in {degree} year {year} for {interests!r}")
            error.user_message = (f"Nothing on offer in year {year}"
                                  + (f" of {degree}" if degree else "")
                                  + f" matches {', '.join(found['directions']).lower()}.")
            raise error

        if degree:
            info = self.catalogue.degrees[degree]
            label, academic_year = f"{degree} {info['title']}", info["academic_year"]
        else:
            label = "any degree (name yours to see only its options)"
            academic_year = next(iter(self.catalogue.academic_year.values()))
        data = {
            "interests_matched": found["directions"],
            "degree": label,
            "year": year,
            "academic_year": academic_year,
            "recommended": found["recommended"],
            "needs_prerequisites": found["needs_prerequisites"],
        }
        if unknown:
            data["not_recognised"] = unknown
        top = (found["recommended"] or found["needs_prerequisites"])[0].split(" (Year")[0]
        return Result(
            data=data,
            narrative=(f"For {', '.join(found['directions'])}, the strongest match in year "
                       f"{year} is {top}. Suggestions only, from the module guides' "
                       f"subject areas. {CHECK}"),
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _degree(self, call: Call, default: str | None) -> tuple[str | None, str]:
        """The degree code asked about, and the words it was named with.

        Unnamed, or named only as a subject ("maths"), it is the default and
        the words come back empty, so the answer can say it assumed one.
        """
        text = (call.inputs.get("degree") or "").strip()
        if not text:
            return default, ""
        generic = self.catalogue.generic_degrees.get(" ".join(text.lower().strip(" .").split()))
        if generic:
            return generic, ""
        degree = self.catalogue.find_degree(text)
        if degree is None:
            error = UnknownDegree(f"no degree matches {text!r}")
            error.user_message = ("I do not know that degree yet. I know: "
                                  + ", ".join(f"{c} {d['title']}"
                                              for c, d in self.catalogue.degrees.items()) + ".")
            raise error
        return degree, text

    @staticmethod
    def _split(text: str) -> list[str]:
        """Modules listed as the user typed them: commas, semicolons or lines.

        Not "and": "Lebesgue Measure and Integration" is one module.
        """
        return [part.strip() for part in re.split(r"[,;\n]+", text) if part.strip()]

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
            error.user_message = (f"I could not find '{text}' in the Imperial module "
                                  "guides I have.")
        raise error

    @staticmethod
    def _terms(terms: list[int]) -> str:
        if not terms:
            return "not stated in the guide"
        return " and ".join(TERM_NAMES.get(t, str(t)) for t in terms)
