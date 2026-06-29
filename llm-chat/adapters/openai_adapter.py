from typing import AsyncIterator
from .base import LLMAdapter


class OpenAIAdapter(LLMAdapter):
    def __init__(self, api_key: str):
        import openai
        self.client = openai.AsyncOpenAI(api_key=api_key)

    async def chat(self, messages: list[dict], model: str) -> str:
        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            tools=[{"type": "web_search_preview"}],
        )
        return response.choices[0].message.content or ""

    async def chat_stream(self, messages: list[dict], model: str) -> AsyncIterator[str]:
        stream = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
