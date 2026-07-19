"""DCMA SQL 工具。"""

from fault_diagnosis.platform.logging import get_logger

_log = get_logger("sql_tools")

_db = None
_sqltools_by_model = {}


def _get_db():
    """懒加载 SQLDatabase 单例。"""
    global _db
    if _db is None:
        import os

        from dotenv import load_dotenv
        from langchain_community.utilities import SQLDatabase

        from fault_diagnosis.platform.settings import MYSQL_DATABASE, MYSQL_USER

        load_dotenv(override=False)
        host = os.getenv("HOST")
        mysql_pw = os.getenv("MYSQL_PW")
        port = os.getenv("PORT")
        _db = SQLDatabase.from_uri(
            f"mysql+pymysql://{MYSQL_USER}:{mysql_pw}@{host}:{port}/{MYSQL_DATABASE}"
        )
    return _db


def get_sqltools(model_name: str | None = None):
    """返回 SQLDatabaseToolkit 生成的工具列表。"""
    from fault_diagnosis.platform.llm_runtime import (
        chat_openai_transport_kwargs,
        chat_template_extra_body,
        resolve_llm_api_key,
        resolve_llm_base_url,
        resolve_llm_model_name,
    )

    resolved_model = resolve_llm_model_name(model_name)
    if resolved_model not in _sqltools_by_model:
        from dotenv import load_dotenv
        from langchain_community.agent_toolkits import SQLDatabaseToolkit
        from langchain_openai import ChatOpenAI

        db = _get_db()
        load_dotenv(override=False)
        base_url = resolve_llm_base_url()
        runtime_kwargs = chat_openai_transport_kwargs(base_url)
        extra_body = chat_template_extra_body(base_url)
        if extra_body is not None:
            runtime_kwargs["extra_body"] = extra_body
        model = ChatOpenAI(
            model=resolved_model,
            base_url=base_url or None,
            api_key=resolve_llm_api_key(),
            temperature=0.7,
            **runtime_kwargs,
        )
        toolkit = SQLDatabaseToolkit(db=db, llm=model)
        _sqltools_by_model[resolved_model] = toolkit.get_tools()
    return _sqltools_by_model[resolved_model]


__all__ = ["get_sqltools"]
