from langgraph.graph import StateGraph, END
# from langchain.chains import RetrievalQA # No longer using RetrievalQA directly
from langchain_community.llms import Ollama # Keep for potential direct use if needed
from agents import get_drafting_agent # Keep drafting agent, remove research agent import
from langchain_community.tools.tavily_search import TavilySearchResults # Import Tavily tool again
from utils import split_and_store
import json # For parsing potential JSON output from research agent
import ast
import os # Need os to potentially get API key if not passed

def build_pipeline():
    # Define state
    class ResearchState(dict):
        query: str
        search_results: list # Expect list of dicts {'url': ..., 'content': ...}
        retriever: object
        response: str

    graph = StateGraph(ResearchState)

    tavily_api_key = os.getenv("TAVILY_API_KEY")
    if not tavily_api_key:
        # Raise an error if the key is not found - crucial for security and proper function
        raise ValueError("TAVILY_API_KEY environment variable not set. Please create a .env file with the key.")

    tavily_tool = TavilySearchResults(k=5, api_key=tavily_api_key)

    # RESEARCH NODE: Direct Tavily Call (Acts as Research Component)
    def research_node(state):
        print(f"\n--- Research Node: Searching Tavily for query: {state['query']} ---")
        try:
            search_results = tavily_tool.invoke(state["query"])
            # Basic validation
            if isinstance(search_results, list) and all(isinstance(item, dict) for item in search_results):
                print(f"--- Tavily returned {len(search_results)} results. ---")
                state["search_results"] = search_results
            else:
                 print(f"--- Warning: Tavily returned unexpected format: {type(search_results)}. Proceeding with empty list. ---")
                 state["search_results"] = []
        except Exception as e:
            print(f"Error calling Tavily Search: {e}")
            state["search_results"] = [] # Proceed with empty list on error
        return state

    def store_node(state):
        processed_results = state["search_results"]
        if not processed_results or not isinstance(processed_results, list) or not any(isinstance(res, dict) and res.get('content') for res in processed_results):
             print("\n--- Store Node: No valid content found in search results to store. ---")
             state["retriever"] = None
             return state

        print(f"\n--- Store Node: Storing {len(processed_results)} documents ---")
        try:
            valid_results = [res for res in processed_results if isinstance(res, dict) and 'content' in res]
            if not valid_results:
                 raise ValueError("No valid documents with content found after filtering.")
            state["retriever"] = split_and_store(valid_results).as_retriever()
        except Exception as e:
            print(f"Error creating vector store: {e}")
            state["retriever"] = None
        return state

    def answer_node(state):
        print(f"\n--- Answer Node: Running Drafting Agent for query: {state['query']} ---")
        if state["retriever"] is None:
            print("\n--- Warning: No retriever available for drafting agent. ---")
            state["response"] = "I could not retrieve any information to answer the query."
            return state

        # Create and invoke the drafting agent, passing the retriever
        drafting_agent = get_drafting_agent(state["retriever"])
        try:
            result = drafting_agent.invoke({"input": state["query"]})
            state["response"] = result['output']
            print(f"\n--- Drafting Agent Output: {state['response']} ---")
        except Exception as e:
            print(f"Error running drafting agent: {e}")
            # Provide a more informative error message if agent fails
            error_detail = str(e)
            if "Could not parse LLM output" in error_detail:
                 state["response"] = f"The drafting agent failed to generate a final answer due to formatting issues. Please check the agent logs."
            elif "Agent stopped due to iteration limit" in error_detail:
                state["response"] = f"The drafting agent stopped because it reached its processing limit. The query might be too complex or the context too large."
            else:
                state["response"] = f"I encountered an error while generating the answer with the drafting agent: {error_detail}"
        return state

    # Build the graph (same structure)
    graph.add_node("research", research_node)
    graph.add_node("store", store_node)
    graph.add_node("answer", answer_node)

    graph.set_entry_point("research")
    graph.add_edge("research", "store")
    graph.add_edge("store", "answer")
    graph.add_edge("answer", END)

    return graph.compile()
