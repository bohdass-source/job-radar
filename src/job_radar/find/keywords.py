"""Mode 3 — keyword search. Free text goes to semantic search; city names in the
query become a location filter; "+word" must appear, "-word" must not."""

from __future__ import annotations

import re

from .filters import SearchFilters, city_coords


def parse_query(query: str, f: SearchFilters) -> tuple[str, SearchFilters]:
    must = [w[1:] for w in query.split() if w.startswith("+") and len(w) > 1]
    never = [w[1:] for w in query.split() if w.startswith("-") and len(w) > 1]
    rest = [w for w in query.split() if not w.startswith(("+", "-"))]
    cities = list(f.cities)
    words = []
    for w in rest:
        clean = re.sub(r"[^\wäöüÄÖÜß-]", "", w)
        if not f.cities and clean and city_coords(clean):
            cities.append(clean)          # "LLM Berlin" -> filter Berlin, search "LLM"
        else:
            words.append(w)
    semantic = " ".join(words + must) or query
    return semantic, f.model_copy(update={"cities": cities, "keywords_all": f.keywords_all + must,
                                          "keywords_none": f.keywords_none + never})
