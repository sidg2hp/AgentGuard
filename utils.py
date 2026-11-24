"""Lightweight in-memory retriever for callers of the original split_and_store API."""
from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_core.embeddings import Embeddings
import hashlib
import math
import re


class _TokenEmbeddings(Embeddings):
    """Stateless token hashing for bounded local similarity retrieval."""

    def embed_query(self, text):
        vector = [0.0] * 256
        for word in re.findall(r"\w+", text.casefold()):
            vector[hashlib.sha256(word.encode()).digest()[0]] += 1
        norm = math.sqrt(sum(x * x for x in vector)) or 1
        return [x / norm for x in vector]

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]


def split_and_store(search_results):
    store = InMemoryVectorStore(_TokenEmbeddings())
    docs = []
    for row in search_results:
        text = row["content"]
        for start in range(0, len(text), 450):
            docs.append(Document(page_content=text[start:start + 500], metadata={"source": row.get("url", "")}))
    if docs:
        store.add_documents(docs)
    return store
