"""Data Analyst Agent: ask questions about a CSV in plain English."""

import os
import sys
import time

import httpx
import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

import excel
import report
from tools import run_python

# The everyday model: cheap, fast, and plenty for one question at a time.
MODEL = "gemini-3.1-flash-lite"

# Reports load three sets of rules at once, and the prompt passes 9,000
# characters. Flash-Lite starts dropping instructions and mis-stating numbers
# at that size, so reports use a full Flash model instead. Fewer free requests
# per day, but a report is an occasional thing.
REPORT_MODEL = "gemini-3.5-flash"

# Hard ceiling on how many trips to the API the agent may make for one question.
MAX_STEPS = 8

# A report needs far more: three to five findings, and every chart costs
# two calls of its own.
REPORT_MAX_STEPS = 22

# Where the agent saves any charts it draws.
CHART_DIR = "charts"

# When False (the default) we print only the final answer.
# When True we print every step: the code, the output, the timings.
VERBOSE = False

# How many times we retry a failed API call before giving up.
MAX_ATTEMPTS = 4

# Seconds to wait before the first retry. It doubles after each failure.
FIRST_RETRY_WAIT = 2

# How long to wait for Gemini to answer before giving up, in milliseconds.
# Without this the SDK waits forever if the server goes quiet.
REQUEST_TIMEOUT_MS = 60_000  # 60 seconds

# HTTP errors that are temporary and worth retrying.
# 429 = we are going too fast. 5xx = Google's servers are struggling.
RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}


def set_verbose(on):
    """Turn step-by-step output on or off."""
    # "global" means: change the VERBOSE defined at the top of the file,
    # rather than making a new local variable that vanishes.
    global VERBOSE
    VERBOSE = on


def log(message):
    """Print a timestamped line - but only when the user asked to see steps."""
    if VERBOSE:
        print(f"[{time.strftime('%H:%M:%S')}] {message}")


def progress(mark):
    """In quiet mode, show a dot per step so it does not look frozen."""
    if not VERBOSE:
        print(mark, end="", flush=True)


def as_list(csv_paths):
    """Accept either one path or several, and always hand back a list.

    This lets Chat("sales.csv") and Chat(["a.csv", "b.csv"]) both work, so
    adding comparison does not break the single file form.
    """
    if isinstance(csv_paths, str):
        return [csv_paths]
    return list(csv_paths)


def summarize_all(csv_paths):
    """Describe every file the agent is allowed to read, one after another."""
    return "\n\n".join(summarize_csv(path) for path in as_list(csv_paths))


def summarize_csv(csv_path):
    """Describe the CSV in a few lines: size, columns, types, and the first 5 rows.

    We send this to the model instead of the file itself, to save tokens.
    """
    df = pd.read_csv(csv_path)

    lines = [
        f"File path: {csv_path}",
        f"Shape: {len(df)} rows x {len(df.columns)} columns",
        "",
        "Columns and data types:",
    ]

    # df.dtypes pairs each column name with its data type.
    for column_name, dtype in df.dtypes.items():
        line = f"  - {column_name}: {dtype}"

        # For text columns with only a handful of values, list them all. This
        # is how the model learns which categories exist without running code,
        # and when comparing two files, which ones appeared or disappeared.
        if str(dtype) in ("object", "str"):
            values = df[column_name].unique()
            if len(values) <= 15:
                line += " -> " + ", ".join(sorted(str(v) for v in values))
            else:
                line += f" -> {len(values)} distinct values"

        lines.append(line)

    lines.append("")
    lines.append("First 5 rows:")
    lines.append(df.head().to_string(index=False))

    return "\n".join(lines)


