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


def chunk_text(
    text: str,
    *,
    window_size: int = 3,
    overlap: int = 1,
) -> list[TextChunk]:
    """문장 단위 슬라이딩 윈도우로 청킹한다.

    window_size: 청크당 문장 수
    overlap: 인접 청크 간 겹치는 문장 수 (앞뒤 문맥 보존용)
    """
    sentences = split_sentences(text)
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
