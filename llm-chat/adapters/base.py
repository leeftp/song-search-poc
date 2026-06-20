from abc import ABC, abstractmethod
from typing import AsyncIterator


class LLMAdapter(ABC):
    """全LLMアダプターの基底クラス"""

    @abstractmethod
    async def chat(self, messages: list[dict], model: str) -> str:
        """メッセージリストを受け取り、応答文字列を返す"""
        ...

    @abstractmethod
    async def chat_stream(self, messages: list[dict], model: str) -> AsyncIterator[str]:
        """ストリーミングで応答を返す"""
        ...
