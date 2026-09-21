"""The smallest possible Gemini call, just to prove the API key works."""

import os

from dotenv import load_dotenv
from google import genai

# Keeping the model name in one place means we only edit one line to switch models.
MODEL = "gemini-3.5-flash-lite"

# Read the .env file and load GEMINI_API_KEY into the environment.
load_dotenv()

# Fail with a clear message instead of a confusing crash later on.
if not os.environ.get("GEMINI_API_KEY"):
    raise SystemExit("GEMINI_API_KEY not found. Check that .env exists in this folder.")

# The client picks up GEMINI_API_KEY from the environment on its own.
client = genai.Client()

response = client.models.generate_content(
    model=MODEL,
    contents="In one short sentence, what is a pandas DataFrame?",
)

print("Model:", MODEL)
print("Reply:", response.text)
