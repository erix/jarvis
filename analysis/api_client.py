"""AI provider client with OpenRouter and OpenAI Codex OAuth support."""
import json
import logging
import os
import re
import time
from typing import Any, Optional

import openai

from .ai_settings import resolve_ai_model, resolve_ai_provider

logger = logging.getLogger(__name__)

# OpenRouter pricing per 1M tokens for anthropic/claude-sonnet-4-6.
_PRICE_INPUT = 3.00 / 1_000_000
_PRICE_OUTPUT = 15.00 / 1_000_000

DEFAULT_PROVIDER = resolve_ai_provider()
DEFAULT_MODEL = resolve_ai_model(DEFAULT_PROVIDER)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


class APIClient:
    def __init__(self, cost_tracker=None, model: Optional[str] = None, provider: Optional[str] = None):
        self.provider = (provider or resolve_ai_provider()).lower()
        self.model = model or resolve_ai_model(self.provider)
        self.cost_tracker = cost_tracker
        self.client = self._build_client()

    def _build_client(self):
        if self.provider == "openrouter":
            api_key = os.getenv("OPENROUTER_API_KEY")
            if not api_key:
                raise RuntimeError("OPENROUTER_API_KEY not set")
            return openai.OpenAI(
                api_key=api_key,
                base_url=OPENROUTER_BASE_URL,
                max_retries=0,  # We handle retries manually
            )

        if self.provider == "codex":
            from .codex_oauth import get_valid_credentials

            credentials = get_valid_credentials()
            return openai.OpenAI(
                api_key=credentials.access_token,
                base_url=CODEX_BASE_URL,
                max_retries=0,
            )

        raise RuntimeError(f"Unsupported AI provider: {self.provider}")

    def analyze(
        self,
        ticker: str,
        analyzer_type: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1024,
        cache: bool = True,
    ) -> Optional[dict]:
        """Call configured provider and return parsed JSON dict, or None on failure."""
        text = self.chat_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            ticker=ticker,
            analyzer_type=analyzer_type,
        )
        return self._extract_json(text)

    def chat_text(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1024,
        ticker: str = "PORTFOLIO",
        analyzer_type: str = "chat",
    ) -> str:
        """Call configured provider and return raw text."""
        for attempt in range(3):
            try:
                if self.provider == "codex":
                    return self._call_codex_responses(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        max_tokens=max_tokens,
                    )
                return self._call_openrouter_chat(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                )

            except openai.APIError as e:
                status_code = getattr(e, "status_code", None)
                if self.provider == "codex" and status_code in (401, 403):
                    self._refresh_codex_client()
                    if attempt < 2:
                        continue
                if status_code and status_code >= 500:
                    logger.warning("[%s] Server error %s, attempt %d/3", ticker, status_code, attempt + 1)
                    time.sleep(2 ** attempt)
                elif status_code == 429:
                    logger.warning("[%s] Rate limited, waiting 60s", ticker)
                    time.sleep(60)
                else:
                    logger.error("[%s/%s] API error: %s", ticker, analyzer_type, e)
                    return None
            except Exception as e:
                logger.error("[%s/%s] Unexpected error: %s", ticker, analyzer_type, e)
                time.sleep(2 ** attempt)
                continue

        logger.error("[%s/%s] All retries exhausted", ticker, analyzer_type)
        return ""

    def _call_openrouter_chat(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            extra_headers={
                "HTTP-Referer": "https://jarvis.internal",
                "X-Title": "JARVIS Hedge Fund",
            },
        )
        if self.cost_tracker:
            self.cost_tracker.record(response.usage)
        return response.choices[0].message.content or ""

    def _call_codex_responses(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        response = self.client.responses.create(
            model=self.model,
            instructions=system_prompt,
            input=user_prompt,
            max_output_tokens=max_tokens,
        )
        text = getattr(response, "output_text", None)
        if not text:
            text = self._extract_responses_text(response)
        if self.cost_tracker:
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "input_tokens", 0) if usage else self.estimate_tokens(user_prompt)
            output_tokens = getattr(usage, "output_tokens", 0) if usage else self.estimate_tokens(text)
            self.cost_tracker.record_subscription_call(
                provider="codex",
                input_tokens=input_tokens or 0,
                output_tokens=output_tokens or 0,
            )
        return text

    def _refresh_codex_client(self) -> None:
        from .codex_oauth import load_credentials, refresh_credentials

        credentials = load_credentials()
        if not credentials:
            return
        refresh_credentials(credentials)
        self.client = self._build_client()

    @staticmethod
    def _extract_responses_text(response: Any) -> str:
        chunks: list[str] = []
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if text:
                    chunks.append(text)
        return "\n".join(chunks)

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        """Extract JSON from text, handling markdown code blocks."""
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        match = re.search(r"\{[\s\S]+\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        logger.debug("Could not extract JSON from response: %s", text[:200])
        return None

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token estimate: ~4 chars per token."""
        return len(text) // 4

    @staticmethod
    def estimate_cost(input_tokens: int, output_tokens: int = 256) -> float:
        return input_tokens * _PRICE_INPUT + output_tokens * _PRICE_OUTPUT