# The standing instructions the model reads before every question.
BASE_RULES = """You are a careful data analyst. You answer questions about a CSV \
file by writing small pieces of Python code and running them.

Rules:
- The run_python tool is the ONLY way you can see the data. Never guess a number.
- Start your code with: import pandas as pd
- Load a file with pd.read_csv and the exact path shown in the summaries
  below, for example: df = pd.read_csv("{csv_path}")
- Every snippet runs in a FRESH process. Variables do not carry over between calls,
  so reload the file each time.
- Always print() what you want to see. Anything you do not print is lost.
- Work in small steps. Run some code, read the output, then decide what to do next.
- If the code fails, read the error message carefully and fix it.
- When you know the answer, reply with plain text and no tool call. Give the actual
  numbers you found and one sentence explaining what they mean.
- Every number in your final answer must be copied from something the tool
  actually printed. Never recalculate in your head, never recall a figure from
  memory, and never adjust one you already have. If you need a number that is
  not in front of you, run code to get it.
- If you drew a chart, your final answer must agree with the chart. Read the
  title you gave it before you write your answer.
"""


CHART_RULES = """Charts:
- Only draw a chart if the question actually asks for one.
- matplotlib is available. Import it as: import matplotlib.pyplot as plt
- Save the picture into the {chart_dir} folder, for example:
    plt.savefig("{chart_dir}/revenue_by_region.png")
- Never call plt.show(). There is no screen. Saving the file is the whole job.
- Call plt.close() after saving, so the next chart starts from a blank page.
- Give the chart axis labels.
- Drawing a chart ALWAYS takes two run_python calls, never one:
    Call 1: print the numbers that will go into the chart. Do not draw anything.
    Call 2: look at those numbers, decide what they show, then draw.
  Do not skip call 1, even when the chart seems obvious.
- NEVER type a number into a title or a label by hand. Every number shown on
  a chart must be built with an f-string from a variable you just computed in
  the same snippet. Typing "revenue reached 50,676" from memory is how charts
  end up lying. Do this instead:

    first = totals.iloc[0]
    last = totals.iloc[-1]
    change = (last - first) / first * 100
    direction = "rose" if change > 0 else "fell"
    title = f"West revenue {direction} {abs(change):.0f}% over the year"

- Work out the direction from the numbers too, the same way. Do not describe a
  series as rising or recovering until you have checked the sign of the change.
- The title is the finding you saw in call 1, written as a sentence, not a
  description of the axes.
  Good: "South overtakes West in December"
  Bad:  "Revenue by Region"
- Then point at that finding on the picture, with exactly ONE annotation.
  The label must be a takeaway with a number in it, not a name for a point.
  It must also say something the title does NOT already say. If the title is
  "Widget earns the most at $1,071,431", the label should add context, such as
  "68% more than Gadget", never repeat the same sentence.
  Good: "Fell 26% from its February peak"
  Bad:  "West peak revenue"
  Point xy at whatever part of the data the TITLE is about. If the title is
  about a decline, point at the end of the decline, not at the peak.
  Work xy out from the real data, never guess it.
  Place the text using axes fraction coordinates, keeping y between 0.1 and
  0.7, so the label can never collide with the title. Keep the arrow SHORT:
  put the text near the point it points at, not on the other side of the
  chart. A long arrow crossing the whole picture is worse than no arrow.

    plt.margins(y=0.15)          # leave some empty space around the data
    plt.annotate(
        "Fell 26% from its February peak",
        xy=(11, 45817),                          # a real data point
        xytext=(0.45, 0.35), textcoords="axes fraction",
        arrowprops=dict(arrowstyle="->", color="black"),
        fontsize=9,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="grey"),
    )

- Make the finding stand out. Whatever the title is about keeps a strong colour
  and a thick line. Everything else is light grey and thin, so the eye lands in
  the right place. Keep every series in the legend:

    plt.plot(x, west,  color="crimson",   linewidth=2.5, zorder=3, label="West")
    plt.plot(x, north, color="lightgrey", linewidth=1.2, zorder=1, label="North")
- After saving, print the file path, then mention that path in your final answer.
"""


