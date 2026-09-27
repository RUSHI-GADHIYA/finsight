"""Prompt-injection detection with a local classifier (no API cost).

Applied twice: to the user's question before a run starts, and to retrieved filing passages
before the analyst sees them, since text inside a document can carry instructions too.
"""

import asyncio
from functools import lru_cache
from typing import Protocol

from app.config import get_settings


class InjectionClassifier(Protocol):
    def scores(self, texts: list[str]) -> list[float]:
        """Probability that each text is a prompt-injection attempt."""
        ...


class HFInjectionClassifier:
    def __init__(self, model_name: str) -> None:
        from transformers import pipeline  # heavy import, keep lazy

        self._pipe = pipeline(
            "text-classification", model=model_name, truncation=True, max_length=512
        )

    def scores(self, texts: list[str]) -> list[float]:
        if not texts:
            return []
        out = self._pipe(texts, batch_size=8)
        return [r["score"] if r["label"] == "INJECTION" else 1.0 - r["score"] for r in out]


@lru_cache
def get_injection_classifier() -> InjectionClassifier:
    return HFInjectionClassifier(get_settings().injection_model)


class InjectionGuard:
    """Async wrapper with the configured threshold. `classifier=None` disables checks."""

    def __init__(self, classifier: InjectionClassifier | None, threshold: float) -> None:
        self._classifier = classifier
        self.threshold = threshold

    async def scores(self, texts: list[str]) -> list[float]:
        if self._classifier is None:
            return [0.0] * len(texts)
        return await asyncio.to_thread(self._classifier.scores, texts)

    async def flagged(self, texts: list[str]) -> list[float | None]:
        """Score per text if it is over the threshold, else None."""
        return [s if s >= self.threshold else None for s in await self.scores(texts)]


def default_guard() -> InjectionGuard:
    settings = get_settings()
    classifier = get_injection_classifier() if settings.guardrails_enabled else None
    return InjectionGuard(classifier, settings.injection_threshold)
