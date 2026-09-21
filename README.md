# Data Analyst Agent

Ask questions about a CSV file in plain English. The agent writes pandas code,
runs it, reads the output, fixes its own mistakes, and answers with real numbers.

It also draws charts that point at the finding, rather than leaving you to
spot it, can write a whole report as a single self-contained HTML file, and can
tell you what changed between two files.

```
$ python agent.py "Which region grew fastest from January to December?"

Analyzing..
The South region grew the fastest, at approximately 117.25%.
```

By default you get the answer and nothing else. Add `-v` to watch it work -
every piece of code it writes, and what that code printed:

```
$ python agent.py -v "What is the total revenue?"

[15:52:41] Question: What is the total revenue?
[15:52:41] --- Step 1 of 8: asking the model ---
[15:52:44] Tool call: run_python
    | import pandas as pd
    | df = pd.read_csv("sales.csv")
    | print(df['revenue'].sum())
[15:52:44] Tool result (took 0.2s):
    | 2145803.62
[15:52:46] Model says: The total revenue is 2,145,803.62.

============================================================
The total revenue across all regions, products, and months is 2,145,803.62.
============================================================
```

`-v` is worth using whenever an answer looks surprising. The agent sometimes
takes a wrong turn and corrects itself, and the only way to know is to look.

Ask for a chart and you get one that makes its point:

![A line chart of monthly revenue by region. The title reads "West region
revenue fell 26% over the year". The West line is highlighted in red while the
other three regions are greyed out, and an arrow points at December with the
label "Fell from 61,574 in January to 45,817 in December".](docs/example-chart.png)

## How it works

The agent never guesses a number. It can only learn about your data by running
code, and with `-v` you can see every line it runs.

1. We send Gemini a **summary** of the CSV - column names, data types, and the
   first 5 rows. Never the whole file.
2. Gemini replies either with plain text, or with a request to call our
   `run_python` tool.
3. If it asks for the tool, we run that code in a **separate process** with a
   15 second timeout, and send back only what it printed - or the error message.
4. Repeat, up to 8 steps, until Gemini answers in plain text.

The error message is the important part. When the generated code crashes, the
model reads the real traceback and writes a corrected version. There is no
retry logic in this project - self-correction falls out of handing the model
its own errors.

The loop is written by hand in `agent.py`. The SDK's automatic function calling
is explicitly disabled, so nothing is hidden.

## Requirements

- Python 3.10 or newer
- A free Google Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey)

## Setup

```bash
git clone https://github.com/ivirmani/data-analyst-agent.git
cd data-analyst-agent
```

Create a virtual environment and install the dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Add your API key. Copy the example file and put your real key in it:

```bash
cp .env.example .env
```

Then edit `.env` so it reads:

```
GEMINI_API_KEY=your_key_here
```

`.env` is listed in `.gitignore` and is never committed.

Check that everything works:

```bash
python hello_gemini.py
```

You should get a one sentence answer back from Gemini.

## Usage

### Ask one question

```bash
python agent.py "What is the total revenue for each product?"
```

Add `-v` (or `--verbose`) to see every step instead of just the answer.

### Chat mode

Run it with no arguments for an ongoing conversation, where follow-up
questions remember what came before:

```bash
python agent.py
```

```
> which region grew fastest?
The South region grew the fastest, at approximately 117.25%.

> what about the slowest?
The West region was the slowest, declining about 25.59%.

> which product lost the most revenue there, in dollars?
Widget, which lost 7,368.29 - about 47% of West's total decline.
```

Commands inside chat mode:

| Command | What it does |
|---|---|
| `reset` | Forget the conversation and start a fresh topic |
| `verbose` | Toggle step-by-step output on and off |
| `quit` | Leave |
| `Ctrl+C` | Abandon the current question, stay in chat |

### Charts

