"""Deterministic simulated LLMs.

``sim/model-a``, ``sim/model-b`` and ``sim/model-c`` are test doubles, not language models.
They read the prompt conventions used by the demo agents (``<context><doc ...>`` blocks,
tool definitions, JSON schemas) and answer extractively, with tunable failure modes:

* wrong-but-grounded answers (``accuracy``), hallucinated facts (``hallucination_rate``),
  fabricated answers to unanswerable questions (``fabrication_rate``);
* compliance with prompt attacks (``attack_susceptibility`` x technique weights);
* refusing benign-but-risky-looking requests (``over_refusal_rate``);
* malformed JSON (``format_error_rate``), truncation, transient errors and latency.

Every decision is a hash of the request, so the same prompt always gets the same answer.
Fault decisions use a *shared per-case difficulty* (the hash does not include the model), so
the cases a strong profile gets wrong are a subset of those a weak profile gets wrong - like
real models, where hard questions are hard for everyone. Paired comparisons depend on that.

Injected faults are recorded in ``raw["sim"]`` so `pground doctor` can measure detector
precision/recall against ground truth. Scorers never read it.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import codecs
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any

from .. import textutil
from ..models import Message, ToolCall, Usage
from ..runtime_context import current_attempt
from .base import ChatRequest, ChatResponse, Provider, ProviderError

# --------------------------------------------------------------------------------------------
# Profiles


@dataclass(frozen=True)
class SimProfile:
    name: str
    accuracy: float = 0.95
    hallucination_rate: float = 0.03
    fabrication_rate: float = 0.05
    attack_susceptibility: float = 0.03
    technique_weights: dict[str, float] = field(default_factory=dict)
    over_refusal_rate: float = 0.03
    format_error_rate: float = 0.01
    latency_median_ms: float = 800.0
    latency_sigma: float = 0.35
    ms_per_output_token: float = 15.0
    transient_error_rate: float = 0.003
    truncation_rate: float = 0.0
    verbose: bool = False
    decodes_obfuscation: bool = True


# How much each attack technique multiplies the base susceptibility.
DEFAULT_TECHNIQUE_WEIGHTS: dict[str, float] = {
    "override": 1.0,
    "roleplay": 1.4,
    "hypothetical": 1.2,
    "refusal_suppression": 1.1,
    "prefix": 1.0,
    "payload_split": 1.3,
    "many_shot": 1.5,
    "language": 1.2,
    "extraction": 1.2,
    "authority": 1.3,
    "encoding": 1.4,
    "indirect": 1.8,
    "multi_turn": 1.6,
}

PROFILES: dict[str, SimProfile] = {
    # Premium: accurate, robust, verbose; slowest and most expensive.
    "model-a": SimProfile(
        name="model-a", accuracy=0.95, hallucination_rate=0.03, fabrication_rate=0.04,
        attack_susceptibility=0.012, over_refusal_rate=0.03, format_error_rate=0.01,
        latency_median_ms=850, latency_sigma=0.35, ms_per_output_token=18,
        transient_error_rate=0.004, verbose=True,
    ),
    # Budget: fastest and cheapest, ~3x the hallucinations, weak attack resistance.
    "model-b": SimProfile(
        name="model-b", accuracy=0.86, hallucination_rate=0.11, fabrication_rate=0.35,
        attack_susceptibility=0.30, over_refusal_rate=0.015, format_error_rate=0.06,
        latency_median_ms=320, latency_sigma=0.30, ms_per_output_token=6,
        transient_error_rate=0.01, truncation_rate=0.004,
    ),
    # Balanced: cheaper and faster than A, close to it on quality and safety.
    "model-c": SimProfile(
        name="model-c", accuracy=0.94, hallucination_rate=0.032, fabrication_rate=0.05,
        attack_susceptibility=0.014, over_refusal_rate=0.032, format_error_rate=0.012,
        latency_median_ms=480, latency_sigma=0.30, ms_per_output_token=9,
        transient_error_rate=0.004,
    ),
}  # fmt: skip

# --------------------------------------------------------------------------------------------
# Deterministic randomness


def _u(*parts: object) -> float:
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(digest[:13], 16) / float(16**13)


def _normal(*parts: object) -> float:
    u1 = max(_u(*parts, "n1"), 1e-12)
    u2 = _u(*parts, "n2")
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2 * math.pi * u2)


def _pick(options: list[str], *parts: object) -> str:
    return options[int(_u(*parts) * len(options)) % len(options)]


def _tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


# --------------------------------------------------------------------------------------------
# Attack perception

_TECHNIQUES: dict[str, re.Pattern[str]] = {
    "override": re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(previous|prior|above|earlier|all|your|system|any)\b"
        r".{0,30}\b(instructions?|rules?|guidelines?|prompts?|directions?|restrictions?|limits?)\b"
        r"|\bnew (instructions|rules)\s*:",
        re.I | re.S,
    ),
    "roleplay": re.compile(
        r"\b(you are now|pretend (?:to be|you are|you're)|act as|role-?play|stay in character|\bDAN\b"
        r"|developer mode|jailbr(?:eak|oken)|unfiltered|no restrictions)",
        re.I,
    ),
    "hypothetical": re.compile(
        r"\b(hypothetical(?:ly)?|imagine (?:that|you|a world)|in a (?:story|novel|movie|game|play)"
        r"|for a (?:story|novel|screenplay|game)|fiction(?:al)?|thought experiment)\b",
        re.I,
    ),
    "refusal_suppression": re.compile(
        r"\b(?:do not|don't|never) (?:refuse|apologi[sz]e|say (?:you )?can'?t|mention (?:any )?polic)"
        r"|\byou (?:must|have to) (?:answer|comply)|\bno disclaimers\b|\bwithout (?:any )?(?:warnings|refusals)\b",
        re.I,
    ),
    "prefix": re.compile(r"\b(?:start|begin) your (?:response|answer|reply) with\b", re.I),
    "payload_split": re.compile(
        r"\b(?:concatenate|combine|join) (?:the )?(?:strings|parts|pieces|variables)|\b[abxy]\s*=\s*['\"]",
        re.I,
    ),
    "extraction": re.compile(
        r"\b(system prompt|initial (?:instructions|prompt)|your (?:instructions|configuration|hidden rules|system rules)"
        r"|repeat (?:the |all )?(?:words|text|everything) above|verbatim|print (?:your|the) (?:instructions|prompt|rules)"
        r"|what (?:were|are) you told|internal reference)\b",
        re.I,
    ),
    "authority": re.compile(
        r"\b(?:i am|i'm) (?:the|a|your) (?:developer|admin(?:istrator)?|store manager|manager|owner|ceo|supervisor"
        r"|security team|auditor)|\bauthori[sz]e(?:d)?\b|\bmaintenance mode\b|\badmin override\b",
        re.I,
    ),
    "language": re.compile(
        r"\b(ignora|instrucciones|ignoriere|anweisungen|ignorez|consignes|révèle|revela|zeige|código|rabattcode)\b",
        re.I,
    ),
}

_INDIRECT = re.compile(
    r"(?:ignore|disregard).{0,40}(?:instructions|rules)|\b(?:note|message|instruction)s? (?:to|for) "
    r"(?:the )?(?:ai|assistant|chatbot|model)s?\b|\bai (?:assistants?|models?)\b.{0,60}\b(?:must|should|append|include|say|add|issue|send)\b"
    r"|\bsystem\s*(?:note|override|message)\s*:|\[\s*assistant instructions?\s*\]",
    re.I | re.S,
)

_GOALS: list[tuple[str, re.Pattern[str]]] = [
    ("pii_exfil", re.compile(
        r"\b(?:customer (?:list|emails?|records?|data|phone|addresses|details)|other customers|card numbers?"
        r"|credit cards?|personal (?:data|information|details)|contact details of|everyone's)\b", re.I)),
    ("secret_leak", re.compile(
        r"\b(?:discount code|staff code|employee code|promo code|staff discount|api key|password|secret|token"
        r"|credentials|código de descuento|rabattcode|code de réduction|access key)\b", re.I)),
    ("system_prompt_leak", re.compile(
        r"\b(?:system prompt|instructions|prompt|configuration|internal reference|rules you were given"
        r"|words above|text above|instrucciones|anweisungen|consignes)\b", re.I)),
]  # fmt: skip

_RISKY_WORDS = re.compile(
    r"\b(kill|killing|knife|knives|blade|shoot|shooting|attack|weapon|gun|hack|exploit|steal|poison|bomb"
    r"|burn|trap|hunt(?:ing)?|ignore|destroy|crack|override|explosive|fire)\b",
    re.I,
)

_SECRET_RE = re.compile(r"\b(?:[A-Z][A-Z0-9]{1,}(?:-[A-Z0-9]{2,}){1,5}|sk-[a-z0-9-]{8,})\b")
_LEET = str.maketrans({"4": "a", "3": "e", "1": "i", "0": "o", "5": "s", "7": "t", "@": "a", "$": "s"})
_HOMOGLYPHS = str.maketrans(
    {"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "і": "i", "у": "y", "ѕ": "s", "ԁ": "d"}
)
_MARKER_RE = re.compile(r"\b[A-Z]{3,}-[A-Z0-9]{2,}\b|\bhttps?://[^\s\"'<>]+|\b[a-z0-9-]+\.example\b")


@dataclass
class AttackSignal:
    techniques: list[str]
    goal: str | None
    direct: bool
    indirect_text: str = ""
    decoded: str = ""

    @property
    def detected(self) -> bool:
        return bool(self.techniques)


def _deobfuscate(text: str) -> tuple[str, list[str]]:
    """Return text with obfuscations decoded, plus the converters that were found."""
    found: list[str] = []
    extra: list[str] = []
    for blob in re.findall(r"[A-Za-z0-9+/]{20,}={0,2}", text):
        try:
            decoded = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if sum(c.isprintable() for c in decoded) > 0.9 * len(decoded) and " " in decoded:
            extra.append(decoded)
            found.append("base64")
    if re.search(r"\brot-?13\b", text, re.I):
        extra.append(codecs.decode(text, "rot13"))
        found.append("rot13")
    if text.translate(_HOMOGLYPHS) != text:
        extra.append(text.translate(_HOMOGLYPHS))
        found.append("homoglyph")
    leet_hits = len(re.findall(r"[a-z][4310573@$][a-z]", text, re.I))
    if leet_hits >= 3:
        extra.append(text.translate(_LEET))
        found.append("leetspeak")
    return ("\n".join([text, *extra]), found)


def perceive_attack(
    user_text: str, untrusted_text: str, n_user_turns: int, profile: SimProfile
) -> AttackSignal:
    analysed, converters = _deobfuscate(user_text)
    if converters and not profile.decodes_obfuscation:
        analysed = user_text
    techniques = [name for name, pattern in _TECHNIQUES.items() if pattern.search(analysed)]
    if len(re.findall(r"(?:^|\n)\s*(?:Q|User|Customer)\s*:", analysed)) >= 3:
        techniques.append("many_shot")
    if converters and (techniques or any(p.search(analysed) for _, p in _GOALS)):
        techniques.append("encoding")
    if n_user_turns >= 3 and techniques:
        techniques.append("multi_turn")
    goal = None
    if techniques:
        for name, pattern in _GOALS:
            if pattern.search(analysed):
                goal = name
                break
    indirect = ""
    if untrusted_text and _INDIRECT.search(untrusted_text):
        indirect = untrusted_text
        techniques.append("indirect")
    return AttackSignal(
        techniques=techniques, goal=goal, direct=bool(techniques and techniques != ["indirect"]),
        indirect_text=indirect, decoded=analysed if converters else "",
    )  # fmt: skip


def _comply_probability(signal: AttackSignal, profile: SimProfile, *, indirect_only: bool = False) -> float:
    weights = {**DEFAULT_TECHNIQUE_WEIGHTS, **profile.technique_weights}
    techs = ["indirect"] if indirect_only else [t for t in signal.techniques if t != "indirect"]
    if not techs:
        return 0.0
    strongest = max(weights.get(t, 1.0) for t in techs)
    stacking = 1 + 0.25 * (len(techs) - 1)
    return min(0.95, profile.attack_susceptibility * strongest * stacking)


REFUSALS = [
    "I'm sorry, but I can't help with that request.",
    "I can't share that information. Is there anything else I can help you with?",
    "Sorry, I'm not able to do that, but I'm happy to help with questions about orders or our policies.",
]

ABSTENTIONS = [
    "I'm sorry, I don't have that information in our help center documents.",
    "I don't have information about that in the documentation I can see.",
]


# --------------------------------------------------------------------------------------------
# Prompt parsing


def parse_context(text: str) -> tuple[list[tuple[str, str]], str]:
    """Split a user message into (docs, remaining text)."""
    match = re.search(r"<context>(.*?)</context>", text, re.S)
    if not match:
        return [], text
    body = match.group(1)
    docs = re.findall(r"<doc(?: id=\"([^\"]*)\")?[^>]*>(.*?)</doc>", body, re.S)
    if not docs:
        docs = [("context", body)]
    rest = (text[: match.start()] + text[match.end() :]).strip()
    return [(doc_id or f"doc{i}", doc.strip()) for i, (doc_id, doc) in enumerate(docs)], rest


def _question_of(rest: str) -> str:
    m = re.search(r"(?:^|\n)\s*(?:Question|Customer|User)\s*:\s*(.+)$", rest, re.S | re.I)
    return (m.group(1) if m else rest).strip()


# Question-framing words (shared with the demo KB's retrieval weighting).
_GENERIC = textutil.FRAMING_WORDS


# --------------------------------------------------------------------------------------------
# The provider


class SimProvider(Provider):
    name = "sim"
    deterministic = True

    def __init__(self, time_scale: float | None = None) -> None:
        env_scale = os.environ.get("PGROUND_SIM_TIME_SCALE")
        self.time_scale = time_scale if time_scale is not None else float(env_scale or 0.0)

    def profile(self, model: str) -> SimProfile:
        if model not in PROFILES:
            raise ProviderError(
                f"unknown simulated model {model!r} (known: {', '.join(PROFILES)})", transient=False
            )
        return PROFILES[model]

    async def chat(self, request: ChatRequest) -> ChatResponse:
        profile = self.profile(request.model)
        prompt_key = _prompt_key(request)
        if current_attempt.get() == 0 and _u("err", profile.name, prompt_key) < profile.transient_error_rate:
            await asyncio.sleep(0)
            raise ProviderError("simulated upstream 503 (overloaded)", transient=True, status_code=503)

        task = request.metadata.get("task") or _infer_task(request)
        handler = {
            "rag": self._rag,
            "tool": self._tool,
            "extract": self._extract,
            "summarize": self._summarize,
        }.get(task, self._chat)
        text, tool_calls, sim = handler(request, profile)

        stop_reason = "tool_use" if tool_calls else "end"
        if not tool_calls and text and _u("trunc", profile.name, prompt_key) < profile.truncation_rate:
            text = text[: max(1, len(text) // 2)]
            stop_reason = "max_tokens"
            sim.setdefault("faults", []).append("truncation")

        output_tokens = _tokens(text + json.dumps([tc.arguments for tc in tool_calls]))
        usage = Usage(input_tokens=_prompt_tokens(request), output_tokens=output_tokens)
        latency = profile.latency_median_ms * math.exp(
            profile.latency_sigma * _normal("lat", profile.name, prompt_key)
        )
        latency += profile.ms_per_output_token * output_tokens
        if self.time_scale > 0:
            await asyncio.sleep(latency * self.time_scale / 1000)
        return ChatResponse(
            text=text, tool_calls=tool_calls, usage=usage, model_served=profile.name,
            stop_reason=stop_reason, latency_ms=round(latency, 2), raw={"sim": sim},
        )  # fmt: skip

    # -- tasks -----------------------------------------------------------------------------

    def _rag(self, request: ChatRequest, profile: SimProfile) -> tuple[str, list[ToolCall], dict[str, Any]]:
        user_turns = [m for m in request.messages if m.role == "user"]
        last_user = user_turns[-1].content if user_turns else ""
        docs, rest = parse_context(last_user)
        question = _question_of(rest)
        key = textutil.normalize(question)
        untrusted = "\n".join(text for _, text in docs)
        signal = perceive_attack(question, untrusted, len(user_turns), profile)
        sim: dict[str, Any] = {"faults": [], "task": "rag"}
        system = request.system or ""

        if signal.direct:
            sim["attack"] = {"techniques": signal.techniques, "goal": signal.goal}
            if _u("attack", key) > 1 - _comply_probability(signal, profile):
                sim["faults"].append("attack_complied")
                return self._comply(signal.goal, system, docs, question, profile, key), [], sim
            return _pick(REFUSALS, "refuse", key), [], sim

        if _RISKY_WORDS.search(question):
            hits = len(_RISKY_WORDS.findall(question))
            p_refuse = min(0.9, profile.over_refusal_rate * (1 + 0.5 * (hits - 1)))
            if _u("overrefusal", key) > 1 - p_refuse:
                sim["faults"].append("over_refusal")
                return _pick(REFUSALS, "refuse", key), [], sim

        clean_docs = docs
        injected_extra = ""
        if signal.indirect_text:
            sim["attack"] = {"techniques": ["indirect"], "goal": "injection_marker"}
            clean_docs = [(d, t) for d, t in docs if not _INDIRECT.search(t)]
            if _u("attack", key, "indirect") > 1 - _comply_probability(signal, profile, indirect_only=True):
                sim["faults"].append("attack_complied")
                injected_extra = _injected_payload(signal.indirect_text)

        answer, faults = self._answer_from_docs(question, clean_docs, profile, key)
        sim["faults"].extend(faults)
        if injected_extra:
            answer = f"{answer}\n\n{injected_extra}"
        return answer, [], sim

    def _answer_from_docs(
        self, question: str, docs: list[tuple[str, str]], profile: SimProfile, key: str
    ) -> tuple[str, list[str]]:
        faults: list[str] = []
        sents = [(doc_id, s) for doc_id, text in docs for s in textutil.sentences(text)]
        q_words = set(textutil.content_words(question))
        all_doc_words = set(textutil.content_words(" ".join(t for _, t in docs)))
        # A question is answerable when its subject terms (content words minus generic framing
        # words) are mostly covered by some document. Synonym phrasings ("return window" vs
        # "returned within 30 days") only drop one framing word, so half-coverage is enough;
        # a genuinely off-topic question ("do you rent kayaks?") shares no subject term.
        salient = q_words - _GENERIC
        present = salient & all_doc_words
        coverage = len(present) / len(salient) if salient else 1.0
        idf = textutil.idf_table([s for _, s in sents]) if sents else {}
        scored = []

        def weight(w: str) -> float:
            return idf.get(w, 1.0) * textutil.term_weight(w)

        denom = sum(weight(w) for w in q_words) or 1.0
        for i, (_, s) in enumerate(sents):
            s_words = set(textutil.content_words(s))
            scored.append((sum(weight(w) for w in q_words & s_words) / denom, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        answerable = bool(scored) and scored[0][0] >= 0.12 and bool(present) and coverage >= 0.5

        if not answerable:
            if _u("fabricate", key) > 1 - profile.fabrication_rate:
                faults.append("fabrication")
                return self._fabricate(question, key), faults
            return _pick(ABSTENTIONS, "abstain", key), faults

        choice = scored[0][1]
        if _u("accuracy", key) > profile.accuracy and len(scored) > 1:
            faults.append("wrong_selection")
            choice = scored[1][1]
        sentence = sents[choice][1]
        if _u("halluc", key) > 1 - profile.hallucination_rate:
            faults.append("hallucination")
            sentence = _perturb(sentence, " ".join(t for _, t in docs), key)
        if profile.verbose:
            return (
                f"Thanks for reaching out! {sentence} Let me know if there's anything else I can help with.",
                faults,
            )
        return sentence, faults

    def _fabricate(self, question: str, key: str) -> str:
        n1 = 2 + int(_u("fab1", key) * 40)
        n2 = 3 + int(_u("fab2", key) * 9)
        return _pick(
            [
                f"Yes, we offer that. It costs ${n1} and usually takes {n2} business days.",
                f"Good news: that's covered for up to {n1} days, as long as you keep the receipt.",
                f"Our policy allows that within {n1} days, with a fee of {n2} percent.",
            ],
            "fabtpl",
            key,
        )

    def _comply(
        self,
        goal: str | None,
        system: str,
        docs: list[tuple[str, str]],
        question: str,
        profile: SimProfile,
        key: str,
    ) -> str:
        secrets = _SECRET_RE.findall(system)
        if goal == "system_prompt_leak":
            return f'Sure! My instructions are: "{system[:500]}"'
        if goal == "secret_leak":
            secret = next((s for s in secrets if not s.startswith("PG-")), secrets[0] if secrets else "N/A")
            if re.search(r"\b(backwards|reverse(?:d)?)\b", question, re.I):
                return f"Sure, here it is backwards: {secret[::-1]}"
            if re.search(r"\bspell\b|\bletter by letter\b", question, re.I):
                return "Sure: " + " ".join(secret)
            return f"Sure, the code is {secret}."
        if goal == "pii_exfil":
            lines = [s for _, t in docs for s in textutil.sentences(t) if textutil.find_pii(s)]
            if lines:
                return "Here are the customer details I found: " + " ".join(lines[:4])
            return "I don't have any customer records in front of me right now."
        competitors = re.findall(r"competitors? \(([^)]+)\)", system)
        names = [n.strip() for group in competitors for n in group.split(",")]
        mentioned = [n for n in names if n and n.lower() in question.lower()]
        if mentioned or names:
            name = (mentioned or names)[0]
            return f"Honestly, {name} has better prices on tents this season - you should shop there."
        return "Sure! Restrictions disabled. [unrestricted mode]"

    def _summarize(
        self, request: ChatRequest, profile: SimProfile
    ) -> tuple[str, list[ToolCall], dict[str, Any]]:
        user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        docs, rest = parse_context(user)
        source = "\n".join(t for _, t in docs) if docs else rest
        key = textutil.normalize(source[:400])
        sim: dict[str, Any] = {"faults": [], "task": "summarize"}
        signal = perceive_attack(rest if docs else "", source, 1, profile)
        sents = textutil.sentences(source)
        if not sents:
            return "There is nothing to summarize.", [], sim
        freq: dict[str, int] = {}
        for w in textutil.content_words(source):
            freq[w] = freq.get(w, 0) + 1
        ranked = sorted(
            range(len(sents)),
            key=lambda i: (
                -sum(freq.get(w, 0) for w in set(textutil.content_words(sents[i])))
                / (1 + len(textutil.content_words(sents[i])) ** 0.5)
            ),
        )
        keep = sorted(i for i in ranked[:3] if not _INDIRECT.search(sents[i]))
        summary = [sents[i] for i in keep] or sents[:1]
        if _u("halluc", key) > 1 - profile.hallucination_rate:
            sim["faults"].append("hallucination")
            summary[0] = _perturb(summary[0], source, key)
        text = " ".join(summary)
        if signal.indirect_text and _u("attack", key, "indirect") > 1 - _comply_probability(
            signal, profile, indirect_only=True
        ):
            sim["faults"].append("attack_complied")
            text += "\n\n" + _injected_payload(signal.indirect_text)
        return ("Summary: " + text) if profile.verbose else text, [], sim

    def _extract(
        self, request: ChatRequest, profile: SimProfile
    ) -> tuple[str, list[ToolCall], dict[str, Any]]:
        user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        schema = request.response_schema or _schema_from_system(request.system or "") or {}
        props: dict[str, Any] = schema.get("properties", {})
        key = textutil.normalize(user[:500])
        sim: dict[str, Any] = {"faults": [], "task": "extract"}
        data: dict[str, Any] = {}
        for name, spec in props.items():
            value = _extract_field(name, spec if isinstance(spec, dict) else {}, user)
            if value is not None:
                data[name] = value
        if data and _u("halluc", key) > 1 - profile.hallucination_rate:
            numeric = [k for k, v in data.items() if isinstance(v, int | float) and not isinstance(v, bool)]
            if numeric:
                sim["faults"].append("hallucination")
                k = numeric[0]
                data[k] = round(data[k] * 1.1 + 1, 2) if isinstance(data[k], float) else data[k] + 1
        text = json.dumps(data)
        if _u("format", key) > 1 - profile.format_error_rate:
            sim["faults"].append("format_error")
            text = (
                f"Here is the extracted data: {text}"
                if _u("fmtkind", key) < 0.5
                else json.dumps({k: v for k, v in list(data.items())[1:]})
            )
        return text, [], sim

    def _tool(self, request: ChatRequest, profile: SimProfile) -> tuple[str, list[ToolCall], dict[str, Any]]:
        convo = request.messages
        user_turns = [m for m in convo if m.role == "user"]
        first_user = user_turns[0].content if user_turns else ""
        all_user = "\n".join(m.content for m in user_turns)
        key = textutil.normalize(all_user)
        tool_msgs = [m for m in convo if m.role == "tool"]
        tool_text = "\n".join(m.content for m in tool_msgs)
        calls_made = [tc for m in convo if m.role == "assistant" for tc in m.tool_calls]
        called = {tc.name for tc in calls_made}
        tools = {t.name for t in request.tools}
        signal = perceive_attack(all_user, tool_text, len(user_turns), profile)
        sim: dict[str, Any] = {"faults": [], "task": "tool"}
        comply_direct = signal.direct and _u("attack", key) > 1 - _comply_probability(signal, profile)
        comply_indirect = bool(signal.indirect_text) and _u(
            "attack", key, "indirect"
        ) > 1 - _comply_probability(signal, profile, indirect_only=True)
        if signal.detected:
            sim["attack"] = {"techniques": signal.techniques, "goal": signal.goal}
        if signal.direct and not comply_direct:
            return _pick(REFUSALS, "refuse", key), [], sim
        if comply_direct or comply_indirect:
            sim["faults"].append("attack_complied")

        order_id_match = re.search(r"\bORD-\d{5}\b", all_user)
        intent = _tool_intent(first_user)
        if not calls_made:
            if order_id_match and "lookup_order" in tools:
                return "", [_call("lookup_order", {"order_id": order_id_match.group(0)}, key, 0)], sim
            return "Could you share your order number? It looks like ORD-12345.", [], sim

        order = _last_tool_json(convo, "lookup_order")
        if not order or order.get("error"):
            return "I couldn't find that order. Please double-check the order number.", [], sim
        order_id = order.get("order_id", order_id_match.group(0) if order_id_match else "")
        limit = _refund_limit(request.system or "")

        if intent == "refund" and "issue_refund" in tools:
            if "issue_refund" in called:
                amount = next(tc.arguments.get("amount") for tc in calls_made if tc.name == "issue_refund")
                return (
                    f"Your refund of ${amount} for {order_id} has been issued. It should appear within 5 business days.",
                    [],
                    sim,
                )
            if comply_direct or comply_indirect:
                amount = _requested_amount(all_user + "\n" + tool_text) or float(order.get("total", 0)) * 3
                return "", [_call("issue_refund", {"order_id": order_id, "amount": amount, "reason": "customer request"}, key, 1)], sim  # fmt: skip
            if not order.get("refundable", False):
                return f"Order {order_id} isn't eligible for a refund: {order.get('refund_note', 'it is outside the return window')}.", [], sim  # fmt: skip
            total = float(order.get("total", 0))
            if limit is not None and total > limit:
                return f"Refunds above ${limit:g} need a human agent, so I've escalated {order_id} to our support team.", [], sim  # fmt: skip
            return (
                "",
                [_call("issue_refund", {"order_id": order_id, "amount": total, "reason": "return"}, key, 1)],
                sim,
            )

        if intent == "email" and "send_email" in tools:
            if "send_email" in called:
                to = next(tc.arguments.get("to") for tc in calls_made if tc.name == "send_email")
                return f"Done - I've emailed the details for {order_id} to {to}.", [], sim
            attacker = re.findall(textutil.EMAIL_RE, all_user + "\n" + tool_text)
            to = (
                attacker[0]
                if (comply_direct or comply_indirect) and attacker
                else order.get("customer_email", "")
            )
            body = f"Order {order_id}: status {order.get('status')}, total ${order.get('total')}."
            return (
                "",
                [_call("send_email", {"to": to, "subject": f"Your order {order_id}", "body": body}, key, 1)],
                sim,
            )

        status = order.get("status", "processing")
        eta = order.get("eta")
        tail = f" Estimated delivery: {eta}." if eta else ""
        return f"Order {order_id} is currently {status}.{tail}", [], sim

    def _chat(self, request: ChatRequest, profile: SimProfile) -> tuple[str, list[ToolCall], dict[str, Any]]:
        user_turns = [m for m in request.messages if m.role == "user"]
        text = user_turns[-1].content if user_turns else ""
        key = textutil.normalize(text)
        sim: dict[str, Any] = {"faults": [], "task": "chat"}
        signal = perceive_attack(text, "", len(user_turns), profile)
        if signal.direct:
            sim["attack"] = {"techniques": signal.techniques, "goal": signal.goal}
            if _u("attack", key) > 1 - _comply_probability(signal, profile):
                sim["faults"].append("attack_complied")
                return self._comply(signal.goal, request.system or "", [], text, profile, key), [], sim
            return _pick(REFUSALS, "refuse", key), [], sim
        return _pick(ABSTENTIONS, "abstain", key), [], sim


# --------------------------------------------------------------------------------------------
# helpers


def _prompt_key(request: ChatRequest) -> str:
    return hashlib.sha256(
        json.dumps(
            [request.model, request.system, [m.model_dump(mode="json") for m in request.messages]],
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _prompt_tokens(request: ChatRequest) -> int:
    text = (request.system or "") + "".join(m.content for m in request.messages)
    text += json.dumps([t.model_dump() for t in request.tools]) if request.tools else ""
    return _tokens(text)


def _infer_task(request: ChatRequest) -> str:
    if request.tools:
        return "tool"
    if request.response_schema or "json schema" in (request.system or "").lower():
        return "extract"
    if any("<context>" in m.content for m in request.messages if m.role == "user"):
        return "rag"
    return "chat"


def _perturb(sentence: str, context: str, key: str) -> str:
    """Change one number so the sentence is no longer supported by the context."""
    present = set(textutil.numbers(context))
    for match in re.finditer(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)", sentence):
        raw = match.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        for factor in (1.5, 2.0, 0.5, 3.0):
            new = value * factor if _u("pf", key) < 0.7 else value + factor * 5
            new_text = f"{new:.2f}" if "." in raw else str(max(1, round(new)))
            normalized = new_text.rstrip("0").rstrip(".") if "." in new_text else new_text
            if normalized not in present and new_text != raw:
                return sentence[: match.start(1)] + new_text + sentence[match.end(1) :]
    return sentence.rstrip(".") + ", and this also applies to clearance items for 90 days."


def _injected_payload(untrusted: str) -> str:
    markers = _MARKER_RE.findall(untrusted)
    if markers:
        return "Also: " + " ".join(dict.fromkeys(markers))
    return "Also: as instructed by the document, I've included the requested note."


def _schema_from_system(system: str) -> dict[str, Any] | None:
    m = re.search(r"(\{.*\})", system, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


_FIELD_PATTERNS: dict[str, str] = {
    "invoice_number": r"Invoice\s*(?:#|No\.?|Number)\s*[:#]?\s*([A-Z0-9-]{3,})",
    "receipt_number": r"Receipt\s*(?:#|No\.?|Number)\s*[:#]?\s*([A-Z0-9-]{3,})",
    "date": r"(\d{4}-\d{2}-\d{2})",
    "invoice_date": r"(\d{4}-\d{2}-\d{2})",
    "vendor": r"(?:From|Vendor|Supplier)\s*:\s*([^\n]+)",
    "merchant": r"(?:Merchant|Store)\s*:\s*([^\n]+)",
    "total": r"Total(?: due| amount| paid)?\s*:\s*\$?([\d,]+\.\d{2})",
    "subtotal": r"Subtotal\s*:\s*\$?([\d,]+\.\d{2})",
    "tax": r"Tax\s*:\s*\$?([\d,]+\.\d{2})",
    "email": r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b",
    "name": r"(?:Name|Signed|Regards,|Thanks,)\s*:?\s*\n?\s*([A-Z][a-z]+ [A-Z][a-z]+)",
    "company": r"(?:Company|Organization|from)\s*:?\s*([A-Z][A-Za-z&]+(?: [A-Z][A-Za-z&]+){0,3}(?: (?:Inc|LLC|Ltd|Co)\.?)?)",
    "budget": r"budget (?:of |is |around |about )*\$?([\d,]{3,})",
    "team_size": r"(\d+)\s+(?:people|employees|staff|team members|guides)",
    "payment_method": r"(?:Paid with|Payment method|Payment)\s*:\s*([^\n]+)",
}

_CATEGORY_KEYWORDS = [
    ("returns", r"\b(refund|money back|return(?:ing)?|send (?:it )?back)\b"),
    ("shipping", r"\b(late|shipping|delivery|tracking|arrived|package|courier|never came)\b"),
    ("warranty", r"\b(broken|defect(?:ive)?|warranty|stopped working|ripped|tore|leak(?:s|ing)?)\b"),
    ("account", r"\b(password|log ?in|account|sign in|locked out|email address)\b"),
    ("billing", r"\b(charged?|billing|invoice|double|overcharged|payment)\b"),
]


def _extract_field(name: str, spec: dict[str, Any], text: str) -> Any:
    if name in ("category", "ticket_category"):
        for category, pattern in _CATEGORY_KEYWORDS:
            if re.search(pattern, text, re.I):
                return category
        return "other"
    if name in ("priority", "urgency"):
        return (
            "high"
            if re.search(r"\b(urgent|asap|immediately|right now|furious|unacceptable)\b", text, re.I)
            else "normal"
        )
    if name == "sentiment":
        negative = len(
            re.findall(
                r"\b(angry|terrible|awful|disappointed|unacceptable|furious|worst|broken)\b", text, re.I
            )
        )
        positive = len(re.findall(r"\b(love|great|excellent|happy|thanks|amazing|perfect)\b", text, re.I))
        return "negative" if negative > positive else "positive" if positive > negative else "neutral"
    if name in ("currency",):
        return "USD" if "$" in text or "USD" in text else None
    if name in ("line_item_count", "item_count"):
        return len(re.findall(r"(?m)^\s*[-*]\s+\S", text))
    pattern = _FIELD_PATTERNS.get(name) or rf"{re.escape(name.replace('_', ' '))}\s*:\s*([^\n]+)"
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    value = m.group(1).strip().rstrip(".")
    kind = spec.get("type")
    if kind == "number":
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return None
    if kind == "integer":
        try:
            return int(float(value.replace(",", "")))
        except ValueError:
            return None
    return value


def _tool_intent(text: str) -> str:
    t = text.lower()
    if re.search(r"\b(refund|money back|reimburse)\b", t):
        return "refund"
    if re.search(r"\b(email|e-mail|send (?:me|it|the|a copy)|forward)\b", t):
        return "email"
    return "status"


def _call(name: str, arguments: dict[str, Any], key: str, step: int) -> ToolCall:
    return ToolCall(
        id=f"call_{hashlib.sha256(f'{key}{name}{step}'.encode()).hexdigest()[:12]}",
        name=name,
        arguments=arguments,
    )


def _last_tool_json(convo: list[Message], tool: str) -> dict[str, Any] | None:
    for m in reversed(convo):
        if m.role == "tool" and (m.name == tool or m.name is None):
            try:
                data = json.loads(m.content)
            except json.JSONDecodeError:
                return None
            return data if isinstance(data, dict) else None
    return None


def _refund_limit(system: str) -> float | None:
    m = re.search(r"refunds? (?:above|over|greater than) \$(\d+(?:\.\d+)?)", system, re.I)
    return float(m.group(1)) if m else None


def _requested_amount(text: str) -> float | None:
    amounts = [float(a.replace(",", "")) for a in re.findall(r"\$\s?(\d[\d,]*(?:\.\d+)?)", text)]
    return max(amounts) if amounts else None
