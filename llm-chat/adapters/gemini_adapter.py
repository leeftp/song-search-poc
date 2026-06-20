from typing import AsyncIterator
from .base import LLMAdapter


class GeminiAdapter(LLMAdapter):
    def __init__(self, api_key: str):
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self._genai = genai

    def _to_gemini_messages(self, messages: list[dict]) -> tuple[str, list]:
        """OpenAI形式のメッセージをGemini形式に変換"""
        system_prompt = ""
        history = []
        last_user_msg = ""

        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                system_prompt = content
            elif role == "user":
                last_user_msg = content
                if history and history[-1]["role"] == "user":
                    history.append({"role": "model", "parts": ["..."]})
                history.append({"role": "user", "parts": [content]})
            elif role == "assistant":
                history.append({"role": "model", "parts": [content]})

        # 最後のユーザーメッセージをhistoryから除く（chat.send_messageで送るため）
        if history and history[-1]["role"] == "user":
            history = history[:-1]

        return system_prompt, history, last_user_msg

    async def chat(self, messages: list[dict], model: str) -> str:
        import asyncio
        system_prompt, history, last_user_msg = self._to_gemini_messages(messages)

        generation_config = {}
        model_obj = self._genai.GenerativeModel(
            model_name=model,
            system_instruction=system_prompt if system_prompt else None,
        )
        chat_session = model_obj.start_chat(history=history)

        # Gemini SDK は同期なので executor で実行
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: chat_session.send_message(last_user_msg)
        )
        return response.text

    async def chat_stream(self, messages: list[dict], model: str) -> AsyncIterator[str]:
        import asyncio
        system_prompt, history, last_user_msg = self._to_gemini_messages(messages)

        model_obj = self._genai.GenerativeModel(
            model_name=model,
            system_instruction=system_prompt if system_prompt else None,
        )
        chat_session = model_obj.start_chat(history=history)

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: chat_session.send_message(last_user_msg, stream=True),
        )

        for chunk in response:
            if chunk.text:
                yield chunk.text
