import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY, base_url="https://openrouter.ai/api/v1")

completion = client.chat.completions.create(
  model="x-ai/grok-4-fast:free",
  messages=[
    {
      "role": "user",
      "content": "Что такое майнбридж?"
    }
  ]
)

print(completion)