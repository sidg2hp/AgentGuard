from langchain.docstore.document import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores import FAISS
from langchain_community.embeddings import OllamaEmbeddings

def split_and_store(search_results):
    docs = [Document(page_content=entry["content"]) for entry in search_results]
    chunks = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50).split_documents(docs)
    vectordb = FAISS.from_documents(chunks, OllamaEmbeddings(model="llama3"))
    return vectordb
