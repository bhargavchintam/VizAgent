"""Direct calls to the shared GPU models, for the live second look.

Cosmos3-Reason speaks the OpenAI chat API and takes a clip as a base64 video_url.
The docs disagree on whether these endpoints need a token, so the bearer header is
sent only when GPU_BEARER_TOKEN is set.
"""

import base64
import os
import re

import httpx

DEFAULT_MODEL = "nvidia/cosmos3-reason"


def configured():
    """True when the second look can run: the endpoint is known and not switched off."""
    return bool(os.environ.get("COSMOS3_REASON_URL")) and os.environ.get("VIZ_COSMOS", "1") != "0"


def parse_think_answer(text):
    """Split a '<think>…</think><answer>X</answer>' reply into (trace, letter)."""
    text = text or ""
    think = re.search(r"<think>(.*?)</think>", text, flags=re.DOTALL | re.IGNORECASE)
    answer = re.search(r"<answer>(.*?)</answer>", text, flags=re.DOTALL | re.IGNORECASE)
    rest = answer.group(1) if answer else re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    letter = re.search(r"\(?\b([A-D])\b\)?", rest)
    trace = (think.group(1) if think else text).strip()
    return trace[:1500], letter.group(1) if letter else None


def cosmos_verify(mp4_bytes, question, timeout=90.0):
    """Ask Cosmos3-Reason one question about one clip. Returns (trace, letter)."""
    token = os.environ.get("GPU_BEARER_TOKEN")
    video = "data:video/mp4;base64," + base64.b64encode(mp4_bytes).decode()
    response = httpx.post(
        os.environ["COSMOS3_REASON_URL"].rstrip("/") + "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"} if token else {},
        json={
            "model": os.environ.get("COSMOS3_REASON_MODEL", DEFAULT_MODEL),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {"type": "video_url", "video_url": {"url": video}},
                    ],
                }
            ],
            "max_tokens": 1024,
            "temperature": 0,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_think_answer(response.json()["choices"][0]["message"]["content"])
