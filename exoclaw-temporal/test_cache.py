import litellm
import os

response = litellm.completion(
    model="deepseek/deepseek-chat",
    messages=[{"role": "user", "content": "What is 1+1?"}],
    api_key=os.environ.get("DEEPSEEK_API_KEY", "")
)
print("Response usage:", response.usage)
if hasattr(response.usage, "prompt_tokens_details"):
    print("Prompt tokens details:", getattr(response.usage, "prompt_tokens_details"))
else:
    print("No prompt_tokens_details found")
