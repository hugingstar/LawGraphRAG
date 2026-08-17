"""문장 단위 슬라이딩 윈도우 청킹.

조문 원문(수집 시)과 사고 진술문(질의 시) 모두 이 모듈로 청킹한다.
각 청크는 원문 기준 절대 char 오프셋(char_start, char_end)을 함께 반환하므로,
검색/인용 결과를 다시 원문 위치에 매핑할 수 있다.
"""

import re
from dataclasses import dataclass

# 한국어 문장 종결 부호(다./요./음./!/?) 뒤 공백 또는 개행 기준으로 분리
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class TextChunk:
    text: str
    char_start: int
    char_end: int


def split_sentences(text: str) -> list[tuple[str, int, int]]:
    """텍스트를 문장 단위로 분리하며 각 문장의 (text, start, end) 오프셋을 반환한다."""
    sentences = []
    pos = 0
    for part in _SENTENCE_END_RE.split(text):
        if not part:
            continue
        start = text.index(part, pos)
        end = start + len(part)
        sentences.append((part, start, end))
        pos = end
    return sentences


def merge_short_sentences(
    text: str, sentences: list[tuple[str, int, int]], min_chars: int
) -> list[tuple[str, int, int]]:
    """min_chars보다 짧은 조각을 뒤 문장과 이어붙여 하나의 단위로 만든다.

    법령 원문은 항·호·목마다 줄이 바뀌고 "①", "1." 같은 기호가 따로 떨어져 있어, 줄바꿈을
    문장 경계로 보면 수십 자짜리 조각이 쏟아진다(수집된 청크의 58%가 50자 미만이었다).
    그런 조각의 임베딩은 의미가 흐려서 어떤 질의와도 어중간하게 가깝고, 조문 하나가 청크
    수십 개로 늘어나 후보 풀만 잡아먹는다.

    병합 단위는 원문에서 연속된 구간이므로 (start, end) 오프셋은 그대로 유효하다."""
    if min_chars <= 0:
        return sentences

    merged: list[tuple[str, int, int]] = []
    buffer_start: int | None = None
    for _, start, end in sentences:
        if buffer_start is None:
            buffer_start = start
        if end - buffer_start >= min_chars:
            merged.append((text[buffer_start:end], buffer_start, end))
            buffer_start = None

    if buffer_start is not None:  # 마지막 자투리는 앞 단위에 붙인다
        last_end = sentences[-1][2]
        if merged:
            _, prev_start, _ = merged[-1]
            merged[-1] = (text[prev_start:last_end], prev_start, last_end)
        else:
            merged.append((text[buffer_start:last_end], buffer_start, last_end))
    return merged


def locate_quote(text: str, quote: str, within: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """원문에서 발췌문의 위치(start, end)를 찾는다. 못 찾으면 None.

    LLM에게 "원문에서 그대로 발췌하라"고 지시해도 공백·줄바꿈은 자주 달라진다. 그대로
    찾기에 실패하면 공백을 모두 뺀 문자열끼리 맞춰본 뒤 원문 오프셋으로 되돌린다 —
    이 폴백이 없으면 인용이 조용히 사라진다(app.annotate 참고).

    within: 이 구간을 먼저 뒤진다(같은 문구가 원문 여러 곳에 있을 때 엉뚱한 위치에
    앵커되는 것을 막는다). 구간에서 못 찾으면 원문 전체를 뒤진다."""
    quote = quote.strip()
    if not quote:
        return None

    if within is not None:
        idx = text.find(quote, within[0], within[1])
        if idx != -1:
            return idx, idx + len(quote)

    idx = text.find(quote)
    if idx != -1:
        return idx, idx + len(quote)

    # 공백 무시 매칭: 원문에서 공백이 아닌 글자만 모으고 그 위치를 함께 기억해 둔다.
    compact_chars: list[str] = []
    offsets: list[int] = []
    for i, ch in enumerate(text):
        if not ch.isspace():
            compact_chars.append(ch)
            offsets.append(i)
    target = "".join(ch for ch in quote if not ch.isspace())
    if not target:
        return None
    idx = "".join(compact_chars).find(target)
    if idx == -1:
        return None
    return offsets[idx], offsets[idx + len(target) - 1] + 1


def chunk_text(
    text: str,
    *,
    window_size: int = 3,
    overlap: int = 1,
    min_sentence_chars: int = 0,
) -> list[TextChunk]:
    """문장 단위 슬라이딩 윈도우로 청킹한다.

    window_size: 청크당 문장 수
    overlap: 인접 청크 간 겹치는 문장 수 (앞뒤 문맥 보존용)
    min_sentence_chars: 이보다 짧은 문장은 뒤 문장과 합쳐 한 단위로 센다(0이면 병합 안 함)
    """
    sentences = split_sentences(text)
    if min_sentence_chars > 0:
        sentences = merge_short_sentences(text, sentences, min_sentence_chars)
    if not sentences:
        return []

    step = max(window_size - overlap, 1)
    chunks: list[TextChunk] = []
    for i in range(0, len(sentences), step):
        window = sentences[i : i + window_size]
        if not window:
            continue
        start = window[0][1]
        end = window[-1][2]
        chunks.append(TextChunk(text=text[start:end], char_start=start, char_end=end))
        if i + window_size >= len(sentences):
            break
    return chunks
