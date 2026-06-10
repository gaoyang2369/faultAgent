"""DCMA SQL 工具。"""

from ..common.logger import get_logger

_log = get_logger("sql_tools")

_db = None
_sqltools = None


def _get_db():
    """懒加载 SQLDatabase 单例。"""
    global _db
    if _db is None:
        import os

        from dotenv import load_dotenv
        from langchain_community.utilities import SQLDatabase

        from ..config import DCMA_DB_NAME, MYSQL_USER

        load_dotenv(override=False)
        host = os.getenv("HOST")
        mysql_pw = os.getenv("MYSQL_PW")
        port = os.getenv("PORT")
        _db = SQLDatabase.from_uri(
            f"mysql+pymysql://{MYSQL_USER}:{mysql_pw}@{host}:{port}/{DCMA_DB_NAME}"
        )
    return _db


def get_sqltools():
    """返回 SQLDatabaseToolkit 生成的工具列表。"""
    global _sqltools
    if _sqltools is None:
        import os

        from dotenv import load_dotenv
        from langchain_community.agent_toolkits import SQLDatabaseToolkit
        from langchain_openai import ChatOpenAI

        db = _get_db()
        load_dotenv(override=False)
        model = ChatOpenAI(
            model=os.getenv("MODEL_NAME"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.7,
        )
        toolkit = SQLDatabaseToolkit(db=db, llm=model)
        _sqltools = toolkit.get_tools()
    return _sqltools


__all__ = ["get_sqltools"]
