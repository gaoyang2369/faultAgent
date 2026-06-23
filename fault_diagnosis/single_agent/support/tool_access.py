"""Lazy tool accessors for the restricted single-agent runner."""


def get_knowledge_tool():
    from ...tools.kb_tools import query_knowledge_base

    return query_knowledge_base


def get_report_tool():
    from ...tools.report_tools import save_report

    return save_report
