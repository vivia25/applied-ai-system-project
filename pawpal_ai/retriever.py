"""Retrieval over data/care_guidelines.json.

Two steps: (1) a metadata filter picks the one life-stage guideline matching the pet's species
and age, and (2) a small dependency-free TF-IDF search finds modifier guidelines (e.g. "high-energy
breed", "arthritis") that match the owner's free-text notes.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("pawpal.retriever")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDELINES_PATH = os.path.join(ROOT, "data", "care_guidelines.json")

STOPWORDS = {
    "a", "an", "the", "and", "or", "to", "for", "of", "in", "on", "with", "is", "it", "he",
    "she", "they", "my", "our", "his", "her", "very", "really", "bit", "has", "have", "be",
    "dog", "cat", "pet", "year", "old", "about", "such", "any", "as", "than", "are", "who",
}


def _stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ing"):
        word = word[:-3]
    return word.rstrip("s") if len(word) > 3 else word


def tokenize(text: str) -> List[str]:
    """Lowercase words with stopwords removed and a crude stem applied."""
    return [_stem(w) for w in re.findall(r"[a-z]+", text.lower()) if w not in STOPWORDS]


class GuidelineStore:
    def __init__(self, docs: List[Dict]):
        if not docs:
            raise ValueError("guideline store is empty")
        self.docs = docs
        self.modifiers = [d for d in docs if d["kind"] == "modifier"]
        counts = [Counter(tokenize(d["title"] + " " + d["text"])) for d in self.modifiers]
        df = Counter(t for c in counts for t in c)
        n = len(self.modifiers)
        self._idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self._vecs = [self._vector(c) for c in counts]

    @classmethod
    def load(cls, path: str = GUIDELINES_PATH) -> "GuidelineStore":
        with open(path, encoding="utf-8") as f:
            docs = json.load(f)
        logger.info("loaded %d guidelines from %s", len(docs), path)
        return cls(docs)

    def _vector(self, counts: Counter) -> Dict[str, float]:
        vec = {t: c * self._idf[t] for t, c in counts.items() if t in self._idf}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def life_stage(self, species: str, age: int) -> Optional[Dict]:
        """The life-stage guideline for this species and age, or None if we have none."""
        for d in self.docs:
            if d["kind"] == "life_stage" and d["species"] == species.lower() \
                    and d["min_age"] <= age <= d["max_age"]:
                return d
        return None

    def modifiers_for(self, species: str, notes: str, min_score: float = 0.15) -> List[Tuple[Dict, float]]:
        """Modifier guidelines for this species whose text matches the notes, best first."""
        if not notes.strip():
            return []
        q = self._vector(Counter(tokenize(notes)))
        hits = []
        for doc, vec in zip(self.modifiers, self._vecs):
            if doc["species"] != species.lower():
                continue
            score = sum(w * vec.get(t, 0.0) for t, w in q.items())
            if score >= min_score:
                hits.append((doc, score))
        hits.sort(key=lambda h: -h[1])
        logger.info("notes=%r modifier hits=%s", notes, [(d["id"], round(s, 3)) for d, s in hits])
        return hits
