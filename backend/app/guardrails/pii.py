"""Redact personal data from user questions before they reach the LLM, logs or the database.

Deliberately small (regexes + a Luhn check): research questions about public filings rarely
contain PII, so this catches accidental pastes without pulling in an NLP stack.
"""

import re
from dataclasses import dataclass

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("CARD", re.compile(r"\b\d(?:[ -]?\d){12,18}\b")),
    ("PHONE", re.compile(r"(?<!\w)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b")),
]


def _luhn_ok(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        checksum += d
    return len(digits) >= 13 and checksum % 10 == 0


@dataclass
class Redaction:
    text: str
    kinds: list[str]  # e.g. ["EMAIL", "CARD"]; empty when nothing was redacted


def redact(text: str) -> Redaction:
    kinds: list[str] = []
    for kind, pattern in _PATTERNS:

        def sub(m: re.Match[str], kind: str = kind) -> str:
            if kind == "CARD" and not _luhn_ok(m.group()):
                return m.group()  # long numbers in finance are usually figures, not cards
            kinds.append(kind)
            return f"[{kind} REDACTED]"

        text = pattern.sub(sub, text)
    return Redaction(text=text, kinds=sorted(set(kinds)))
