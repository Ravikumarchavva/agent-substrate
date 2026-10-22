"""The `inference` worker role: multimodal embedding and reranking over HTTP (`loamz-data worker --role inference`).

It speaks the OpenAI embeddings wire (`POST /v1/embeddings`) and the Jina/Cohere rerank wire (`POST /v1/rerank`), so
`loamz.engine.models.RemoteEmbedder` / `RemoteReranker` reach it, and so do llama.cpp, vLLM, Ollama and TEI.
"""
