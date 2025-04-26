import os
from langchain.agents import AgentExecutor, create_react_agent
from langchain_core.prompts import PromptTemplate
from langchain_community.llms import Ollama
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain.tools.retriever import create_retriever_tool
from langchain_core.tools import Tool

# --- Configuration ---
# Ensure API key is loaded (using env var from app.py)
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")  # No fallback value!
OLLAMA_MODEL = "llama3"
# ---------------------

# --- Research Agent ---
def get_research_agent():
    """Creates an agent dedicated to searching Tavily for online information gathering."""
    if not TAVILY_API_KEY:
        raise ValueError("TAVILY_API_KEY is not set. Please configure it in your environment variables.")

    # Tavily tool for online research
    tavily_tool = TavilySearchResults(k=5, api_key=TAVILY_API_KEY)
    llm = Ollama(model=OLLAMA_MODEL)

    # Prompt for the research agent
    research_prompt_template = '''You are a research assistant. Your goal is to gather relevant information from the web using the Tavily tool.

TOOLS:
------
{tools}

Use the following format:

Question: the input question you must research
Thought: I need to use the Tavily tool to gather information.
Action: the action to take, should be one of [{tool_names}] (must be TavilySearchResults)
Action Input: the original Question
Observation: the result of the action (this is the gathered information)
Thought: I now have the information. I will summarize it.
Final Answer: [The summarized information based on the Observation]

Begin!

Question: {input}
Thought:{agent_scratchpad}'''
    research_prompt = PromptTemplate.from_template(research_prompt_template)

    research_agent_runnable = create_react_agent(llm, [tavily_tool], research_prompt)

    research_agent_executor = AgentExecutor(
        agent=research_agent_runnable,
        tools=[tavily_tool],
        verbose=True,
        handle_parsing_errors=True,
        max_iterations=10
    )
    return research_agent_executor

# --- Drafting Agent ---
def get_drafting_agent(retriever):
    """Creates an agent dedicated to drafting an answer using retrieved context."""
    llm = Ollama(model=OLLAMA_MODEL)

    # Create a retriever tool for the drafting agent
    retriever_tool = create_retriever_tool(
        retriever,
        "context_retriever",
        "Searches and returns relevant context/documents based on a query."
    )

    # Simplified prompt for drafting based on retrieved context
    drafting_prompt_template = '''You are a writing assistant. Your goal is to draft a comprehensive answer to the Question based *only* on the context provided by the context_retriever tool.

TOOLS:
------
{tools}

Use the following format:

Question: the input question you must answer
Thought: I need to use the context_retriever tool to get information relevant to the Question.
Action: the action to take, should be one of {tool_names} (must be context_retriever)
Action Input: the original Question
Observation: the result of the action (this is the context)
Thought: I now have the context. I will write the final answer based *only* on the provided context.
Final Answer: [The comprehensive answer drafted using *only* the text from the Observation]

Begin!

Question: {input}
Thought:{agent_scratchpad}'''
    drafting_prompt = PromptTemplate.from_template(drafting_prompt_template)

    drafting_agent_runnable = create_react_agent(llm, [retriever_tool], drafting_prompt)

    drafting_agent_executor = AgentExecutor(
        agent=drafting_agent_runnable,
        tools=[retriever_tool],
        verbose=True,
        handle_parsing_errors=True,
        max_iterations=10
    )
    return drafting_agent_executor

# --- Dual-Agent System ---
def get_dual_agent_system(retriever):
    """Creates a dual-agent system with a research agent and a drafting agent."""
    research_agent = get_research_agent()
    drafting_agent = get_drafting_agent(retriever)

    def dual_agent_executor(question):
        """Executes the dual-agent system."""
        # Step 1: Use the research agent to gather information
        research_result = research_agent.run(question)

        # Step 2: Use the drafting agent to draft an answer based on the research result
        drafting_result = drafting_agent.run(research_result)

        return drafting_result

    return dual_agent_executor
