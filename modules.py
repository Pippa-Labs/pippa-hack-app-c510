"""The module catalogue: the data files beside this one, and lookups over them.

Each data file is one department and one academic year, built in advance from
the department's published module guides (see `sources` inside it). Nothing
is fetched at run time.

To add a degree or a year, drop another `<department>_<year>.json` beside this
file in the same shape and add its name to DATA_FILES. Module codes are
unique across Imperial, so files merge into one catalogue: a degree in one
file may list a module defined in another (JMC lists Maths modules), and a
module is defined in one file only.

When two modules share a title (a year 3 module and its year 4 form), the
title finds the earlier one; the later one is found by its code.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DATA_FILES = ("maths_2026-27.json", "computing_2026-27.json")

_CODE = re.compile(r"\b([a-z]{4})?\s*([4-7]\d{4})\b")
_NOT_WORD = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    return " ".join(_NOT_WORD.sub(" ", text.lower()).split())


@dataclass
class Catalogue:
    modules: dict = field(default_factory=dict)
    degrees: dict = field(default_factory=dict)
    streams: dict = field(default_factory=dict)
    #: stream id -> words that point to it in a description of interests
    stream_keywords: dict = field(default_factory=dict)
    #: module code -> academic year of the file it came from
    academic_year: dict = field(default_factory=dict)
    #: a bare subject ("maths", "computing") -> the degree it stands for
    generic_degrees: dict = field(default_factory=dict)
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
        for stream, words in data.get("stream_keywords", {}).items():
            known = self.stream_keywords.setdefault(stream, [])
            known.extend(w for w in words if w not in known)
        for code, degree in data.get("degrees", {}).items():
            self.degrees[code] = {**degree, "academic_year": year}
        for name in data.get("generic_degree_names", []):
            self.generic_degrees[_norm(name)] = data["default_degree"]
        for code, module in data["modules"].items():
            self.modules[code] = module
            self.academic_year[code] = year
        self.names = {}
        for code in sorted(self.modules, key=lambda c: (self.modules[c]["year"], c)):
            module = self.modules[code]
            for name in [module["title"], *module.get("aliases", [])]:
                self.names.setdefault(_norm(name), code)
        self.required_for, self.useful_for = {}, {}
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
        text = f"{code} {module['title']} (Year {module['year']})"
        if module.get("not_running"):
            text += " [not running in 2026-27]"
        return text

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

    def directions(self, text: str) -> dict[str, int]:
        """Stream id -> how many of its keywords the text mentions."""
        padded = f" {_norm(text)} "
        hits = {}
        for stream, words in self.stream_keywords.items():
            count = sum(1 for word in words if f" {_norm(word)} " in padded)
            if count:
                hits[stream] = count
        return hits

    def recommend(self, interests: str, taken: list[str] = (), degree: str | None = None,
                  year: int = 3, limit: int = 8) -> dict | None:
        """Modules that suit a research interest or career, for one year of study.

        `interests` is matched against each stream's keywords. A module scores
        for its own streams, and for the later modules in those streams that
        it is a prerequisite of, which is what lets a year 2 elective be
        recommended for where it leads. Modules already taken, or excluded
        by one that was, are left out. When `taken` is given, a module whose
        essential prerequisites are missing is listed apart rather than
        recommended. Returns None if the interests match no stream.
        """
        wanted = self.directions(interests)
        if not wanted:
            return None
        taken = self._with_prerequisites(taken)
        blocked = set(taken)
        for code in taken:
            module = self.modules.get(code, {})
            blocked.update(module.get("excludes", []))
            if module.get("level7_form"):
                blocked.add(module["level7_form"])

        if degree:
            plan = self.degrees[degree]["years"].get(str(year)) or {}
            pool = list(plan.get("optional", []))
        else:
            pool = [c for c, m in self.modules.items() if m["year"] == year]

        def own(code: str) -> float:
            return sum(wanted.get(s, 0) for s in self.modules[code].get("streams", []))

        scored = []
        for code in pool:
            module = self.modules[code]
            if (code in blocked or blocked & set(module.get("excludes", []))
                    or module.get("not_running")):
                continue
            score = (own(code)
                     + 0.5 * sum(own(c) for c in self.required_for.get(code, []))
                     + 0.25 * sum(own(c) for c in self.useful_for.get(code, [])))
            if score > 0:
                scored.append((-score, code))
        scored.sort()
        # Weak links ("useful for one later module") should not fill the list.
        if scored:
            floor = -scored[0][0] / 4
            scored = [(score, code) for score, code in scored if -score >= floor]

        recommended, needs = [], []
        for _, code in scored[:limit]:
            missing = self._missing(code, taken)
            areas = ", ".join(self.streams[s] for s in self.modules[code].get("streams", [])
                              if s in wanted)
            text = self.label(code) + (f": {areas}" if areas else ": leads on to modules in your area")
            if missing and taken:
                needs.append(f"{self.label(code)} needs " + ", ".join(missing) + " first")
            else:
                if missing:
                    text += "; needs " + ", ".join(missing) + " first"
                recommended.append(text)
        order = sorted(wanted, key=lambda s: (-wanted[s], s))
        return {
            "directions": [self.streams[s] for s in order],
            "recommended": recommended,
            "needs_prerequisites": needs,
        }

    def _with_prerequisites(self, taken) -> set[str]:
        """Everything taken, plus what those modules required: whoever took
        Galois Theory has had Groups and Rings, even if they did not say so."""
        done, todo = set(), list(taken)
        while todo:
            code = todo.pop()
            if code in done:
                continue
            done.add(code)
            todo.extend(self.modules.get(code, {}).get("prerequisites", {})
                        .get("required", {}).get("all_of", []))
        return done

    def _missing(self, code: str, taken: set[str]) -> list[str]:
        required = self.modules[code].get("prerequisites", {}).get("required", {})
        missing = [c for c in required.get("all_of", []) if c not in taken]
        for group in required.get("any_of", []):
            if not taken & set(group):
                missing.append(" or ".join(group))
        return missing
