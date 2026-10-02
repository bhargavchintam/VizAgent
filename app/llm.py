"""LLM reasoning via W&B (CoreWeave) serverless inference, with optional Weave tracing."""

import json
import logging
import os
from functools import lru_cache

log = logging.getLogger("vizagent.llm")

WANDB_INFERENCE_URL = "https://api.inference.wandb.ai/v1"
# Override with LLM_MODEL; list what's available with `python -c "import llm; print(llm.list_models())"`.
DEFAULT_MODEL = "meta-llama/Llama-3.1-8B-Instruct"

try:
    import weave

    op = weave.op
except ImportError:  # tracing is optional; the app still runs without weave installed

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
    )


def list_models():
    return [m.id for m in client().models.list()]


def chat(messages, model=None, temperature=0.2, **kwargs):
    resp = client().chat.completions.create(
        model=model or os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        messages=messages,
        temperature=temperature,
        **kwargs,
    )
    return resp.choices[0].message.content


def chat_json(messages, model=None, **kwargs):
    """Ask for a JSON answer and parse it, tolerating ```json fences around it."""
    text = chat(messages, model=model, **kwargs).strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return json.loads(text)
