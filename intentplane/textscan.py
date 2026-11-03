"""Secrets in free text: a defence-in-depth heuristic, not a guarantee.

The gateway decides where a comment goes, never what it says. This looks for credentials in the text an agent
writes (a comment's or a PR's title and body), so a policy can refuse to publish one. It finds known token formats
and long high-entropy strings; anything encoded, split or paraphrased goes through, and some legitimate text is
refused (known false positives: base64 blobs and hashes other than hex digests, long random identifiers,
minified data, a key pasted on purpose in a security discussion).
"""
from __future__ import annotations

import math
import re
from collections import Counter

KNOWN = {
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "slack-token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    "private-key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
}
CANDIDATE = re.compile(r"[A-Za-z0-9+=_\-]{32,}")   # no "/": URLs and paths are common in comments
COMMIT = re.compile(r"^[0-9a-f]{40}$")
MIN_ENTROPY = 4.2   # bits per character; English words and hex stay well below, random base64 and tokens above


def entropy(s: str) -> float:
    counts = Counter(s)
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def secret_kinds(text: str) -> list[str]:
    """The kinds of secret found in `text`, empty when none."""
    kinds = [kind for kind, pattern in KNOWN.items() if pattern.search(text)]
    for candidate in CANDIDATE.findall(text):
        # Random tokens carry digits (about 15% in base64); long identifiers and words hardly any.
        digits = sum(c.isdigit() for c in candidate) / len(candidate)
        if COMMIT.match(candidate) or digits < 0.1 or not any(c.isalpha() for c in candidate):
            continue
        if entropy(candidate) >= MIN_ENTROPY:
            kinds.append("high-entropy")
            break
    return kinds
