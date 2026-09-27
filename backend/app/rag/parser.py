"""Turn a 10-K/10-Q HTML document into named sections.

Most filings use headings like "Item 1A. Risk Factors" (sometimes with the title on the next
line, in table layouts). Complications handled here:
- every heading also appears in the table of contents -> keep the longest span per section;
- many filings repeat a running page header ("Item 1A", "Risk Factors") on every page ->
  only headings whose title is a known item title count, and repeats of the same section merge;
- some filers (e.g. Intel) use no "Item N." headings at all -> fall back to standalone title lines.
"""

import re
import warnings
from dataclasses import dataclass

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from bs4.element import NavigableString

_ITEM_HEADING = re.compile(
    r"^[ \t]*item[ \t]+(\d{1,2}[a-c]?)\b[ \t]*[.:\-–—]?[ \t]*(.*)$", re.I | re.M
)
_LINE = re.compile(r"^[ \t]*(\S[^\n]*?)[ \t]*$", re.M)

# section key -> pattern matched against the heading title (form-agnostic: works for 10-K and 10-Q)
SECTION_TITLES: dict[str, re.Pattern[str]] = {
    "business": re.compile(r"^business\b", re.I),
    "risk_factors": re.compile(r"^risk\s+factors", re.I),
    "mdna": re.compile(r"^management[’'`]?s\s+discussion", re.I),
    "market_risk": re.compile(r"^quantitative\s+and\s+qualitative", re.I),
    "legal_proceedings": re.compile(r"^legal\s+proceedings", re.I),
}
# Other 10-K/10-Q item titles: not extracted, but they end the preceding section.
_OTHER_ITEM_TITLES = re.compile(
    r"^(unresolved\s+staff|cybersecurity|properties|mine\s+safety|market\s+for|\[?reserved"
    r"|selected\s+financial|financial\s+statements|changes\s+in\s+and\s+disagreements"
    r"|controls\s+and\s+procedures|other\s+information|disclosure\s+regarding\s+foreign"
    r"|directors|executive\s+compensation|security\s+ownership|certain\s+relationships"
    r"|principal\s+account|exhibits|form\s+10-k\s+summary|unregistered\s+sales|defaults\s+upon)",
    re.I,
)
OTHER = "_other"
_STANDALONE_BUSINESS = re.compile(r"business(\s+overview)?", re.I)

MIN_SECTION_CHARS = 500  # shorter spans are TOC entries or cross-references
MAX_STANDALONE_TITLE_CHARS = 60

_BLOCK_TAGS = ["p", "div", "tr", "li", "table", "section", "h1", "h2", "h3", "h4", "h5", "h6"]


@dataclass(frozen=True)
class Section:
    key: str
    text: str


def html_to_text(html: str) -> str:
    # EDGAR filings are inline-XBRL XHTML; the lenient HTML parser handles them better than
    # the XML parser (which chokes on stray entities), so the warning is expected.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "ix:header"]):
        tag.decompose()
    # Newlines only between blocks: inline spans (e.g. "RIS" + "K FACTORS") must stay joined.
    for br in soup("br"):
        br.replace_with(NavigableString("\n"))
    for tag in soup(_BLOCK_TAGS):
        tag.insert_after(NavigableString("\n"))
    for cell in soup(["td", "th"]):
        cell.insert_after(NavigableString(" "))

    text = soup.get_text()
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()


def _classify(title: str) -> str | None:
    for key, pattern in SECTION_TITLES.items():
        if pattern.match(title):
            return key
    return OTHER if _OTHER_ITEM_TITLES.match(title) else None


def _heading_title(text: str, match: re.Match[str]) -> str:
    title = match.group(2).strip()
    if title:
        return title
    # Title on the following non-empty line.
    rest = text[match.end() :].lstrip()
    return rest.split("\n", 1)[0].strip()


def _item_headings(text: str) -> list[tuple[int, str]]:
    events = []
    for match in _ITEM_HEADING.finditer(text):
        key = _classify(_heading_title(text, match))
        if key is not None:
            events.append((match.start(), key))
    return events


def _standalone_headings(text: str) -> list[tuple[int, str]]:
    events = []
    for match in _LINE.finditer(text):
        line = match.group(1)
        if len(line) > MAX_STANDALONE_TITLE_CHARS or line.endswith((".", ",", ";")):
            continue
        key = _classify(line)
        # A bare "Business..." line is too ambiguous ("Business Combinations"); require the
        # whole line to be the heading.
        if key == "business" and not _STANDALONE_BUSINESS.fullmatch(line):
            continue
        if key is not None:
            events.append((match.start(), key))
    return events


def _sections_from(text: str, events: list[tuple[int, str]]) -> dict[str, str]:
    # Collapse runs of the same section (running page headers) into their first occurrence.
    merged: list[tuple[int, str]] = []
    for pos, key in events:
        if not merged or merged[-1][1] != key:
            merged.append((pos, key))

    best: dict[str, str] = {}
    for i, (start, key) in enumerate(merged):
        if key == OTHER:
            continue
        end = merged[i + 1][0] if i + 1 < len(merged) else len(text)
        body = text[start:end].strip()
        if len(body) >= MIN_SECTION_CHARS and len(body) > len(best.get(key, "")):
            best[key] = body
    return best


def split_sections(text: str) -> list[Section]:
    best = _sections_from(text, _item_headings(text))
    if len(best) < 2:
        fallback = _sections_from(text, _standalone_headings(text))
        if len(fallback) > len(best):
            best = fallback
    return [Section(key, body) for key, body in best.items()]


def parse_filing(html: str) -> list[Section]:
    return split_sections(html_to_text(html))
