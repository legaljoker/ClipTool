"""Optional Claude integration for smarter clip picks and better titles.

Only used when the `anthropic` package is installed and ANTHROPIC_API_KEY is set
(or the feature is forced on in config.yaml). Everything works without it.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from cliptool.ffmpeg_utils import fmt_time
from cliptool.logger import get_logger

log = get_logger("ai")


def ai_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def should_use(setting: Any) -> bool:
    if setting is True or str(setting).lower() in ("true", "yes", "on"):
        if not ai_available():
            log.warning("AI is turned on but 'anthropic' isn't installed or ANTHROPIC_API_KEY isn't set.")
            return False
        return True
    if str(setting).lower() == "auto":
        return ai_available()
    return False


def ask_json(prompt: str, schema: dict, cfg: dict, system: str = "") -> Optional[dict]:
    import anthropic

    client = anthropic.Anthropic()
    kwargs: dict[str, Any] = dict(
        model=cfg.get("model", "claude-opus-5-5"),
        max_tokens=16000,
        system=system or "You are an expert short-form video editor for TikTok and YouTube.",
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": cfg.get("effort", "medium"),
                       "format": {"type": "json_schema", "schema": schema}},
    )
    try:
        # Server-side fallback lets the request complete on another model if it is declined.
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                           **kwargs)
    except TypeError:  # older SDK without the fallbacks parameter
        resp = client.messages.create(**kwargs)
    if getattr(resp, "stop_reason", None) == "refusal":
        log.warning("Claude declined this request; using the built-in results instead.")
        return None
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    return json.loads(text) if text else None


def timed_transcript(transcript, limit_chars: int = 400_000) -> str:
    lines = [f"[{fmt_time(s.start)}-{fmt_time(s.end)}] {s.text.strip()}" for s in transcript.segments]
    text = "\n".join(lines)
    if len(text) > limit_chars:
        log.warning("Transcript is very long; sending the first %d characters to Claude.", limit_chars)
        text = text[:limit_chars]
    return text


CLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "ranking": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate": {"type": "integer"},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["candidate", "score", "reason", "title"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ranking"],
    "additionalProperties": False,
}


def make_clip_reranker(cfg: dict):
    def rerank(cands, transcript, count):
        listing = "\n\n".join(f"Candidate {i}: {fmt_time(c.start)}-{fmt_time(c.end)} "
                              f"({c.duration:.0f}s)\n{c.text}" for i, c in enumerate(cands))
        prompt = (
            "Below is a timed transcript of a video, followed by candidate clips found by an "
            f"automatic tool. Rank the candidates by how well they would perform as stand-alone "
            f"TikTok / YouTube Shorts (strong hook in the first 3 seconds, self-contained, emotional or "
            f"surprising payoff). Score each 0-10, give a one-sentence reason, and a catchy title "
            f"(max 70 characters). Return the best {count} or more.\n\n"
            f"TRANSCRIPT:\n{timed_transcript(transcript)}\n\nCANDIDATES:\n{listing}"
        )
        log.info("Asking Claude to rank %d clip candidates...", len(cands))
        data = ask_json(prompt, CLIP_SCHEMA, cfg)
        if not data:
            return cands
        ranked = []
        heur_max = max(c.score for c in cands) or 1.0
        for item in data.get("ranking", []):
            i = item.get("candidate")
            if isinstance(i, int) and 0 <= i < len(cands) and cands[i] not in ranked:
                c = cands[i]
                c.score = 0.6 * float(item.get("score", 0)) + 0.4 * (c.score / heur_max * 10)
                c.reasons = [f"AI: {item.get('reason', '').strip()}"] + c.reasons[:2]
                c.title = item.get("title", "").strip()
                ranked.append(c)
        rest = [c for c in cands if c not in ranked]
        ranked.sort(key=lambda c: -c.score)
        return ranked + rest

    return rerank


META_SCHEMA = {
    "type": "object",
    "properties": {
        "titles": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["titles", "description", "hashtags"],
    "additionalProperties": False,
}


def draft_metadata(transcript_text: str, platform: str, n_titles: int, n_tags: int,
                   cfg: dict, chapters: str = "") -> Optional[dict]:
    prompt = (
        f"Write upload metadata for a {platform} video from its transcript. Give {n_titles} title "
        f"options (punchy, honest, max 70 characters, no clickbait lies), one description "
        f"(2-4 short sentences, first line works as a hook"
        + (", then include these chapters exactly as given" if chapters else "")
        + f"), and {n_tags} relevant hashtags each starting with #.\n\n"
        + (f"CHAPTERS:\n{chapters}\n\n" if chapters else "")
        + f"TRANSCRIPT:\n{transcript_text[:200_000]}"
    )
    log.info("Asking Claude for %s title/description/hashtag drafts...", platform)
    return ask_json(prompt, META_SCHEMA, cfg)
