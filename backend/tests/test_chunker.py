import pytest

from app.rag.chunker import chunk_text


def test_short_text_is_single_chunk() -> None:
    assert chunk_text("one\n\ntwo", size=100, overlap=10) == ["one\n\ntwo"]


def test_chunks_respect_size_and_overlap() -> None:
    paragraphs = [f"Paragraph {i} " + "x" * 80 for i in range(20)]
    chunks = chunk_text("\n\n".join(paragraphs), size=400, overlap=100)

    assert len(chunks) > 1
    assert all(len(c) <= 400 for c in chunks)
    # Every paragraph survives, and consecutive chunks share their boundary paragraph.
    for p in paragraphs:
        assert any(p in c for c in chunks)
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert prev.split("\n\n")[-1] == nxt.split("\n\n")[0]


def test_oversize_paragraph_split_on_sentences() -> None:
    para = " ".join(f"Sentence number {i} ends here." for i in range(50))
    chunks = chunk_text(para, size=200, overlap=0)

    assert all(len(c) <= 200 for c in chunks)
    assert all(c.endswith(".") for c in chunks)


def test_overlap_must_be_smaller_than_size() -> None:
    with pytest.raises(ValueError):
        chunk_text("abc", size=10, overlap=10)
