from functools import lru_cache
from typing import Protocol

from app.config import get_settings


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    # BGE models expect this prefix on queries (not on documents) for retrieval.
    QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # heavy import, keep lazy

        self._model = SentenceTransformer(model_name)
        self.dim = self._model.get_embedding_dimension() or 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, batch_size=32, normalize_embeddings=True)
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        vector = self._model.encode(self.QUERY_PREFIX + text, normalize_embeddings=True)
        return vector.tolist()  # type: ignore[no-any-return]


@lru_cache
def get_embedder() -> Embedder:
    return SentenceTransformerEmbedder(get_settings().embedding_model)