REPORT_RULES = """Reports:
You are writing a short report, not answering one question. Explore the data in
several directions and record each thing you find as you go.

- The report module is already available. Record a finding like this:

    import report
    report.add_finding(
        headline=f"South grew {growth:.0f}% over the year",
        detail="It started as the smallest region and finished second.",
        chart="charts/south_growth.png",
        table=summary_df.to_html(index=False, border=0),
    )

  chart and table are both optional. headline and detail are not.
- Record 4 or 5 findings, and make them different KINDS of fact rather than
  four superlatives. Between them they must cover all three of these:
    * how the totals changed over time, as a percentage
    * the biggest mover in EACH direction: whatever grew fastest, and whatever
      fell hardest. Rank by change, not by size. The fastest riser is often
      small today and is the thing a reader most needs to know about.
    * a breakdown across one of the categories in the data
  Three "biggest X" facts is a weak report even when every number is right.
- If something is both large and shrinking, say both in the SAME finding. A
  leader that is declining is far more interesting than a leader.
- Every headline must contain a real number, built with an f-string from a
  value you computed. The same rule as chart titles, for the same reason.
- At least TWO of the findings MUST have a chart. This is not optional. Four
  findings of plain text is not a report. Draw the chart first, save it, then
  pass its path to add_finding as chart="charts/whatever.png".
- Tidy any table before you pass it. Rename columns to readable labels, and
  format the numbers so a person can read them:

      table_df["Total revenue"] = table_df["Total revenue"].map("${:,.0f}".format)

  "$434,973" belongs in a report. "434972.65" does not.
- Each finding must say something different. Do not record the same fact twice
  in different words.
- When every finding is recorded, build the report in a final call:

    import report
    report.build(title="How the business performed in 2024", source="sales.csv")

- Then reply in plain text: two sentences summarising what you found, and the
  path to report.html.
- That closing summary may ONLY repeat things you actually recorded as
  findings. Do not introduce a new claim, a new cause or a new "driver" in it.
  If you did not record it, do not say it.
- Never call anything a driver of growth unless you computed its growth and
  the number came out positive. Check the sign before you praise it.
"""


EXCEL_RULES = """Excel workbooks:
Your deliverable is a FILE, not an answer. The job is finished when
excel.build() has run and analysis.xlsx exists on disk. Printing the numbers
into your reply is not finishing. Do not write a summary of the data in text.

Produce tables a person can work with, not a dump of the raw rows.

- The excel module is already available. Record a table like this:

    import excel
    excel.add_table(
        "Revenue by region",
        table_df,
        note="Total revenue for 2024, summed across every product and month.",
        chart="bar",
        labels="Region",
        values="Total revenue",
    )

  chart, labels and values are optional. Use chart="bar" to compare categories
  and chart="line" for anything measured over time.
- Record 2 to 4 tables. Each one becomes its own sheet.
- Aggregate first. A sheet holding 144 raw rows is not useful; a sheet holding
  four regional totals is.
- Rename columns to readable labels before passing the table: "Total revenue",
  not "revenue". labels and values must match the names AFTER renaming.
- Leave numbers as NUMBERS. Do not turn them into strings like "$650,708" -
  that would stop the recipient using them in formulas. The workbook applies
  the display formatting itself.
- When every table is recorded, build the workbook in a final call:

    import excel
    excel.build()

- Then reply in plain text: a sentence on what the workbook contains, and the
  path analysis.xlsx.
"""


COMPARE_RULES = """Comparing files:
You have been given more than one file. The user wants to know what CHANGED
between them, not what each one contains. Two sets of totals side by side is
not a comparison - the reader should never have to do the subtraction.

- Load both and give the variables names that say which is which:

    old = pd.read_csv("sales.csv")
    new = pd.read_csv("sales_2025.csv")

- Report the difference and the percentage difference, not two separate totals.
- Comparing is ALWAYS a two-pass job, the same as drawing a chart:
    Call 1: for EVERY category column in the data - region, product, and any
            others - print all of the following, for BOTH files:
              * the total per category
              * the trend within that file, from its first period to its last
                period, as a percentage
    Call 2: read those numbers and work out what actually changed.
  Do not skip call 1. Do not stop at the first column where you find a change:
  products changing does not excuse you from checking regions.
- Then report what you found, in this order:
    * any category whose trend CHANGED SIGN between the files. Growing 36% in
      one file and falling 26% in the next is the largest kind of change there
      is, even when the two yearly totals look nearly identical.
    * categories present in one file but not the other, in BOTH directions.
      Say added or discontinued, never a 100% fall or an infinite rise.
    * the totals, and how far they moved.
  Totals go LAST. They are the least interesting part of a comparison.
"""


