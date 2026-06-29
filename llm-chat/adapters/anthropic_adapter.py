from typing import AsyncIterator
from .base import LLMAdapter


class AnthropicAdapter(LLMAdapter):
    def __init__(self, api_key: str):
        import anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    def _split_system(self, messages: list[dict]) -> tuple[str, list[dict]]:
        system = ""
        rest = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                rest.append(msg)
        return system, rest

    async def chat(self, messages: list[dict], model: str) -> str:
        system, msgs = self._split_system(messages)
        kwargs = dict(
            model=model, max_tokens=4096, messages=msgs,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        )
        if system:
            kwargs["system"] = system
        response = await self.client.messages.create(**kwargs)
        # web_search使用時はcontentが複数ブロックになるため、textブロックのみ結合して返す
        return "".join(b.text for b in response.content if hasattr(b, "text"))

    async def chat_stream(self, messages: list[dict], model: str) -> AsyncIterator[str]:
        system, msgs = self._split_system(messages)
        kwargs = dict(model=model, max_tokens=4096, messages=msgs)
        if system:
            kwargs["system"] = system
        async with self.client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
