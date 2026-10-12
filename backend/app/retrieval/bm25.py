"""In-memory BM25 index over transcript chunks.

Why BM25 and not embeddings (see docs/architecture.md, "Retrieval trade-offs"):
 * zero extra model dependency -> works on a laptop with only Ollama running
 * transcripts are keyword-dense (names of frameworks, companies, metrics)
 * deterministic and explainable: we can show *why* a chunk matched
The index is rebuilt from the Postgres `chunks` table at startup (~seconds)."""
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

import snowballstemmer

_stemmer = snowballstemmer.stemmer("english")
WORD_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")

STOPWORDS = frozenset(
    """a about above after again against all am an and any are as at be because been before being below between both but by
    can could did do does doing down during each few for from further had has have having he her here hers him his how i if in
    into is it its itself just me more most my myself no nor not now of off on once only or other our ours out over own same she
    should so some such than that the their theirs them then there these they this those through to too under until up very was
    we were what when where which while who whom why will with would you your yours yeah like really kind sort thing things
    lenny podcast tell explain give me us say said says according does lennys""".split()
)


@lru_cache(maxsize=200_000)
def _stem(word: str) -> str:
    return _stemmer.stemWord(word)


def tokenize(text: str) -> list[str]:
    out = []
    for w in WORD_RE.findall(text.lower()):
        w = w.strip("'-")
        if len(w) < 2 or w in STOPWORDS:
            continue
        out.append(_stem(w))
    return out


@dataclass
class Doc:
    key: int  # chunk id
    payload: dict  # opaque metadata returned with hits
    tokens_len: int = 0


@dataclass
class ScoredDoc:
    doc: Doc
    score: float
    coverage: float  # idf-weighted share of query terms present in the doc (0..1)
    matched_terms: list[str] = field(default_factory=list)


class BM25Index:
    def __init__(self, k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs: list[Doc] = []
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.doc_terms: list[frozenset[str]] = []
        self.avgdl = 0.0
        self.idf: dict[str, float] = {}
        self._built = False
        self.by_key: dict[int, int] = {}

    def __len__(self) -> int:
        return len(self.docs)

    @property
    def ready(self) -> bool:
        return self._built and len(self.docs) > 0

    def build(self, items: list[tuple[int, str, dict]]) -> None:
        """items: (key, text, payload). Title/guest text should be pre-pended by the caller."""
        self.docs, self.postings, self.doc_terms = [], defaultdict(list), []
        total_len = 0
        for idx, (key, text, payload) in enumerate(items):
            toks = tokenize(text)
            counts = Counter(toks)
            self.docs.append(Doc(key=key, payload=payload, tokens_len=len(toks)))
            self.doc_terms.append(frozenset(counts))
            total_len += len(toks)
            for term, tf in counts.items():
                self.postings[term].append((idx, tf))
        n = len(self.docs)
        self.avgdl = total_len / n if n else 0.0
        self.idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}
        self.by_key = {d.key: i for i, d in enumerate(self.docs)}
        self._built = True

    def search(self, query: str, k: int = 20) -> tuple[list[ScoredDoc], list[str]]:
        q_terms = list(dict.fromkeys(tokenize(query)))
        if not q_terms or not self.docs:
            return [], q_terms
        max_idf = max(self.idf.values()) if self.idf else 1.0
        scores: dict[int, float] = defaultdict(float)
        for t in q_terms:
            post = self.postings.get(t)
            if not post:
                continue
            idf = self.idf[t]
            for idx, tf in post:
                dl = self.docs[idx].tokens_len
                denom = tf + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                scores[idx] += idf * tf * (self.k1 + 1) / denom
        if not scores:
            return [], q_terms
        # idf weight of the whole query; unseen terms are treated as maximally informative
        weights = {t: self.idf.get(t, max_idf) for t in q_terms}
        total_w = sum(weights.values()) or 1.0
        top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[: k * 4]
        out = []
        for idx, sc in top:
            present = [t for t in q_terms if t in self.doc_terms[idx]]
            cov = sum(weights[t] for t in present) / total_w
            out.append(ScoredDoc(self.docs[idx], sc, cov, present))
        # blend: BM25 magnitude ranks, coverage breaks ties toward chunks that match *all* concepts
        out.sort(key=lambda d: d.score * (0.6 + 0.4 * d.coverage), reverse=True)
        return out[:k], q_terms
