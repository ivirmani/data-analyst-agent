"""Collects findings while the agent works, then renders them into one HTML page.

Every run_python call happens in a fresh process, so findings cannot be kept in
a variable between steps. Instead each one is appended to a small JSON file on
disk, and a final step turns that file into the finished report.
"""

import base64
import html
import json
import os
from datetime import datetime

# Where findings are collected before the report is built.
DRAFT_FILE = ".report_draft.json"


def clear():
    """Throw away any findings left over from a previous report."""
    if os.path.exists(DRAFT_FILE):
        os.remove(DRAFT_FILE)


def _load():
    """Read the findings recorded so far. Returns an empty list if there are none."""
    if not os.path.exists(DRAFT_FILE):
        return []
    with open(DRAFT_FILE) as f:
        return json.load(f)


def add_finding(headline, detail="", chart=None, table=None):
    """Record one finding for the report.

    headline: a short sentence stating what you found, with the number in it.
    detail:   an optional sentence or two of context.
    chart:    an optional path to a PNG you already saved.
    table:    an optional table, as HTML from df.to_html(index=False).
    """
    findings = _load()
    findings.append(
        {"headline": headline, "detail": detail, "chart": chart, "table": table}
    )

    with open(DRAFT_FILE, "w") as f:
        json.dump(findings, f, indent=2)

    print(f"Recorded finding {len(findings)}: {headline}")


def _embed_image(path):
    """Turn a PNG file into text that can live inside the HTML itself.

    base64 encodes binary data as plain characters, so the picture travels
    inside the page. That makes the report a single file you can email.
    """
    if not path or not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()
    return f"data:image/png;base64,{encoded}"


STYLE = """
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         line-height: 1.6; color: #1a1a1a; background: #f6f6f4;
         margin: 0; padding: 40px 20px; }
  .page { max-width: 780px; margin: 0 auto; background: #fff; padding: 48px;
          border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  h1 { font-size: 28px; line-height: 1.25; margin: 0 0 8px; }
  .meta { color: #6b6b6b; font-size: 14px; margin-bottom: 40px;
          padding-bottom: 20px; border-bottom: 1px solid #e5e5e5; }
  .finding { margin-bottom: 44px; }
  h2 { font-size: 19px; line-height: 1.35; margin: 0 0 8px; }
  .detail { color: #444; margin: 0 0 16px; }
  img { width: 100%; border: 1px solid #e5e5e5; border-radius: 4px; }
  table { border-collapse: collapse; font-size: 14px; margin-top: 16px; }
  th, td { text-align: left; padding: 7px 14px 7px 0; border-bottom: 1px solid #ececec; }
  th { font-weight: 600; color: #6b6b6b; }
  .footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid #e5e5e5;
            color: #8a8a8a; font-size: 13px; }
"""


def build(title, output="report.html", source=""):
    """Render every recorded finding into one self-contained HTML file."""
    findings = _load()

    if not findings:
        print("No findings were recorded, so there is nothing to build.")
        return

    parts = []

    for finding in findings:
        # escape() turns characters like < and & into something safe to display,
        # so a stray symbol in the text cannot break the page.
        block = ["<div class='finding'>"]
        block.append(f"<h2>{html.escape(finding['headline'])}</h2>")

        if finding.get("detail"):
            block.append(f"<p class='detail'>{html.escape(finding['detail'])}</p>")

        image = _embed_image(finding.get("chart"))
        if image:
            block.append(f"<img src='{image}' alt='{html.escape(finding['headline'])}'>")

        # The table is HTML produced by pandas, so it is inserted as-is.
        if finding.get("table"):
            block.append(finding["table"])

        block.append("</div>")
        parts.append("\n".join(block))

    generated = datetime.now().strftime("%d %B %Y at %H:%M")
    source_line = f"Source: {html.escape(source)} &middot; " if source else ""

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{STYLE}</style>
</head>
<body>
  <div class="page">
    <h1>{html.escape(title)}</h1>
    <div class="meta">{source_line}Generated {generated}</div>
    {"".join(parts)}
    <div class="footer">
      Produced by Data Analyst Agent. Every figure was computed from the source
      data by code, not written from memory.
    </div>
  </div>
</body>
</html>
"""

    with open(output, "w") as f:
        f.write(page)

    clear()  # start clean for the next report
    print(f"Report written to {output} ({len(findings)} findings)")


# Build a small example report, so we can check this file works on its own.
if __name__ == "__main__":
    import pandas as pd

    clear()

    df = pd.read_csv("sales.csv")
    totals = df.groupby("region")["revenue"].sum().round(2).reset_index()
    totals.columns = ["Region", "Total revenue"]

    add_finding(
        headline="West is the largest region at $650,708",
        detail="West accounts for 30% of the year's revenue, ahead of North at $626,360.",
        table=totals.to_html(index=False, border=0),
    )

    add_finding(
        headline="But West revenue fell 26% over the year",
        detail="It began 2024 as the biggest region and ended it as the smallest.",
        chart="docs/example-chart.png",
    )

    build(
        title="Test report: does report.py work?",
        output="report.html",
        source="sales.csv",
    )