DATA_SECTION = """Here are the files you can read:

{summary}
"""


def build_system_prompt(csv_paths, mode="chat"):
    paths = as_list(csv_paths)
    """Fill the CSV path and summary into the system prompt template."""
    # Only include the rules that apply. Report rules are long, and a small
    # model handles a short prompt better than a long one, so an ordinary
    # question never has to carry them.
    sections = [BASE_RULES]

    # Excel charts are native spreadsheet objects, so the matplotlib rules are
    # not just useless there, they compete with the rules that matter.
    if mode != "excel":
        sections.append(CHART_RULES)

    if mode == "report":
        sections.append(REPORT_RULES)
    if mode == "excel":
        sections.append(EXCEL_RULES)
    if len(paths) > 1:
        sections.append(COMPARE_RULES)
    sections.append(DATA_SECTION)

    prompt = "\n".join(sections)

    # We fill the placeholders with .replace() rather than .format(), because
    # the prompt contains example code with { } braces in it, and .format()
    # would try to treat those as placeholders too.
    # The summary goes in last, so that braces inside the data are left alone.
    prompt = prompt.replace("{csv_path}", paths[0])
    prompt = prompt.replace("{chart_dir}", CHART_DIR)
    prompt = prompt.replace("{summary}", summarize_all(paths))
    return prompt


# The tool definition: how we describe run_python to Gemini.
# This is a description only. The model can ask for it, but cannot run anything.
RUN_PYTHON_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="run_python",
            description=(
                "Run a snippet of Python code and get back whatever it printed. "
                "pandas and matplotlib are installed. Each call runs in a fresh process, so no "
                "variables survive between calls. If the code fails you get the "
                "error message back."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "code": types.Schema(
                        type=types.Type.STRING,
                        description="The Python code to run. Must use print() to show results.",
                    )
                },
                required=["code"],
            ),
        )
    ]
)


def find_function_call(parts):
    """Return the first tool call in the model's reply, or None if there isn't one."""
    for part in parts:
        if part.function_call:
            return part.function_call
    return None


def collect_text(parts):
    """Join together any plain text the model sent."""
    return "".join(part.text for part in parts if part.text)


def call_model(client, conversation, config, model):
    """Send the conversation to Gemini, retrying if the failure looks temporary."""
    wait = FIRST_RETRY_WAIT

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return client.models.generate_content(
                model=model,
                contents=conversation,
                config=config,
            )
        except httpx.TimeoutException:
            # The server took our request but never replied in time.
            if attempt == MAX_ATTEMPTS:
                raise
            log(f"Request timed out. Retrying in {wait}s "
                f"(attempt {attempt} of {MAX_ATTEMPTS})")
            time.sleep(wait)
            wait = wait * 2

        except errors.APIError as error:
            is_last_attempt = attempt == MAX_ATTEMPTS

            # A 429 usually means "slow down", and backing off fixes it. But a
            # 429 for the DAILY quota will still be there in eight seconds, so
            # retrying just wastes time before delivering the same bad news.
            if error.code == 429 and "PerDay" in str(error):
                raise

            # A permanent error (bad key, bad request) will never fix itself,
            # so re-raise it immediately instead of waiting around.
            if error.code not in RETRYABLE_CODES or is_last_attempt:
                raise

            log(f"API error {error.code}. Retrying in {wait}s "
                f"(attempt {attempt} of {MAX_ATTEMPTS})")
            time.sleep(wait)
            wait = wait * 2  # back off: 2s, then 4s, then 8s


