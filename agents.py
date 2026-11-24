"""Original .run/.invoke entry points backed by the guarded research workflow."""
import os

from agentguard.policy import ToolGuard
from agentguard.evidence import collect
from agentguard.providers import tavily_search
from pipeline import build_pipeline


class _Agent:
    def __init__(self, run):
        self.run = run

    def invoke(self, request):
        return {"output": self.run(request["input"] if isinstance(request, dict) else request)}


def get_research_agent():
    guard = ToolGuard(tavily_search, secrets=(os.getenv("TAVILY_API_KEY", ""),))
    def research(query):
        rows = guard.call("research", "search", {"query": query}, [])
        docs = collect(rows, guard.secrets)
        return "\n".join(f"{quote} [{source}]" for source, doc in docs.items() for quote in doc["excerpts"])
    return _Agent(research)


def _retrieve(retriever, query):
    docs = retriever.invoke(query)
    return [{"content": doc.page_content, "url": doc.metadata.get("source")} for doc in docs]


def get_drafting_agent(retriever):
    graph = build_pipeline(search=lambda query: _retrieve(retriever, query))
    return _Agent(lambda query: graph.invoke({"query": query})["response"])


def get_dual_agent_system(retriever=None):
    retrieve = None if retriever is None else lambda query: _retrieve(retriever, query)
    graph = build_pipeline(retrieve=retrieve)
    return lambda question: graph.invoke({"query": question})["response"]
