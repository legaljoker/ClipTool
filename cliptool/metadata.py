"""Title, description and hashtag drafts generated from a transcript."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from cliptool import ai
from cliptool.logger import get_logger
from cliptool.textutil import content_words, hook_score, split_sentences, top_keywords
from cliptool.transcribe import Transcript

log = get_logger("metadata")

PLATFORM_TAGS = {
    "tiktok": ["#fyp", "#foryou", "#viral"],
    "youtube_shorts": ["#shorts", "#youtubeshorts"],
    "youtube": [],
    "instagram_reels": ["#reels", "#reelsinstagram"],
}

PLATFORM_LABEL = {"tiktok": "TikTok", "youtube_shorts": "YouTube Shorts", "youtube": "YouTube",
                  "instagram_reels": "Instagram Reels"}


@dataclass
class Metadata:
    platform: str
    titles: list[str]
    description: str
    hashtags: list[str]
    source: str = "built-in"
    keywords: list[str] = field(default_factory=list)


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" -,.")
    return s[:1].upper() + s[1:] if s else s


def _shorten(s: str, limit: int = 70) -> str:
    s = _clean(s)
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:-") + "..."


def _hashtag(word: str) -> str:
    parts = re.findall(r"[a-zA-Z0-9]+", word)
    return "#" + "".join(p[:1].upper() + p[1:] for p in parts) if parts else ""


def builtin_metadata(transcript: Transcript, platform: str, n_titles: int = 5, n_tags: int = 8,
                     chapters: str = "") -> Metadata:
    text = transcript.text
    keywords = top_keywords(text, 12)
    kw = [k for k in keywords if " " not in k] or keywords
    topic = keywords[0] if keywords else "this"
    sentences = split_sentences(text)

    # best hook-ish sentences of a reasonable length
    ranked = sorted((s for s in sentences if 4 <= len(s.split()) <= 16),
                    key=lambda s: -(hook_score(s) + sum(1 for w in content_words(s) if w in keywords) * 0.4))
    titles: list[str] = []
    for s in ranked:
        t = _shorten(s.rstrip("."))
        if t.lower() not in (x.lower() for x in titles):
            titles.append(t)
        if len(titles) >= 3:
            break
    Topic = topic.title()
    templates = [
        f"The Truth About {Topic}",
        f"{Topic}: What Nobody Tells You",
        f"I Can't Believe This About {Topic}",
        f"Why {Topic} Matters More Than You Think",
        f"{Topic} Explained in Under a Minute" if platform != "youtube" else f"Everything About {Topic}",
    ]
    if len(kw) > 1:
        templates.insert(1, f"{kw[0].title()} vs {kw[1].title()}?")
    for t in templates:
        if len(titles) >= n_titles:
            break
        if t not in titles:
            titles.append(t)

    # description: the sentences carrying the most keywords
    unique = list(dict.fromkeys(sentences))
    scored = sorted(unique, key=lambda s: -sum(1 for w in content_words(s) if w in keywords))
    picked = [s for s in unique if s in scored[:3]]  # keep original order
    description = " ".join(_clean(s) for s in picked)
    if len(description) > 400:
        description = _shorten(description, 400)

    tags: list[str] = []
    singles = [k for k in keywords if " " not in k]
    for k in singles + [k for k in keywords if " " in k]:
        tag = _hashtag(k)
        if tag and tag.lower() not in (t.lower() for t in tags) and len(tag) <= 25:
            tags.append(tag)
        if len(tags) >= max(0, n_tags - len(PLATFORM_TAGS.get(platform, []))):
            break
    tags += PLATFORM_TAGS.get(platform, [])

    if chapters:
        description = f"{description}\n\nChapters:\n{chapters}"
    description = f"{description}\n\n{' '.join(tags)}".strip()
    return Metadata(platform, titles[:n_titles], description, tags[:n_tags + 3], "built-in", keywords)


def generate(transcript: Transcript, platform: str, cfg: dict, ai_cfg: dict, chapters: str = "") -> Metadata:
    n_titles, n_tags = int(cfg.get("num_titles", 5)), int(cfg.get("num_hashtags", 8))
    base = builtin_metadata(transcript, platform, n_titles, n_tags, chapters)
    if ai.should_use(cfg.get("use_ai", "auto")) and transcript.text:
        try:
            data: Optional[dict] = ai.draft_metadata(transcript.text, PLATFORM_LABEL.get(platform, platform),
                                                     n_titles, n_tags, ai_cfg, chapters)
            if data:
                tags = [t if t.startswith("#") else f"#{t}" for t in data.get("hashtags", [])]
                for t in PLATFORM_TAGS.get(platform, []):
                    if t not in tags:
                        tags.append(t)
                desc = data.get("description", "").strip()
                if chapters and "00:00" not in desc:
                    desc += f"\n\nChapters:\n{chapters}"
                return Metadata(platform, data.get("titles", [])[:n_titles] or base.titles,
                                f"{desc}\n\n{' '.join(tags)}", tags, "Claude AI", base.keywords)
        except Exception as exc:
            log.warning("AI metadata failed (%s); using built-in drafts.", exc)
    return base