Ask for one and it saves a PNG into `charts/`. The charts are annotated: the
title states the finding rather than describing the axes, the series being
discussed is highlighted while the rest fade to grey, and an arrow points at
the thing being claimed.

This takes two passes. The agent first runs code to print the numbers, looks
at them, and only then draws - because you cannot title a chart with its
finding before you know what the finding is.

Every number shown on a chart is built with an f-string from a value computed
in the same snippet, and direction words come from the sign of the change:

```python
direction = "rose" if change > 0 else "fell"
title = f"West revenue {direction} {abs(change):.0f}% over the year"
```

That matters more than it looks. An earlier version let the model write labels
freehand, and it produced "West revenue ends the year with a strong recovery"
over a line that had fallen 26%. The chart was plotted correctly from the data;
only the sentence on top of it was invented. Numbers that are computed cannot
be wrong in that way, and a falling line cannot be called a recovery when the
word itself comes from the sign of a subtraction.

```bash
python agent.py "Draw a line chart of monthly revenue for each region"
```

### Reports

Give it a broad question and `--report`, and instead of one answer you get
`report.html` - a self-contained page with several findings, charts and tables:

```bash
python agent.py --report "How did the business perform in 2024?"
```

![The top of a generated report. The heading reads "How the business performed
in 2024", with a source line naming sales.csv and the date. Below it a finding
titled "Total revenue grew 15.8% over the year" and a line chart of monthly
revenue.](docs/example-report.png)

The agent works through the data in several directions and records each finding
as it goes. It is required to cover the change over time, the biggest mover in
each direction, and a breakdown by category - so a report cannot just be four
"biggest X" facts. Ranking by change rather than size is the rule that matters
most: the fastest riser is usually small today, which is exactly why ranking by
size hides it.

Findings accumulate in a small JSON file as they are discovered, because each
piece of generated code runs in a fresh process and nothing survives in memory
between steps. A final step renders that file into HTML, with the charts
base64-encoded into the page so the report is one file you can email.

The report itself is `report.html` in the project folder. It is not committed -
it is output, regenerated whenever you ask.

### Comparing two files

Give it two CSVs and it reports what changed, rather than describing each one:

```bash
python agent.py --compare sales.csv sales_2025.csv "What changed between these two years?"
```

```
Regional monthly trends reversed sign: West rebounded to grow 74% across 2025
while East fell 26%. The Doohickey product was discontinued and replaced by
Gizmo, which generated $649,520. Total annual revenue rose 19.6%, from
$2,145,804 to $2,567,092.
```

Comparison is a two-pass job, like charting. The agent first prints, for every
category column in both files, the totals and the trend within each file - then
reads those numbers and decides what changed.

That first pass is the whole feature. The obvious comparison is between the
totals, and the totals are where the important changes hide. In the sample data
East is **+1.2% year over year**, the dullest number in the table, while inside
those years it went from growing 36% to falling 26%. A comparison that reports
"East was flat" is true and useless.

So the agent is told to report, in this order: anything whose trend changed
sign, then anything added or discontinued, then the totals. Totals go last
because they are the least interesting part of a comparison.

Combine it with `--report` for a comparison report:

```bash
python agent.py --compare sales.csv sales_2025.csv --report "What changed?"
```

## Things it can do

- **Aggregate**: totals, averages, counts, grouped any way you like
- **Compare**: growth rates, rankings, period over period changes
- **Filter**: any slice of the data, described in words
- **Chart**: annotated bar, line and scatter charts - titled with the finding,
  with the series being discussed highlighted and the rest greyed out
- **Report**: a self-contained HTML page of findings, charts and tables, from
  one broad question
- **Compare**: what changed between two files - reversals, things added or
  discontinued, and by how much
- **Follow up**: "what about the East?" works, in chat mode
- **Recover**: if its code crashes, it reads the error and tries again
- **Refuse**: if the data cannot answer your question, it says so instead of
  inventing a number

Some questions to try against the included `sales.csv`:

