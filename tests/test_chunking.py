from app.chunking import chunk_text, split_sentences


def test_split_sentences_offsets_roundtrip():
    text = "지게차 작업 중 근로자가 넘어졌다. 안전모를 착용하지 않았다. 관리자는 확인하지 않았다."
    sentences = split_sentences(text)

    assert len(sentences) == 3
    for sentence_text, start, end in sentences:
        assert text[start:end] == sentence_text


def test_chunk_text_offsets_match_source():
    text = "문장 하나. 문장 둘. 문장 셋. 문장 넷. 문장 다섯."
    chunks = chunk_text(text, window_size=2, overlap=1)

    assert len(chunks) > 0
    for chunk in chunks:
        assert text[chunk.char_start : chunk.char_end] == chunk.text


def test_chunk_text_has_overlap_between_windows():
    text = "가나다. 라마바. 사아자. 차카타."
    chunks = chunk_text(text, window_size=2, overlap=1)

    assert len(chunks) >= 2
    assert chunks[0].char_end > chunks[1].char_start


def test_chunk_text_empty_input():
    assert chunk_text("") == []
