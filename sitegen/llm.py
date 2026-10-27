"""One async `complete()` over the generator pool: Claude models via the Anthropic SDK, everything else via OpenRouter."""
import asyncio
import os
import time
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

# Design Arena (website + image-to-HTML leaders, 2026-10-27), restricted to models our OpenRouter account can
# reach under its zero-data-retention policy (Muse Wonder/Spark, GPT-6 Astra, GLM are excluded).
# Sites are assigned round-robin by seed, so any run of consecutive seeds splits evenly between them.
GENERATOR_POOL = [
    "claude-opus-5-5",
    "openai/gpt-6.1-sol",
    # Dropped 2026-10-27: Sonnet 5.5 and Kimi K3.1 Pro (keep the pool to the two strongest website models);
    # Gemini 4 Argon (ZDR only via Google Vertex, whose ZDR endpoints were down).
]

# Under the zero-data-retention policy only Sol's Azure endpoints are eligible. The default Azure pool rejects large
# requests at peak load ("exceeds the maximum usage size"), so prefer the regional pools.
PROVIDER_PREFS = {"openai/gpt-6.1-sol": {"order": ["azure/us", "azure/eu", "azure"], "allow_fallbacks": True}}

_anthropic: AsyncAnthropic | None = None
_openrouter: AsyncOpenAI | None = None


@dataclass
class Completion:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    seconds: float
    stop_reason: str


def _clients():
    global _anthropic, _openrouter
    if _anthropic is None:
        _anthropic = AsyncAnthropic(max_retries=4, timeout=900)
        _openrouter = AsyncOpenAI(base_url="https://openrouter.ai/api/v1",
                                  api_key=os.environ["OPENROUTER_API_KEY"], max_retries=4, timeout=900)
    return _anthropic, _openrouter


async def complete(model: str, system: str, user: str, max_tokens: int = 32000) -> Completion:
    anthropic, openrouter = _clients()
    t0 = time.time()
    if model.startswith("claude-"):
        async with anthropic.messages.stream(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            msg = await stream.get_final_message()
        text = "".join(b.text for b in msg.content if b.type == "text")
        return Completion(text, model, msg.usage.input_tokens, msg.usage.output_tokens,
                          time.time() - t0, msg.stop_reason or "")
    resp = await openrouter.chat.completions.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        extra_body={"provider": PROVIDER_PREFS[model]} if model in PROVIDER_PREFS else {},
    )
    if not resp.choices:  # OpenRouter reports some provider failures as a 200 with an error body
        err = (resp.model_extra or {}).get("error") or {}
        raise RuntimeError(f"provider error: {err.get('message', 'response has no choices')}"[:300])
    choice = resp.choices[0]
    usage = resp.usage
    return Completion(choice.message.content or "", model,
                      usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0,
                      time.time() - t0, choice.finish_reason or "")


async def complete_with_retry(model: str, system: str, user: str, max_tokens: int = 32000,
                              validate=None, attempts: int = 4) -> Completion:
    """Retry on API errors, truncation, or a failed `validate(text)` (which should raise), backing off 15/30/60s."""
    last = None
    for i in range(attempts):
        try:
            c = await complete(model, system, user, max_tokens)
            if c.stop_reason in ("max_tokens", "length"):
                raise ValueError(f"truncated at {c.output_tokens} tokens")
            if validate:
                validate(c.text)
            return c
        except Exception as e:  # noqa: BLE001 - surface the last error after all attempts
            last = e
            if i < attempts - 1:
                await asyncio.sleep(15 * 2 ** i)
    raise RuntimeError(f"{model} failed after {attempts} attempts: {last}")
