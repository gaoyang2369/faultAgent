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
    import os

    resolved_model = (model_name or os.getenv("MODEL_NAME") or "").strip()
    if resolved_model not in _sqltools_by_model:
        from dotenv import load_dotenv
        from langchain_community.agent_toolkits import SQLDatabaseToolkit
        from langchain_openai import ChatOpenAI

        db = _get_db()
        load_dotenv(override=False)
        model = ChatOpenAI(
            model=resolved_model,
            base_url=os.getenv("OPENAI_BASE_URL"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.7,
        )
        toolkit = SQLDatabaseToolkit(db=db, llm=model)
        _sqltools_by_model[resolved_model] = toolkit.get_tools()
    return _sqltools_by_model[resolved_model]


__all__ = ["get_sqltools"]
