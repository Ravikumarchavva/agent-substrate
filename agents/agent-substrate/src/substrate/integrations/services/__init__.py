"""Clients for the heavy services you run yourself and reach by URL.

OCR and layout analysis, multimodal embedding, reranking — anything that needs a GPU or a gigabyte of dependencies —
runs as a server, and this package is only the client side: a base URL (and a token) in, a typed result out. Nothing here
imports a model library, so the base install stays small and the same code talks to a service on localhost, in the
cluster, or hosted.

``embedding_reranker``   EmbeddingRerankerClient — embeddings and rerank scores from an embedding-reranker service
``llama_server``         EmbeddingReranker — the same, direct to llama-server (``/embeddings``, ``/rerank``)

Importing this package imports nothing; import the module you need.
"""
