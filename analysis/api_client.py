"""OpenRouter API client (OpenAI-compatible) with retry and JSON extraction."""
import json
import logging
import os
import re
import time
from typing import Any, Optional

import openai

logger = logging.getLogger(__name__)

# OpenRouter pricing per 1M tokens for anthropic/claude-sonnet-4-6
_PRICE_INPUT = 3.00 / 1_000_000
_PRICE_OUTPUT = 15.00 / 1_000_000

DEFAULT_MODEL = os.getenv("JARVIS_MODEL", "anthropic/claude-sonnet-4-6")


class APIClient:
    def __init__(self, cost_tracker=None, model: str = DEFAULT_MODEL):
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        self.client = openai.OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            max_retries=0,  # We handle retries manually
        )
        self.model = model
        self.cost_tracker = cost_tracker

    def analyze(
        self,
        ticker: str,
        analyzer_type: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1024,
        cache: bool = True,
    ) -> Optional[dict]:
        """Call Claude via OpenRouter and return parsed JSON dict, or None on failure."""
        # OpenRouter handles prompt caching internally — no explicit cache_control needed
        for attempt in range(3):
            try:
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

                text = response.choices[0].message.content or ""
                return self._extract_json(text)

            except openai.APIError as e:
                status_code = getattr(e, "status_code", None)
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
        return None

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
