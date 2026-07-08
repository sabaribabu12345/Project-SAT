from __future__ import annotations

import json
import math
from typing import Protocol
from urllib import error, request

from apps.api.settings import Settings


class MappingSimilarityScorer(Protocol):
    def score_pair(self, left: str, right: str) -> int | None:
        """Return a 0-100 similarity score or None when unavailable."""


class OpenAIEmbeddingSimilarityScorer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._embedding_cache: dict[str, list[float] | None] = {}
        self._failure_count = 0
        self._disabled = False

    def score_pair(self, left: str, right: str) -> int | None:
        if not self._settings.pdf_embedding_mapping_enabled:
            return None
        left_vec = self._embedding(left.strip())
        right_vec = self._embedding(right.strip())
        if not left_vec or not right_vec:
            return None
        similarity = _cosine_similarity(left_vec, right_vec)
        # Clamp to [0, 100] for parity with existing lexical score behavior.
        return max(0, min(100, int(round(similarity * 100))))

    def _embedding(self, text: str) -> list[float] | None:
        if not text:
            return None
        if text in self._embedding_cache:
            return self._embedding_cache[text]
        vector = self._fetch_embedding(text)
        self._embedding_cache[text] = vector
        return vector

    def _fetch_embedding(self, text: str) -> list[float] | None:
        if self._disabled:
            return None
        api_base = (self._settings.pdf_embedding_api_base or self._settings.openai_api_base or "").strip()
        if not api_base:
            return None
        api_key = (self._settings.pdf_embedding_api_key or self._settings.openai_api_key or "").strip()
        if not api_key:
            return None
        model = (self._settings.pdf_embedding_model or "text-embedding-3-small").strip()

        base = api_base.rstrip("/")
        endpoint = f"{base}/embeddings" if base.endswith("/v1") else f"{base}/v1/embeddings"
        payload = {"model": model, "input": text}
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        timeout = max(1, int(self._settings.pdf_embedding_timeout_seconds))
        try:
            with request.urlopen(req, timeout=timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (error.HTTPError, error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            self._record_failure()
            return None

        data = raw.get("data")
        if not isinstance(data, list) or not data:
            self._record_failure()
            return None
        embedding = data[0].get("embedding")
        if not isinstance(embedding, list):
            self._record_failure()
            return None

        vector: list[float] = []
        for value in embedding:
            try:
                vector.append(float(value))
            except (TypeError, ValueError):
                self._record_failure()
                return None
        self._failure_count = 0
        return vector

    def _record_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= 3:
            self._disabled = True


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for lv, rv in zip(left, right, strict=True):
        dot += lv * rv
        left_norm += lv * lv
        right_norm += rv * rv
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (math.sqrt(left_norm) * math.sqrt(right_norm))))
