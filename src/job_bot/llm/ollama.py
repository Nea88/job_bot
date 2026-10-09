import asyncio
import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class OllamaClient:
    def __init__(self, url: str, model: str, concurrency: int = 1, timeout: float = 300, num_ctx: int = 4096):
        self._url = url.rstrip("/")
        self._model = model
        self._num_ctx = num_ctx
        self._sem = asyncio.Semaphore(concurrency)
        self._http = httpx.AsyncClient(timeout=timeout)

    async def chat_json(self, system: str, user: str, schema: type[T], temperature: float = 0.2) -> T:
        """Structured output: Ollama constrains generation to the JSON schema, pydantic validates it."""
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema.model_json_schema(),
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": self._num_ctx},
        }
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                async with self._sem:
                    resp = await self._http.post(f"{self._url}/api/chat", json=payload)
                resp.raise_for_status()
                return schema.model_validate_json(resp.json()["message"]["content"])
            except (httpx.HTTPError, ValidationError, KeyError) as e:
                log.warning("ollama attempt %d failed: %s", attempt + 1, e)
                last_error = e
        raise LLMError(str(last_error)) from last_error

    async def ensure_model(self) -> None:
        """Pull the model on first start so a fresh install needs no manual `ollama pull`."""
        resp = await self._http.get(f"{self._url}/api/tags")
        resp.raise_for_status()
        names = {m.get("name") for m in resp.json().get("models", [])}
        if self._model in names or f"{self._model}:latest" in names:
            return
        log.info("pulling Ollama model %s, this may take a while...", self._model)
        resp = await self._http.post(
            f"{self._url}/api/pull", json={"model": self._model, "stream": False}, timeout=None
        )
        resp.raise_for_status()
        if resp.json().get("status") != "success":
            raise LLMError(f"pull {self._model} failed: {resp.text[:200]}")
        log.info("model %s is ready", self._model)

    async def close(self) -> None:
        await self._http.aclose()
