"""Original .run/.invoke entry points backed by the guarded research workflow."""
import os

from agentguard.policy import ToolGuard
from agentguard.providers import tavily_search
from pipeline import build_pipeline


class _Agent:
    def __init__(self, run):
        self.run = run

    def invoke(self, request):
        return {"output": self.run(request["input"] if isinstance(request, dict) else request)}


def get_research_agent():
    guard = ToolGuard(tavily_search, secrets=(os.getenv("TAVILY_API_KEY", ""),))
    return _Agent(lambda query: guard.call("research", "search", {"query": query}, []))


def _retrieve(retriever, query):
    docs = retriever.invoke(query)
    return [{"content": doc.page_content, "url": doc.metadata.get("source")} for doc in docs]


def get_drafting_agent(retriever):
    graph = build_pipeline(search=lambda query: _retrieve(retriever, query))
    return _Agent(lambda query: graph.invoke({"query": query})["response"])


def get_dual_agent_system(retriever=None):
    def combined_search(query):
        rows = tavily_search(query)
        return [*rows[:3], *_retrieve(retriever, query)] if retriever is not None else rows

    graph = build_pipeline(search=combined_search)
    return lambda question: graph.invoke({"query": question})["response"]
