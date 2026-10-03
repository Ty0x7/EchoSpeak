"""Common English words that say nothing about what a text is about.

Used where relevance is scored by shared words (memory recall, past-chat
search): without this, "is", "my" and "the" make every memory look related.
"""

from __future__ import annotations

import re

STOPWORDS = frozenset("""
a an the and or but if then so to of in on at by for with from into onto about as is are was were be been being
am do does did doing done have has had having i me my mine myself you your yours yourself we us our ours they them
their theirs he him his she her hers it its this that these those there here what which who whom whose when where
why how all any both each few more most other some such no nor not only own same than too very can could will
would shall should may might must just also now please thanks thank hi hello hey ok okay yes yeah sure let lets
get got make made want need like know think tell say said see look go going gonna one two up down out over again
it's i'm i've i'll you're don't can't won't didn't isn't
""".split())


def keywords(text: str, limit: int = 0) -> list[str]:
    """Distinct content words of 3+ letters, in order of first use."""
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9'_-]{2,}", str(text or "").casefold()) if w not in STOPWORDS]
    out = list(dict.fromkeys(words))
    return out[:limit] if limit else out
