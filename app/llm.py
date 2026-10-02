"""LLM reasoning via W&B (CoreWeave) serverless inference, with optional Weave tracing."""

import json
import logging
import os
import re
from concurrent import futures
from functools import lru_cache

log = logging.getLogger("vizagent.llm")

WANDB_INFERENCE_URL = "https://api.inference.wandb.ai/v1"
# Override with LLM_MODEL / LLM_FALLBACK_MODEL; list what's available with
# `python -c "import llm; print(llm.list_models())"`.
DEFAULT_MODEL = "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B"
FALLBACK_MODEL = "openai/gpt-oss-20b"

try:
    import weave

    op = weave.op
    # Weave's executor carries the current trace into worker threads, so parallel
    # checks show up nested under the sweep that started them.
    ThreadPoolExecutor = getattr(weave, "ThreadPoolExecutor", futures.ThreadPoolExecutor)
except ImportError:  # tracing is optional; the app still runs without weave installed
    ThreadPoolExecutor = futures.ThreadPoolExecutor

    def op(fn=None, **_kwargs):
        return fn if fn is not None else (lambda f: f)


def wandb_project():
    team, project = os.environ.get("WANDB_TEAM"), os.environ.get("WANDB_PROJECT")
    return f"{team}/{project}" if team and project else None


def configured():
    return bool(os.environ.get("WANDB_API_KEY"))


def init_tracing():
    """Start Weave tracing if W&B is configured. Returns True when tracing is on."""
    project = wandb_project()
    if not (configured() and project):
        return False
    try:
        import weave

        weave.init(project)
        return True
    except Exception as exc:  # never let tracing take the app down
        log.warning("Weave tracing disabled: %s", exc)
        return False


@lru_cache(maxsize=1)
def client():
    import openai

    return openai.OpenAI(
        base_url=WANDB_INFERENCE_URL,
        api_key=os.environ["WANDB_API_KEY"],
        project=wandb_project(),
        timeout=60.0,
        max_retries=1,
    )


def list_models():
    return [m.id for m in client().models.list()]


def models():
    """The model to use, then the fallback to try if it errors."""
    primary = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    fallback = os.environ.get("LLM_FALLBACK_MODEL", FALLBACK_MODEL)
    return [primary] if fallback == primary else [primary, fallback]


def chat(messages, model=None, temperature=0.2, **kwargs):
    candidates = [model] if model else models()
    for i, name in enumerate(candidates):
        try:
            resp = client().chat.completions.create(
                model=name, messages=messages, temperature=temperature, **kwargs
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:
            if i == len(candidates) - 1:
                raise
            log.warning("model %s failed (%s); trying %s", name, type(exc).__name__, candidates[i + 1])


def extract_json(text):
    """Parse the JSON object in a model reply, ignoring <think> blocks, fences and chatter."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model reply")
    return json.loads(text[start : end + 1])


def chat_json(messages, model=None, **kwargs):
    """Ask for a JSON answer and parse it."""
    return extract_json(chat(messages, model=model, **kwargs))
