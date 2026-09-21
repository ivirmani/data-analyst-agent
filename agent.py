"""Data Analyst Agent: ask questions about a CSV in plain English."""

import os
import sys
import time

import httpx
import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from tools import run_python

MODEL = "gemini-3.1-flash-lite"

# Hard ceiling on how many trips to the API the agent may make for one question.
MAX_STEPS = 8

# Where the agent saves any charts it draws.
CHART_DIR = "charts"

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


def log(message):
    """Print a timestamped line, so we can watch what the agent is doing."""
    print(f"[{time.strftime('%H:%M:%S')}] {message}")


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
        lines.append(f"  - {column_name}: {dtype}")

    lines.append("")
    lines.append("First 5 rows:")
    lines.append(df.head().to_string(index=False))

    return "\n".join(lines)


# The standing instructions the model reads before every question.
SYSTEM_PROMPT = """You are a careful data analyst. You answer questions about a CSV \
file by writing small pieces of Python code and running them.

Rules:
- The run_python tool is the ONLY way you can see the data. Never guess a number.
- Start your code with: import pandas as pd
- Load the file with: df = pd.read_csv("{csv_path}")
- Every snippet runs in a FRESH process. Variables do not carry over between calls,
  so reload the file each time.
- Always print() what you want to see. Anything you do not print is lost.
- Work in small steps. Run some code, read the output, then decide what to do next.
- If the code fails, read the error message carefully and fix it.
- When you know the answer, reply with plain text and no tool call. Give the actual
  numbers you found and one sentence explaining what they mean.

Charts:
- Only draw a chart if the question actually asks for one.
- matplotlib is available. Import it as: import matplotlib.pyplot as plt
- Save the picture into the {chart_dir} folder, for example:
    plt.savefig("{chart_dir}/revenue_by_region.png")
- Never call plt.show(). There is no screen. Saving the file is the whole job.
- Call plt.close() after saving, so the next chart starts from a blank page.
- Give the chart a title and axis labels.
- After saving, print the file path, then mention that path in your final answer.

Here is a summary of the file you are analyzing:

{summary}
"""


def build_system_prompt(csv_path):
    """Fill the CSV path and summary into the system prompt template."""
    return SYSTEM_PROMPT.format(
        csv_path=csv_path,
        summary=summarize_csv(csv_path),
        chart_dir=CHART_DIR,
    )


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


def call_model(client, conversation, config):
    """Send the conversation to Gemini, retrying if the failure looks temporary."""
    wait = FIRST_RETRY_WAIT

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return client.models.generate_content(
                model=MODEL,
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

    def __init__(self, csv_path="sales.csv"):
        self.csv_path = csv_path

        # Create the charts folder if it is not there yet. exist_ok means
        # "do nothing if it already exists" rather than raising an error.
        os.makedirs(CHART_DIR, exist_ok=True)

        self.client = genai.Client(
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )

        self.config = types.GenerateContentConfig(
            system_instruction=build_system_prompt(csv_path),
            tools=[RUN_PYTHON_TOOL],
            # Turn OFF the SDK's automatic tool running. We drive the loop ourselves.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        # The whole conversation so far. The model has no memory, so we resend
        # this entire list every turn. It keeps growing as the session goes on.
        self.conversation = []

        log(f"Data file: {csv_path}")

    def ask(self, question):
        """Answer one question, remembering everything asked before it."""
        self.conversation.append(
            types.Content(role="user", parts=[types.Part(text=question)])
        )

        log(f"Question: {question}")

        for step in range(1, MAX_STEPS + 1):
            log(f"--- Step {step} of {MAX_STEPS}: asking the model ---")

            response = call_model(self.client, self.conversation, self.config)

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
                # Keep the answer in the history, so a follow-up question can
                # refer back to what was just said.
                self.conversation.append(reply)
                return thinking.strip() or "(The model replied with nothing.)"

            # Save the model's request into the conversation before we act on it.
            self.conversation.append(reply)

            code = call.args.get("code", "")
            log(f"Tool call: {call.name}")
            print(indent(code))

            started = time.time()
            result = run_python(code)
            log(f"Tool result (took {time.time() - started:.1f}s):")
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
        return (
            f"Stopped after {MAX_STEPS} steps without reaching an answer. "
            "Try asking a simpler or more specific question."
        )


def ask(question, csv_path="sales.csv"):
    """Answer a single question with no history. Behaves exactly as before."""
    return Chat(csv_path).ask(question)


def indent(text):
    """Indent a block of text so it stands out from the log lines."""
    return "\n".join("    | " + line for line in text.splitlines())


def print_answer(answer):
    """Show a final answer in a way that stands out from the log lines."""
    print()
    print("=" * 60)
    print(answer)
    print("=" * 60)


def chat_loop(csv_path="sales.csv"):
    """Keep asking questions in one conversation until the user quits."""
    chat = Chat(csv_path)

    print()
    print(f"Chat mode. Ask about {csv_path}, or type 'quit' to leave.")
    print("Follow-up questions work - the agent remembers what you asked before.")
    print("Type 'reset' to forget the conversation and start a fresh topic.")

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

    # Catch the predictable failures and turn them into one clear sentence,
    # instead of dumping a traceback on the user.
    try:
        if len(sys.argv) >= 2:
            # One question passed on the command line: answer it and exit.
            print_answer(ask(sys.argv[1]))
        else:
            # No question given: start an ongoing conversation instead.
            chat_loop()
    except FileNotFoundError:
        raise SystemExit("sales.csv not found. Run 'python make_data.py' first.")
    except httpx.TimeoutException:
        raise SystemExit(
            "Gemini did not respond in time, even after retrying. "
            "The servers are probably busy. Try again in a minute."
        )
    except errors.APIError as error:
        raise SystemExit(
            f"Gemini API error {error.code}: {error.message}\n"
            "If this is 429 or 503, wait a minute and try again."
        )
    except KeyboardInterrupt:
        raise SystemExit("\nStopped by you (Ctrl+C).")
