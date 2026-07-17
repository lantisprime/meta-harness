"""Retrieval module: semantic scoring + budgeted, fenced injection."""
from selflearn.retrieval.injection import InjectionBlock, render_injection_block
from selflearn.retrieval.retriever import RetrievalResult, Retriever, cosine

__all__ = ["InjectionBlock", "render_injection_block", "RetrievalResult",
           "Retriever", "cosine"]
