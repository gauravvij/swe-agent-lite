"""OpenRouter LLM client with retry logic and rate limit handling."""
import logging
import sys
import time
from typing import Optional

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, "/app/swe_agent_benchmark_0958")
from utils.config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, MODEL_NAME,
    MAX_TOKENS_PER_CALL, TEMPERATURE, REQUEST_TIMEOUT, MAX_RETRIES, RETRY_DELAY
)

logger = logging.getLogger(__name__)


class LLMClient:
    """Client for OpenRouter API using moonshotai/kimi-k2.5."""

    def __init__(self, model: str = MODEL_NAME, temperature: float = TEMPERATURE):
        """Initialize the LLM client with OpenRouter configuration."""
        self.model = model
        self.temperature = temperature
        self.client = OpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
            timeout=REQUEST_TIMEOUT,
        )
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_calls = 0

    def chat(
        self,
        messages: list[dict],
        max_tokens: int = MAX_TOKENS_PER_CALL,
        temperature: Optional[float] = None,
        stop: Optional[list[str]] = None,
    ) -> tuple[str, dict]:
        """
        Send a chat completion request to OpenRouter.

        Returns:
            Tuple of (response_text, usage_dict)
        """
        temp = temperature if temperature is not None else self.temperature
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                kwargs = dict(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temp,
                )
                if stop:
                    kwargs["stop"] = stop

                response = self.client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content or ""
                usage = {}
                if response.usage:
                    usage = {
                        "prompt_tokens": response.usage.prompt_tokens or 0,
                        "completion_tokens": response.usage.completion_tokens or 0,
                        "total_tokens": (response.usage.total_tokens or 0),
                    }
                    self.total_prompt_tokens += usage["prompt_tokens"]
                    self.total_completion_tokens += usage["completion_tokens"]
                self.total_calls += 1
                return content, usage

            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                if "rate limit" in err_str or "429" in err_str:
                    wait = RETRY_DELAY * (2 ** attempt)
                    logger.warning(f"Rate limit hit, waiting {wait}s (attempt {attempt+1})")
                    time.sleep(wait)
                elif "timeout" in err_str or "timed out" in err_str:
                    logger.warning(f"Timeout on attempt {attempt+1}, retrying...")
                    time.sleep(RETRY_DELAY)
                elif "502" in err_str or "503" in err_str or "504" in err_str:
                    wait = RETRY_DELAY * (attempt + 1)
                    logger.warning(f"Server error {e}, waiting {wait}s...")
                    time.sleep(wait)
                else:
                    logger.error(f"LLM error (attempt {attempt+1}): {e}")
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(RETRY_DELAY)
                    else:
                        break

        raise RuntimeError(f"LLM call failed after {MAX_RETRIES} attempts: {last_error}")

    def get_usage_stats(self) -> dict:
        """Return cumulative token usage statistics."""
        return {
            "total_calls": self.total_calls,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
        }

    def estimate_cost(self) -> float:
        """Estimate cost based on kimi-k2.5 pricing (~$0.14/M input, $0.14/M output)."""
        input_cost = (self.total_prompt_tokens / 1_000_000) * 0.14
        output_cost = (self.total_completion_tokens / 1_000_000) * 0.14
        return input_cost + output_cost
