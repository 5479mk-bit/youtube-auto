"""한국어 문장 조립에 필요한 최소한의 도구."""

from __future__ import annotations


def has_final_consonant(word: str) -> bool:
    """마지막 글자에 받침이 있는지. 한글이 아니면 없는 것으로 본다."""
    if not word:
        return False
    last = word[-1]
    if not ("가" <= last <= "힣"):
        return False
    return (ord(last) - ord("가")) % 28 != 0


def particle(word: str, with_final: str, without_final: str) -> str:
    """'카페이 2.9년로' 같은 문장이 나오지 않도록 조사를 골라 붙인다."""
    return f"{word}{with_final if has_final_consonant(word) else without_final}"
