"""The module catalogue: the data files beside this one, and lookups over them.

Each data file is one department and one academic year, built in advance from
the department's published module guides (see `sources` inside it). Nothing
is fetched at run time.

To add a degree or a year, drop another `<department>_<year>.json` beside this
file in the same shape and add its name to DATA_FILES. Module codes are
unique across Imperial, so files merge into one catalogue.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DATA_FILES = ("maths_2026-27.json",)

_CODE = re.compile(r"\b([a-z]{4})?\s*([4-7]\d{4})\b")
_NOT_WORD = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    return " ".join(_NOT_WORD.sub(" ", text.lower()).split())


@dataclass
class Catalogue:
    modules: dict = field(default_factory=dict)
    degrees: dict = field(default_factory=dict)
    streams: dict = field(default_factory=dict)
    #: module code -> academic year of the file it came from
    academic_year: dict = field(default_factory=dict)
    #: normalised title or alias -> module code
    names: dict = field(default_factory=dict)
    #: module code -> codes that list it as required / recommended
    required_for: dict = field(default_factory=dict)
    useful_for: dict = field(default_factory=dict)

    @classmethod
    def load(cls, folder: Path | None = None) -> "Catalogue":
        folder = folder or Path(__file__).parent
        catalogue = cls()
        for name in DATA_FILES:
            data = json.loads((folder / name).read_text("utf-8"))
            catalogue.add(data)
        return catalogue

    def add(self, data: dict) -> None:
        year = data["academic_year"]
        self.streams.update(data.get("streams", {}))
        for code, degree in data.get("degrees", {}).items():
            self.degrees[code] = {**degree, "academic_year": year}
        for code, module in data["modules"].items():
            self.modules[code] = module
            self.academic_year[code] = year
            for name in [module["title"], *module.get("aliases", [])]:
                self.names[_norm(name)] = code
        # The reverse map is computed, never stored, so it cannot drift.
        for code, module in self.modules.items():
            prereqs = module.get("prerequisites", {})
            required = prereqs.get("required", {})
            needed = list(required.get("all_of", []))
            for group in required.get("any_of", []):
                needed.extend(group)
            for before in needed:
                self.required_for.setdefault(before, []).append(code)
            for before in prereqs.get("recommended", []):
                self.useful_for.setdefault(before, []).append(code)

    # ── Lookup ────────────────────────────────────────────────────────────

    def find(self, text: str) -> tuple[str | None, list[str]]:
        """A module code for what somebody typed, or None and some suggestions.

        Accepts a code with or without its prefix ("MATH60028", "60028"), a
        title, or an alias. A partial title counts only if it fits exactly one
        module, so "fluid dynamics" asks which one rather than guessing.
        """
        query = _norm(text)
        if not query:
            return None, []
        match = _CODE.search(query)
        if match:
            prefix, digits = match.group(1), match.group(2)
            hits = [c for c in self.modules
                    if c[-5:] == digits and (not prefix or c[:4].lower() == prefix)]
            if len(hits) == 1:
                return hits[0], []
            if hits:
                return None, sorted(hits)
        if query in self.names:
            return self.names[query], []
        partial = sorted({code for name, code in self.names.items()
                          if f" {query} " in f" {name} "})
        if len(partial) == 1:
            return partial[0], []
        if partial:
            return None, partial
        close = difflib.get_close_matches(query, self.names, n=3, cutoff=0.75)
        suggestions = sorted({self.names[name] for name in close})
        if len(suggestions) == 1:
            return suggestions[0], []
        return None, suggestions

    def find_degree(self, text: str) -> str | None:
        query = _norm(text)
        if not query:
            return None
        for code, degree in self.degrees.items():
            if query == code.lower() or query in (
                    _norm(n) for n in [degree["title"], *degree.get("aliases", [])]):
                return code
        for code in self.degrees:
            if re.search(rf"\b{code.lower()}\b", query):
                return code
        return None

    def label(self, code: str) -> str:
        module = self.modules.get(code)
        if not module:
            return code
        return f"{code} {module['title']} (Year {module['year']})"

    # ── Answers ───────────────────────────────────────────────────────────

    def prerequisites(self, code: str) -> dict:
        module = self.modules[code]
        prereqs = module.get("prerequisites", {})
        required = prereqs.get("required", {})
        essential = [self.label(c) for c in required.get("all_of", [])]
        for group in required.get("any_of", []):
            essential.append("one of: " + "; ".join(self.label(c) for c in group))
        excluded = [self.label(c) for c in module.get("excludes", [])]
        if module.get("level7_form"):
            excluded.append(f"{module['level7_form']} {module['title']} (the MATH7 form; "
                            "you cannot take both)")
        return {
            "essential": essential,
            "useful": [self.label(c) for c in prereqs.get("recommended", [])],
            "excluded_with": excluded,
        }

    def unlocks(self, code: str) -> dict:
        return {
            "essential_for": [self.label(c) for c in sorted(self.required_for.get(code, []))],
            "useful_for": [self.label(c) for c in sorted(self.useful_for.get(code, []))],
        }

    def options(self, degree: str, year: int, term: int | None = None) -> dict | None:
        plan = self.degrees[degree]["years"].get(str(year))
        if plan is None:
            return None

        def keep(code: str) -> bool:
            terms = self.modules[code].get("terms") or []
            return term is None or term in terms

        return {
            "core": [self.label(c) for c in plan.get("core", []) if keep(c)],
            "options": [self.label(c) for c in plan.get("optional", []) if keep(c)],
            "rules": list(plan.get("rules", [])),
        }

    def recommend(self, taken: list[str], direction: str, degree: str | None = None,
                  year: int | None = None) -> list[str]:
        """EXTENSION POINT: suggest modules for a career or research direction.

        Not wired to any outcome yet. Declaring an outcome Pippa can route to
        and then refuse would look exactly like a broken app. The data it
        needs is already in place: every module carries `streams` tags (see
        `streams` in the data file), `prerequisites` says what must come
        first, and `unlocks` gives the reverse direction. A first version
        would: map `direction` to stream ids, keep modules in those streams
        whose essential prerequisites are all in `taken` and which are not
        excluded by anything in `taken`, then rank them by how many later
        modules in the same streams each one unlocks.

        When it is implemented, add a `modules.recommend` outcome to
        manifest.yaml with `taken` (array of string) and `direction` (string)
        inputs, and a branch in App.handle.
        """
        raise NotImplementedError("recommendations are not implemented yet")
