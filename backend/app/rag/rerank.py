from functools import lru_cache
from typing import Protocol

from app.config import get_settings


class Reranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]: ...


class CrossEncoderReranker:
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import CrossEncoder  # heavy import, keep lazy

        self._model = CrossEncoder(model_name, max_length=512)

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        scores = self._model.predict([(query, t) for t in texts], batch_size=16)
        return [float(s) for s in scores]


@lru_cache
def get_reranker() -> Reranker:
    return CrossEncoderReranker(get_settings().reranker_model)
