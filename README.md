# Data Analyst Agent

Ask questions about a CSV file in plain English. The agent writes pandas code,
runs it, reads the output, fixes its own mistakes, and answers with real numbers.

It also draws charts that point at the finding, rather than leaving you to
spot it.

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

Every number printed on a chart is computed from the data in the same snippet
that draws it - never written by the model from memory.

```bash
python agent.py "Draw a line chart of monthly revenue for each region"
```

## Things it can do

- **Aggregate**: totals, averages, counts, grouped any way you like
- **Compare**: growth rates, rankings, period over period changes
- **Filter**: any slice of the data, described in words
- **Chart**: bar, line, scatter, anything matplotlib can draw
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
| `make_data.py` | Generates the sample `sales.csv` |
| `hello_gemini.py` | Minimal API call, to check your key works |
| `sales.csv` | Sample data: 144 rows of fake regional sales |

## Configuration

The knobs are constants at the top of `agent.py`:

| Setting | Default | Meaning |
|---|---|---|
| `MODEL` | `gemini-3.1-flash-lite` | Which Gemini model to use |
| `MAX_STEPS` | `8` | Most API calls allowed per question |
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
