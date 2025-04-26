# Kairon Agent: Dual-Agent System

This project implements a dual-agent system using LangChain and Tavily for online information gathering and answer drafting. The system consists of two agents:

1. **Research Agent**: Gathers relevant information from the web using Tavily.
2. **Drafting Agent**: Drafts comprehensive answers based on the gathered information.

## Features
- **Research Agent**: Utilizes TavilySearchResults to fetch online data.
- **Drafting Agent**: Uses a retriever tool to draft answers based on context.
- **Dual-Agent System**: Combines both agents to provide end-to-end functionality.

## Setup

### Prerequisites
- Python 3.8+
- Install required dependencies:
  ```bash
  pip install langchain langchain-core langchain-community
  ```
- Set the `TAVILY_API_KEY` environment variable:
  ```bash
  export TAVILY_API_KEY=your_api_key  # Linux/Mac
  set TAVILY_API_KEY=your_api_key    # Windows
  ```

### Configuration
- Ensure the `TAVILY_API_KEY` is set in your environment.
- The system uses the `Ollama` LLM with the `llama3` model.

## Usage

### Research Agent
The research agent gathers information from the web using Tavily.
```python
from agents import get_research_agent

research_agent = get_research_agent()
question = "What are the latest advancements in AI?"
result = research_agent.run(question)
print(result)
```

### Drafting Agent
The drafting agent drafts answers based on retrieved context.
```python
from agents import get_drafting_agent
from langchain.vectorstores import FAISS
from langchain.embeddings import OpenAIEmbeddings

# Create a retriever
embeddings = OpenAIEmbeddings()
retriever = FAISS.load_local("path_to_faiss_index", embeddings).as_retriever()

drafting_agent = get_drafting_agent(retriever)
question = "What are the benefits of AI in healthcare?"
result = drafting_agent.run(question)
print(result)
```

### Dual-Agent System
The dual-agent system combines both agents for end-to-end functionality.
```python
from agents import get_dual_agent_system

dual_agent = get_dual_agent_system(retriever)
question = "What is the impact of climate change on agriculture?"
result = dual_agent(question)
print(result)
```

## Testing
- Test the agents individually or as a dual-agent system.
- Use `verbose=True` in `AgentExecutor` for detailed logs.

## License
This project is licensed under the MIT License.