"""Understand an agent's finished turn without a model call.

`analyse()` turns one final message into a `Digest`:
- what kind of message it is: report, question, short reply or progress;
- its claims, sorted into reported results, failures, open work, proposals,
  discoveries and decisions;
- PR, issue and commit references, and test counts.

It is deterministic, cheap enough to run on every turn, and never invents anything:
every item is a line from the message. Everything stays attributed to the agent that wrote
it. EchoSpeak never upgrades a reported result to "verified"; that takes independent
evidence (V2+).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

CATEGORIES = ("results", "failures", "open", "proposals", "discoveries", "decisions")

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("failures", re.compile(r"\b(fail(ed|s|ing|ure)?|regress(ed|ion)(?!\s+(tests?|suites?|checks?))|didn'?t work|did not work|broke|broken|reverted|rejected|"
                            r"couldn'?t|could not|error(s)? (remain|persist)|worse)\b", re.I)),
    ("open", re.compile(r"\b(not (yet )?(verified|tested|done|run)|pending|todo|remaining|still (open|needs|to)|limitation|"
                        r"blocked|blocker|follow[- ]?up|next steps?|needs? (owner|approval|review)|unverified|not covered|left to)\b", re.I)),
    ("proposals", re.compile(r"\b(i (propose|recommend|suggest)|we (could|should)|recommend(ation)?|option [a-z0-9]|consider|proposal)\b", re.I)),
    ("discoveries", re.compile(r"\b(found|discover(ed|y)|root cause|the cause|turns out|learned|it appears|because)\b", re.I)),
    ("decisions", re.compile(r"\b(decided|decision|chose|chosen|kept|dropped|switched to|went with)\b", re.I)),
    ("results", re.compile(r"\b(\d+\s*(/\s*\d+\s*)?(passed|passing|pass)|all (tests|checks) pass|passes|passed|green|verified|"
                           r"confirmed|measured|merged|installed|fixed|implemented|added|created|shipped|works|working|done|"
                           r"completed|opened (pr|pull)|pushed)\b", re.I)),
]
_INJECTION = re.compile(
    r"\b(ignore (all |any |the )?(previous|prior|above|earlier) (instructions|messages|rules)|"
    r"disregard (all |the )?(previous|prior|above|earlier|your)|system prompt|you are now|new instructions|"
    r"from now on,? (you|always|never|echo)|(you|echo) (must|should) always|always (run|execute)\b|"
    r"never ask (the (user|owner)|for (permission|approval))|"
    r"override (your|the|all|any) (instructions|rules|safety|permissions|policy))",
    re.I,
)
_REPORT_HEAD = re.compile(r"^\s*(#{1,4}\s|\*\*[^*]{2,60}\*\*\s*$|[A-Z][A-Za-z /&-]{2,40}:\s*$)", re.M)
_REPORT_WORDS = re.compile(r"\b(summary|what changed|changes|verification|evidence|results?|tests?|limitations?|"
                           r"next steps?|remaining|report|outcome|acceptance)\b", re.I)
_PR = re.compile(r"(?:\bPR\s*#|pull/|\bpull request\s*#?)(\d{1,6})", re.I)
_ISSUE = re.compile(r"(?:\bissue\s*#|issues/|(?<![\w/])#)(\d{1,6})\b", re.I)
_SHA = re.compile(r"\b([0-9a-f]{7,12})\b")
_TESTS = re.compile(r"\b(\d+)\s+passed\b(?:[^\n]{0,40}?\b(\d+)\s+failed\b)?", re.I)


@dataclass
class Digest:
    kind: str = "short_reply"           # report | question | short_reply | progress
    headline: str = ""
    results: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    open: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    discoveries: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    prs: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    tests_passed: int = 0
    tests_failed: int = 0
    asks_owner: bool = False
    dropped_lines: int = 0               # instruction-like lines that were not kept
    evidence: str = "reported"           # never "verified": the agent said so, EchoSpeak didn't check

    def public(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def outcome(self) -> str:
        if self.failures and not self.results:
            return "failed"
        if self.failures or self.open:
            return "mixed"
        if self.results:
            return "success"
        return "neutral"


_SECTIONS: list[tuple[str, re.Pattern[str]]] = [
    ("open", re.compile(r"not verified|limits?|limitations?|remaining|open (items|questions)|next|todo|blockers?|follow|risks?|unverified", re.I)),
    ("failures", re.compile(r"fail|regress|rejected|didn'?t work|what broke", re.I)),
    ("proposals", re.compile(r"propos|recommend|options?|suggest", re.I)),
    ("discoveries", re.compile(r"cause|findings?|discover|learn|insights?|why", re.I)),
    ("decisions", re.compile(r"decisions?|chose|design", re.I)),
    ("results", re.compile(r"evidence|results?|verification|verified|tests?|what changed|changes?|done|accomplish|outcome|measure", re.I)),
]
_NEGATED = re.compile(r"\b(never|no|not|nothing|without|zero|0)\s+(\w+\s+){0,2}$", re.I)


def _section(heading: str) -> str:
    for category, rule in _SECTIONS:
        if rule.search(heading):
            return category
    return ""


def _lines(text: str) -> list[tuple[str, str]]:
    """(section category or "", line) for each meaningful sentence."""

    body = re.sub(r"```.*?```", " ", text, flags=re.S)
    out: list[tuple[str, str]] = []
    section = ""
    for raw in body.splitlines():
        stripped = raw.strip()
        heading = re.match(r"^(#{1,6}\s+(.+)|\*\*([^*]{2,60})\*\*:?\s*$|([A-Z][A-Za-z /&-]{2,40}):\s*$)", stripped)
        if heading:
            section = _section(next(g for g in heading.groups()[1:] if g))
            continue
        line = re.sub(r"^\s*([-*+•]|\d+[.)]|>)\s*", "", raw).strip()
        line = re.sub(r"\*\*|`", "", line).strip("* ")
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        if len(line) < 4 or re.fullmatch(r"[|:\- ]+", line):
            continue
        if line.count("|") >= 2:
            line = " · ".join(cell.strip() for cell in line.strip("|").split("|") if cell.strip())
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line):
            if len(sentence) >= 4:
                out.append((section, sentence[:300]))
    return out


def _match(category: str, rule: re.Pattern[str], line: str) -> bool:
    for found in rule.finditer(line):
        if category == "failures" and (_NEGATED.search(line[:found.start()]) or re.match(r"0\s+failed", line[found.start() - 2:] or "")):
            continue
        return True
    return False


def analyse(text: str) -> Digest:
    message = str(text or "").strip()
    digest = Digest()
    if not message:
        return digest
    heads = len(_REPORT_HEAD.findall(message))
    words = len(_REPORT_WORDS.findall(message))
    bullets = len(re.findall(r"^\s*([-*+•]|\d+[.)])\s", message, re.M))
    tail = message.rstrip()[-300:]
    digest.asks_owner = tail.endswith("?") or bool(re.search(r"\b(should i|do you want|would you like|shall i|want me to|"
                                                              r"your (call|decision|approval)|let me know)\b", tail, re.I))
    if (len(message) >= 500 and (heads >= 2 or (bullets >= 4 and words >= 2) or words >= 5)) \
            or (len(message) >= 120 and heads >= 3 and words >= 2):  # a concise but structured report
        digest.kind = "report"
    elif digest.asks_owner:
        digest.kind = "question"
    elif len(message) < 400:
        digest.kind = "short_reply"
    else:
        digest.kind = "progress"
    lines = _lines(message)
    digest.headline = next((l for _, l in lines if not _INJECTION.search(l)), "")[:200]
    seen: set[str] = set()
    for section, line in lines:
        if _INJECTION.search(line):
            digest.dropped_lines += 1
            continue
        key = line.lower()
        if key in seen:
            continue
        category = ""
        if _match("failures", _RULES[0][1], line):
            category = "failures"
        elif section:
            category = section
        else:
            category = next((name for name, rule in _RULES[1:] if _match(name, rule, line)), "")
        if category:
            bucket = getattr(digest, category)
            if len(bucket) < 8:
                bucket.append(line)
            seen.add(key)
    digest.prs = sorted(set(_PR.findall(message)), key=int)[:10]
    digest.issues = sorted(set(_ISSUE.findall(message)) - set(digest.prs), key=int)[:10]
    digest.commits = sorted({sha for sha in _SHA.findall(message) if re.search(r"[a-f]", sha) and re.search(r"\d", sha)})[:10]
    counts = _TESTS.findall(message)  # the largest mention; summing would double count repeats
    digest.tests_passed = max((int(p) for p, _ in counts), default=0)
    digest.tests_failed = max((int(f or 0) for _, f in counts), default=0)
    return digest