class Chat:
    """One ongoing conversation about one CSV file.

    Everything that stays the same for the whole session - the client and the
    config - is built once, here. The conversation list lives here too, which
    is what lets a follow-up question refer back to an earlier one.
    """

    def __init__(self, csv_paths="sales.csv", mode="chat"):
        self.csv_paths = as_list(csv_paths)
        self.mode = mode

        # Building a document takes many more steps than one question does,
        # and is a harder job, so it also gets the stronger model.
        building = mode in ("report", "excel")
        self.max_steps = REPORT_MAX_STEPS if building else MAX_STEPS
        self.model = REPORT_MODEL if building else MODEL

        # Throw away anything left over from an earlier run.
        if mode == "report":
            report.clear()
        if mode == "excel":
            excel.clear()

        # Create the charts folder if it is not there yet. exist_ok means
        # "do nothing if it already exists" rather than raising an error.
        os.makedirs(CHART_DIR, exist_ok=True)

        self.client = genai.Client(
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )

        self.config = types.GenerateContentConfig(
            system_instruction=build_system_prompt(self.csv_paths, mode),
            tools=[RUN_PYTHON_TOOL],
            # Turn OFF the SDK's automatic tool running. We drive the loop ourselves.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        # The whole conversation so far. The model has no memory, so we resend
        # this entire list every turn. It keeps growing as the session goes on.
        self.conversation = []

        log(f"Data files: {', '.join(self.csv_paths)}")
        log(f"Model: {self.model}")

    def ask(self, question):
        """Answer one question, remembering everything asked before it."""
        self.conversation.append(
            types.Content(role="user", parts=[types.Part(text=question)])
        )

        log(f"Question: {question}")
        progress("Analyzing")

        for step in range(1, self.max_steps + 1):
            log(f"--- Step {step} of {self.max_steps}: asking the model ---")
            progress(".")

            response = call_model(
                self.client, self.conversation, self.config, self.model
            )

            # A "candidate" is one possible reply. We asked for one, so take the first.
            reply = response.candidates[0].content
            parts = reply.parts or []

            # The model sometimes explains itself in text alongside a tool call.
            thinking = collect_text(parts)
            if thinking.strip():
                log(f"Model says: {thinking.strip()}")

            call = find_function_call(parts)

            # No tool call means the model is giving its final answer.
            if call is None:
                log("No tool call. This is the final answer.")
                progress("\n")
                # Keep the answer in the history, so a follow-up question can
                # refer back to what was just said.
                self.conversation.append(reply)
                return thinking.strip() or "(The model replied with nothing.)"

            # Save the model's request into the conversation before we act on it.
            self.conversation.append(reply)

            code = call.args.get("code", "")
            log(f"Tool call: {call.name}")
            if VERBOSE:
                print(indent(code))

            started = time.time()
            result = run_python(code)
            log(f"Tool result (took {time.time() - started:.1f}s):")
            if VERBOSE:
                print(indent(result))

            # Send the result back, labelled as the answer to that tool call.
            self.conversation.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_function_response(
                            name=call.name,
                            response={"output": result},
                        )
                    ],
                )
            )

        log("Step limit reached.")
        progress("\n")
        return (
            f"Stopped after {self.max_steps} steps without reaching an answer. "
            "Try asking a simpler or more specific question."
        )


def ask(question, csv_paths="sales.csv", mode="chat"):
    """Answer a single question with no history. Behaves exactly as before."""
    return Chat(csv_paths, mode).ask(question)


def indent(text):
    """Indent a block of text so it stands out from the log lines."""
    return "\n".join("    | " + line for line in text.splitlines())


def print_answer(answer):
    """Show the final answer. In verbose mode it needs a banner to stand out
    from all the log lines. In quiet mode the answer is all there is."""
    if VERBOSE:
        print()
        print("=" * 60)
        print(answer)
        print("=" * 60)
    else:
        print(answer)


