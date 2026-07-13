from __future__ import annotations

import os

from langchain_openai import ChatOpenAI


def build_chat_model(model_name: str | None = None) -> ChatOpenAI:
    return ChatOpenAI(
        model=model_name or os.getenv("MODEL_NAME"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.7,
    )


def build_summary_model(model_name: str | None = None) -> ChatOpenAI:
    return ChatOpenAI(
        model=model_name or os.getenv("MODEL_NAME"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.7,
    )
