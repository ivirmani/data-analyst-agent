"""The agent's one tool: run a snippet of Python and report what happened."""

import os
import subprocess
import sys

# How long the code is allowed to run before we kill it.
TIMEOUT_SECONDS = 15

# Cap how much output we hand back, so a stray print(df) can't flood the model.
MAX_OUTPUT_CHARS = 4000

# The environment the code runs in: a copy of ours, plus one setting.
# MPLBACKEND=Agg tells matplotlib to draw straight into image files rather
# than trying to open a window, which there is no one around to look at.
CHILD_ENV = {**os.environ, "MPLBACKEND": "Agg"}


def run_python(code: str) -> str:
    """Run `code` in a separate Python process.

    Returns whatever the code printed, or a readable error message.
    Never raises - the agent should always get a string back.
    """
    try:
        result = subprocess.run(
            # sys.executable is the Python we are running right now (our venv's),
            # so the code has pandas available. "-c" means "run this code string".
            [sys.executable, "-c", code],
            env=CHILD_ENV,         # run it with matplotlib in file-writing mode
            capture_output=True,   # collect the output instead of letting it print
            text=True,             # hand it back as text, not raw bytes
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return (
            f"ERROR: The code ran longer than {TIMEOUT_SECONDS} seconds and was stopped. "
            "Try a simpler approach."
        )

    # A "return code" of 0 means success. Anything else means the code crashed.
    if result.returncode != 0:
        return "ERROR:\n" + result.stderr.strip()

    output = result.stdout.strip()

    if not output:
        return "The code ran without errors but printed nothing. Use print() to show a result."

    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n...[output truncated]"

    return output


# This block only runs when you execute this file directly, not when it is imported.
if __name__ == "__main__":
    print("1. Code that works")
    print(run_python("print(2 + 2)"))
    print()

    print("2. Code that uses pandas on our CSV")
    print(run_python(
        "import pandas as pd\n"
        "df = pd.read_csv('sales.csv')\n"
        "print(df['revenue'].sum())"
    ))
    print()

    print("3. Code that crashes")
    print(run_python("print(1 / 0)"))
    print()

    print("4. Code that prints nothing")
    print(run_python("x = 5"))
    print()

    print("5. Code that never finishes (takes 15 seconds to give up)")
    print(run_python("while True: pass"))
