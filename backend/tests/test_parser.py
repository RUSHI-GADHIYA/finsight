from app.rag.parser import html_to_text, parse_filing, split_sections

BODY = "The company faces significant risk from supply constraints. " * 20
MDNA = "Revenue grew driven by data center demand and pricing. " * 20


def _filing_html() -> str:
    return f"""
    <html><body>
      <ix:header><p>hidden xbrl metadata</p></ix:header>
      <p>TABLE OF CONTENTS</p>
      <p>Item 1A. Risk Factors</p><p>12</p>
      <p>Item 7. Management's Discussion and Analysis</p><p>40</p>
      <p>Item 1A.</p><p>Risk Factors</p>
      <p>{BODY}</p>
      <p>Item 1B. Unresolved Staff Comments</p><p>None.</p>
      <p>Item 7. Management&#8217;s Discussion and Analysis of Financial Condition</p>
      <p>{MDNA}</p>
      <p>Item 8. Financial Statements</p><p>...</p>
    </body></html>
    """


def test_html_to_text_strips_hidden_xbrl_header() -> None:
    assert "hidden xbrl" not in html_to_text(_filing_html())


def test_parse_filing_skips_table_of_contents_and_finds_bodies() -> None:
    sections = {s.key: s.text for s in parse_filing(_filing_html())}

    assert set(sections) == {"risk_factors", "mdna"}
    assert "supply constraints" in sections["risk_factors"]
    assert "Unresolved Staff" not in sections["risk_factors"]
    assert "data center demand" in sections["mdna"]
    assert "Financial Statements" not in sections["mdna"]


def test_split_sections_ignores_short_cross_references() -> None:
    text = "Item 1A. Risk Factors\nSee below.\n\nItem 2. Properties\nOffices."
    assert split_sections(text) == []


def test_inline_spans_stay_on_one_line() -> None:
    html = "<p><span>ITEM 1A. RIS</span><span>K FACTORS</span></p><p>Body</p>"
    assert html_to_text(html).splitlines()[0] == "ITEM 1A. RISK FACTORS"


def test_running_page_headers_do_not_split_sections() -> None:
    page = "Supply chain disruption could hurt margins. " * 15
    text = (
        "Item 1A. Risk Factors\n"
        + "".join(f"{page}\nItem 1A\n{page}\n" for _ in range(3))  # header on every page
        + "Item 1B. Unresolved Staff Comments\nNone."
    )
    sections = split_sections(text)

    assert [s.key for s in sections] == ["risk_factors"]
    assert sections[0].text.count("Supply chain") == 6 * 15


def test_falls_back_to_standalone_titles_without_item_headings() -> None:
    text = (
        "Management's Discussion and Analysis\n" + "Revenue rose. " * 60 + "\n"
        "Business Combinations\nWe acquired a company.\n"  # subheading, not the Business item
        "Risk Factors\n" + "Competition is intense. " * 40 + "\n"
        "Risk Factors\n" + "More risk text. " * 40 + "\n"  # running header repeat
        "Properties\nOffices."
    )
    sections = {s.key: s.text for s in split_sections(text)}

    assert set(sections) == {"mdna", "risk_factors"}
    assert "Business Combinations" in sections["mdna"]
    assert "More risk text" in sections["risk_factors"]
    assert "Offices" not in sections["risk_factors"]