def chat_loop(csv_paths="sales.csv"):
    """Keep asking questions in one conversation until the user quits."""
    chat = Chat(csv_paths)

    print()
    print(f"Chat mode. Ask about {', '.join(chat.csv_paths)}, or type 'quit' to leave.")
    print("Follow-up questions work - the agent remembers what you asked before.")
    print("Type 'reset' to start a fresh topic, or 'verbose' to see the steps.")

    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            # Ctrl+D or Ctrl+C at the prompt means "I'm done".
            print("\nBye.")
            return

        # Just pressing Enter should do nothing, not send an empty question.
        if not question:
            continue

        if question.lower() in ("quit", "exit", "q"):
            print("Bye.")
            return

        # Emptying the list wipes the memory. The client, the config and the
        # system prompt are untouched, so the next question starts clean but
        # still knows all about the CSV.
        # Let the user switch step-by-step output on and off mid-conversation.
        if question.lower() in ("verbose", "steps"):
            set_verbose(not VERBOSE)
            print("Showing every step." if VERBOSE else "Showing answers only.")
            continue

        if question.lower() in ("reset", "new", "clear"):
            cleared = len(chat.conversation)
            chat.conversation.clear()
            print(f"Forgot {cleared} messages. Fresh start - ask me anything.")
            continue

        # A failed question should not end the whole session, so we catch the
        # predictable problems here and go back to the prompt.
        try:
            print_answer(chat.ask(question))
        except KeyboardInterrupt:
            print("\nStopped that question. Ask another, or type 'quit'.")
        except httpx.TimeoutException:
            print("Gemini did not respond in time. Try that question again.")
        except errors.APIError as error:
            print(f"Gemini API error {error.code}: {error.message}")


if __name__ == "__main__":
    load_dotenv()

    if not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY not found. Check that .env exists in this folder.")

    # Pull any -v / --verbose flag out, leaving just the question behind.
    arguments = sys.argv[1:]
    for flag in ("-v", "--verbose"):
        if flag in arguments:
            arguments.remove(flag)
            set_verbose(True)

    # --report and --excel are two ways of asking for a document, so they
    # are one setting rather than two flags that could both be on.
    mode = "chat"
    for flag, flag_mode in (("--report", "report"), ("--excel", "excel")):
        if flag in arguments:
            if mode != "chat":
                raise SystemExit("Use either --report or --excel, not both.")
            mode = flag_mode
            arguments.remove(flag)

    # --compare is followed by the two files to compare.
    csv_paths = "sales.csv"
    if "--compare" in arguments:
        where = arguments.index("--compare")
        csv_paths = arguments[where + 1:where + 3]

        # Two arguments is not enough of a check: the question itself would
        # otherwise be mistaken for the second file. Require .csv names.
        looks_like_files = all(p.lower().endswith(".csv") for p in csv_paths)

        if len(csv_paths) != 2 or not looks_like_files:
            raise SystemExit(
                "--compare needs two files, for example:\n"
                '  python agent.py --compare sales.csv sales_2025.csv "what changed?"'
            )
        # Remove the flag and both paths, leaving just the question.
        del arguments[where:where + 3]

    # Catch the predictable failures and turn them into one clear sentence,
    # instead of dumping a traceback on the user.
    try:
        if mode != "chat" and not arguments:
            raise SystemExit(f'--{mode} needs a question, for example:\n'
                             f'  python agent.py --{mode} "How did 2024 go?"')

        if arguments:
            # A question was passed on the command line: answer it and exit.
            print_answer(ask(arguments[0], csv_paths, mode))
        else:
            # No question given: start an ongoing conversation instead.
            chat_loop(csv_paths)
    except FileNotFoundError as error:
        raise SystemExit(
            f"File not found: {error.filename}\n"
            "If you have not made the sample data yet, run: python make_data.py"
        )
    except httpx.TimeoutException:
        raise SystemExit(
            "Gemini did not respond in time, even after retrying. "
            "The servers are probably busy. Try again in a minute."
        )
    except errors.APIError as error:
        if error.code == 429 and "PerDay" in str(error):
            raise SystemExit(
                f"Daily free quota used up for this model.\n"
                f"Reports and spreadsheets use {REPORT_MODEL}, which has a much\n"
                f"smaller free allowance than {MODEL}.\n"
                "Either wait for the quota to reset (midnight Pacific), or edit\n"
                "REPORT_MODEL at the top of agent.py to another Flash model."
            )
        raise SystemExit(
            f"Gemini API error {error.code}: {error.message}\n"
            "If this is 429 or 503, wait a minute and try again."
        )
    except KeyboardInterrupt:
        raise SystemExit("\nStopped by you (Ctrl+C).")
