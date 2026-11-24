"""Bundled public documents and their stable citation identifiers."""
CATALOG = {
    "rag": "Retrieval augmented generation (RAG) uses retrieved documents as evidence.",
    "agents": "Research agents gather evidence. Drafting agents select relevant evidence.",
    "policies": "Tool policies check permissions before a handler runs.",
}

CATALOG_PREFIX = "urn:agentguard:catalog:"


def local_read(document_id):
    return CATALOG.get(document_id)


def catalog_text(uri):
    if isinstance(uri, str) and uri.startswith(CATALOG_PREFIX):
        return local_read(uri[len(CATALOG_PREFIX):])
    return None
