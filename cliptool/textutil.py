"""Small text-analysis helpers shared by the clip finder, expressions and metadata drafts."""
from __future__ import annotations

import re
from collections import Counter

STOPWORDS = set("""
a about above after again against all also am an and any are aren't as at be because been before being
below between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down
during each few for from further get got gonna had hadn't has hasn't have haven't having he he'd he'll
he's her here here's hers herself him himself his how how's i i'd i'll i'm i've if in into is isn't it
it's its itself just kind know let's like lot me more most mustn't my myself no nor not now of off on
once only or other ought our ours ourselves out over own really right same say said she she'd she'll
she's should shouldn't so some such than that that's the their theirs them themselves then there
there's these they they'd they'll they're they've thing things think this those through to too um uh
under until up us very was wasn't we we'd we'll we're we've well were weren't what what's when when's
where where's which while who who's whom why why's will with won't would wouldn't yeah yes you you'd
you'll you're you've your yours yourself yourselves okay ok oh actually basically literally gonna wanna
going go goes went one two get gets getting make made want mean even still something anything thing
way back much many every pretty sure maybe probably see look little bit
""".split())

EMOTION_WORDS: dict[str, set[str]] = {
    "happy": set("""happy glad love loved loving great awesome amazing nice good fun funny enjoy enjoyed
        beautiful wonderful fantastic perfect excellent cool sweet thanks thank laugh laughing haha lol
        smile yay best favorite delighted pleased proud lucky win won winning""".split()),
    "excited": set("""wow omg insane crazy incredible unbelievable epic huge massive hyped excited
        can't-wait finally let's lets go woo woohoo whoa yes""".split()),
    "sad": set("""sad sorry miss missed lost lose losing cry crying cried hurt hurts pain lonely alone
        depressed depressing unfortunately tragic heartbroken disappointed disappointing regret tired
        died death dead unhappy upset""".split()),
    "mad": set("""annoying annoyed annoys frustrating frustrated ugh seriously ridiculous whatever
        irritating irritated bothered bother stupid dumb nonsense""".split()),
    "angry": set("""angry hate hated furious rage pissed outraged disgusting unacceptable damn hell
        terrible horrible awful worst liar lied screw shut kill""".split()),
    "surprised": set("""what really seriously no-way wait surprised surprise shocked shocking whoa
        unexpected suddenly""".split()),
    "scared": set("""scared afraid fear terrified terrifying nervous anxious worried worry scary creepy
        panic""".split()),
}

HOOK_PHRASES = [
    "here's", "here is", "the secret", "secret", "nobody", "no one", "never", "always", "stop", "mistake",
    "biggest", "best", "worst", "how to", "why", "you need", "you won't believe", "the truth", "truth is",
    "did you know", "listen", "watch", "the reason", "this is", "number one", "first", "imagine",
    "what if", "i can't believe", "the problem", "most people", "everyone", "nobody tells you",
]


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z][a-zA-Z'\-]*", text.lower())


def content_words(text: str) -> list[str]:
    return [t for t in tokenize(text) if len(t) > 2 and t not in STOPWORDS]


def top_keywords(text: str, n: int = 10) -> list[str]:
    words = content_words(text)
    counts = Counter(words)
    bigrams = Counter(f"{a} {b}" for a, b in zip(words, words[1:]))
    scored = [(w, c * (1.0 + min(len(w), 10) / 10)) for w, c in counts.items()]
    scored += [(b, c * 2.2) for b, c in bigrams.items() if c >= 2]
    scored.sort(key=lambda x: -x[1])
    out: list[str] = []
    for word, _ in scored:
        if any(word in o or o in word for o in out):
            continue
        out.append(word)
        if len(out) >= n:
            break
    return out


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def emotion_counts(text: str) -> Counter:
    tokens = tokenize(text)
    joined = " ".join(tokens)
    counts: Counter = Counter()
    for emo, words in EMOTION_WORDS.items():
        for w in words:
            if "-" in w:
                if w.replace("-", " ") in joined:
                    counts[emo] += 1
            else:
                counts[emo] += tokens.count(w)
    return counts


def hook_score(text: str) -> float:
    low = text.lower()
    score = sum(1.0 for p in HOOK_PHRASES if p in low)
    if "?" in text:
        score += 1.0
    if re.search(r"\b\d+\b", text):
        score += 0.5
    if re.search(r"\byou\b", low):
        score += 0.5
    return score
