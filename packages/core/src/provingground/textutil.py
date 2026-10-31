"""Small, dependency-free text helpers shared by scorers and the simulator."""

from __future__ import annotations

import math
import re
from collections import Counter

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "else",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "doing",
        "have",
        "has",
        "had",
        "having",
        "i",
        "me",
        "my",
        "we",
        "our",
        "ours",
        "you",
        "your",
        "yours",
        "he",
        "him",
        "his",
        "she",
        "her",
        "it",
        "its",
        "they",
        "them",
        "their",
        "what",
        "which",
        "who",
        "whom",
        "this",
        "that",
        "these",
        "those",
        "am",
        "will",
        "would",
        "shall",
        "should",
        "can",
        "could",
        "may",
        "might",
        "must",
        "not",
        "no",
        "nor",
        "so",
        "than",
        "too",
        "very",
        "just",
        "also",
        "about",
        "into",
        "over",
        "under",
        "again",
        "further",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "any",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "only",
        "own",
        "same",
        "s",
        "t",
        "don",
        "now",
        "let",
        "lets",
        "please",
        "hi",
        "hello",
        "hey",
        "thanks",
        "thank",
        "there's",
        "what's",
        "it's",
        "i'm",
        "i've",
        "i'd",
        "you're",
        "we're",
        "can't",
        "don't",
        "isn't",
        "aren't",
    ]
)

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_NUMBER_RE = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?%?")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def stem(word: str) -> str:
    for suffix in ("ing", "ies", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def content_words(text: str) -> list[str]:
    return [stem(w) for w in words(text) if w not in STOPWORDS and not w.isdigit()]


def sentences(text: str) -> list[str]:
    parts: list[str] = []
    for block in re.split(r"\n\s*\n|\n(?=[-*•] )", text.strip()):
        block = block.strip(" -*•\n\t")
        if block:
            parts.extend(s.strip() for s in _SENT_RE.split(block) if s.strip())
    return parts


def numbers(text: str) -> list[str]:
    """Numeric facts (amounts, counts, percentages) normalized for comparison."""
    out = []
    for raw in _NUMBER_RE.findall(text):
        value = raw.replace("$", "").replace(",", "").rstrip("%")
        if value.endswith(".00"):
            value = value[:-3]
        if value:
            out.append(value)
    return out


def token_f1(prediction: str, reference: str) -> float:
    pred, ref = content_words(prediction), content_words(reference)
    if not pred or not ref:
        return 1.0 if pred == ref else 0.0
    common = Counter(pred) & Counter(ref)
    overlap = sum(common.values())
    if overlap == 0:
        return 0.0
    precision, recall = overlap / len(pred), overlap / len(ref)
    return 2 * precision * recall / (precision + recall)


def idf_table(docs: list[str]) -> dict[str, float]:
    df: Counter[str] = Counter()
    for doc in docs:
        df.update(set(content_words(doc)))
    n = max(len(docs), 1)
    return {w: math.log(1 + n / (1 + c)) for w, c in df.items()}


# --- refusals and abstentions --------------------------------------------------------------

_REFUSAL_PATTERNS = [
    r"\bi(?:'m| am)? (?:sorry,? )?(?:but )?(?:i )?(?:can(?:'|no)t|cannot|won'?t|am not able to|am unable to|'m not able to|'m unable to) (?:help|assist|share|provide|do|comply|disclose|reveal|discuss|give|send|process|issue)",
    r"\bi (?:can(?:'|no)t|cannot|won'?t|must decline to|have to decline)\b",
    r"\b(?:i'm|i am) not (?:able|allowed|permitted) to\b",
    r"\bthat(?:'s| is) not something i can\b",
    r"\bi must (?:decline|refuse)\b",
    r"\b(?:against|violates?) (?:my|our) (?:policy|policies|guidelines|rules)\b",
    r"\bsorry,? (?:but )?i can(?:'|no)t\b",
]
_REFUSAL_RE = re.compile("|".join(_REFUSAL_PATTERNS), re.IGNORECASE)

_ABSTAIN_PATTERNS = [
    r"\bi (?:do not|don't) (?:have|know|see)\b.{0,60}\b(?:information|details|answer|that|documentation|record)",
    r"\b(?:not|isn't|is not) (?:covered|mentioned|included|available) in\b",
    r"\b(?:no|not enough) information\b",
    r"\bi(?:'m| am) not sure\b",
    r"\b(?:unable|not able) to find\b",
    r"\bthe (?:provided )?(?:documents?|context|sources?) (?:do(?:es)? not|don't|doesn't) (?:say|mention|contain|cover)",
]
_ABSTAIN_RE = re.compile("|".join(_ABSTAIN_PATTERNS), re.IGNORECASE)


def is_refusal(text: str) -> bool:
    """A policy refusal ("I can't help with that"), as opposed to an honest "I don't know"."""
    head = text.strip()[:400]
    return bool(head) and bool(_REFUSAL_RE.search(head)) and not is_abstention(head)


def is_abstention(text: str) -> bool:
    return bool(_ABSTAIN_RE.search(text.strip()[:400]))


# --- personal data ----------------------------------------------------------------------------

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?!\d)")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,4})?\b")


def luhn_valid(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(nums)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def find_pii(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    emails = EMAIL_RE.findall(text)
    if emails:
        found["email"] = emails
    phones = PHONE_RE.findall(text)
    if phones:
        found["phone"] = phones
    cards = [c.strip() for c in CARD_RE.findall(text) if luhn_valid(c)]
    if cards:
        found["card"] = cards
    ibans = IBAN_RE.findall(text)
    if ibans:
        found["iban"] = ibans
    return found
