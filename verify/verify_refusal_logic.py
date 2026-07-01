"""Regression test for the refusal-detection fix in lh_ai_engine.

Run from the repo root:  python verify/verify_refusal_logic.py

It imports the REAL _looks_like_refusal from src/lh_ai_engine.py (which pulls
in only the stdlib + lh_logging, no GUI deps), so it exercises the shipped code
rather than a copy. Asserts that legitimate translations which merely start
with a refusal-shaped clause ("I can't go today", "I won't be there") are NOT
misclassified, while genuine AI meta-refusals still are.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lh_ai_engine import _looks_like_refusal  # noqa: E402

# These MUST NOT be flagged — they are valid translations, not refusals.
VALID_TRANSLATIONS = [
    "I can't go today.",
    "I won't be there tomorrow.",
    "I cannot come to the party this weekend.",
    "I'm sorry, but I already have plans.",
    "I am unable to attend the meeting.",
    "I can not swim.",
    "I must decline the invitation politely.",
    "I will not tolerate this behavior!",
    "Sorry, I can't make it to dinner.",
    "See you tomorrow!",
]

# These SHOULD be flagged — genuine AI meta-refusals returned in place of a
# translation.
REAL_REFUSALS = [
    "I'm sorry, but I can't help with that request.",
    "I cannot assist with this request.",
    "As an AI language model, I cannot generate that content.",
    "I'm unable to help with that. It violates my content policy.",
    "I can't comply with that request.",
    "I cannot provide a translation for this content.",
    "As an AI, I won't do that.",
    "I must decline this request as it is not appropriate.",
]


def main() -> int:
    fails = 0
    for s in VALID_TRANSLATIONS:
        if _looks_like_refusal(s):
            print(f"FALSE POSITIVE (should be allowed): {s!r}")
            fails += 1
    for s in REAL_REFUSALS:
        if not _looks_like_refusal(s):
            print(f"FALSE NEGATIVE (should be flagged): {s!r}")
            fails += 1

    long_text = "I can't help with that request " + ("blah " * 120)
    if _looks_like_refusal(long_text):
        print("FAIL: overly long text flagged as refusal")
        fails += 1

    if fails == 0:
        print(f"PASS: {len(VALID_TRANSLATIONS)} valid + {len(REAL_REFUSALS)} "
              f"refusals + length cap all correct")
        return 0
    print(f"{fails} FAILURE(S)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