```
What is the total revenue?
Which quarter of 2024 had the highest revenue?
Which region had the highest revenue in December, and did it grow fastest?
Is there any month where West outsold North?
Which product is growing fastest in the South?
Make a bar chart of total revenue by region
```

## Using your own data

Replace `sales.csv`, or point the agent at any CSV from Python:

```python
from agent import Chat

chat = Chat("my_data.csv")
print(chat.ask("what are the top 5 rows by value?"))
```

Nothing in the prompt or the tool is specific to sales data.

## Project structure

| File | What it does |
|---|---|
| `agent.py` | The system prompt, tool definition, agent loop, and chat mode |
| `tools.py` | Runs generated code in a subprocess with a timeout |
| `report.py` | Collects findings, then renders them into one HTML file |
| `make_data.py` | Generates both sample CSVs |
| `hello_gemini.py` | Minimal API call, to check your key works |
| `sales.csv` | Sample data: 144 rows of fake regional sales for 2024 |
| `sales_2025.csv` | The year after, with changes to compare against |

## Configuration

The knobs are constants at the top of `agent.py`:

| Setting | Default | Meaning |
|---|---|---|
| `MODEL` | `gemini-3.1-flash-lite` | Model for ordinary questions |
| `REPORT_MODEL` | `gemini-3.6-flash` | Model for `--report`, which needs a stronger one |
| `MAX_STEPS` | `8` | Most API calls allowed per question |
| `REPORT_MAX_STEPS` | `22` | The same limit in `--report` mode, which needs far more |
| `MAX_ATTEMPTS` | `4` | Retries on a temporary API failure |
| `REQUEST_TIMEOUT_MS` | `60000` | How long to wait for Gemini |
| `CHART_DIR` | `charts` | Where charts are saved |

And in `tools.py`:

| Setting | Default | Meaning |
|---|---|---|
| `TIMEOUT_SECONDS` | `15` | How long generated code may run |
| `MAX_OUTPUT_CHARS` | `4000` | Output cap, so a big print cannot flood the model |

### Choosing a model

Any Gemini model with function calling works. Flash-Lite models have a much
larger free daily request allowance than the full Flash models, which matters
because one question costs 2-4 API calls. If you hit `503` or `504` errors, the
model is busy - switch `MODEL` to another one.

Models do get retired. If you get a `404`, check the
[current model list](https://ai.google.dev/gemini-api/docs/models).

**Why two models.** A report loads three sets of rules at once and the prompt
passes 9,000 characters. At that size Flash-Lite began dropping the report
procedure entirely and mis-stating figures - claiming a region went from -46%
to +39% when the real values were -26% and +74%. Making the rules more
emphatic made it worse, which is the signal that the problem is the volume of
instructions rather than their wording. Reports use a full Flash model
instead. Ordinary questions stay on Flash-Lite, which has a far larger free
daily allowance and handles them perfectly well.

## Limitations

**This is not a sandbox.** Generated code runs in a subprocess with a timeout,
which stops runaway code from hanging the agent. It does **not** stop that code
from reading or deleting your files. Only point this at CSVs you trust, on a
machine where that risk is acceptable. Real isolation means Docker with no
network and a read-only mount.

**Free tier data use.** On Google's free tier, your content may be used to
improve their products. Do not point this at confidential data without moving
to a paid tier.

**Vague questions get one interpretation.** Asked "which product drove the
decline?", the agent may answer by percentage when you meant dollars - and it
will not mention that it chose. Ask precisely, or follow up.

**It can still be wrong.** It shows you every line of code it ran. For anything
that matters, read it.

## Ideas for extending it

- **Streamlit UI** - a browser interface instead of the terminal
- **Token tracking** - `response.usage_metadata` tells you what each call cost
- **Multiple files** - let the agent join two CSVs
- **Docker sandbox** - real isolation for the generated code
- **History compaction** - drop old code and results, keep questions and answers

## License

MIT
