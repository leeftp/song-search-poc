from typing import AsyncIterator
from .base import LLMAdapter


class GeminiAdapter(LLMAdapter):
    def __init__(self, api_key: str):
        from google import genai
        from google.genai import types
        self.types = types
        self.client = genai.Client(api_key=api_key)

    def _build_contents(self, messages: list[dict]):
        """OpenAI形式のメッセージをgoogle.genai形式に変換する。"""
        types = self.types
        contents = []
        for msg in messages:
            role = msg["role"]
            if role == "system":
                continue  # system はGenerateContentConfigのsystem_instructionで渡す
            genai_role = "model" if role == "assistant" else "user"
            contents.append(
                types.Content(role=genai_role, parts=[types.Part(text=msg["content"])])
            )
        return contents

    def _system_prompt(self, messages: list[dict]) -> str:
        for msg in messages:
            if msg["role"] == "system":
                return msg["content"]
        return ""

    async def chat(self, messages: list[dict], model: str) -> str:
        import asyncio
        types = self.types
        system = self._system_prompt(messages)
        contents = self._build_contents(messages)

        config = types.GenerateContentConfig(
            system_instruction=system or None,
            max_output_tokens=4096,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self.client.models.generate_content(
                model=model, contents=contents, config=config
            ),
        )
        return response.text or ""

    async def chat_stream(self, messages: list[dict], model: str) -> AsyncIterator[str]:
        import asyncio
        types = self.types
        system = self._system_prompt(messages)
        contents = self._build_contents(messages)

        config = types.GenerateContentConfig(
            system_instruction=system or None,
            max_output_tokens=4096,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self.client.models.generate_content(
                model=model, contents=contents, config=config
            ),
        )
        if response.text:
            yield response.text
