from openai import OpenAI
import os

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

response = client.chat.completions.create(
    model="gpt-4.1-mini",
    messages=[
        {
            "role": "user",
            "content": "hello"
        }
    ]
)

print(response.choices[0].message.content)