import re

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _split_long(paragraph: str, size: int) -> list[str]:
    """Split an oversize paragraph on sentence boundaries (hard cut as a last resort)."""
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        while len(sentence) > size:
            pieces.append(sentence[:size])
            sentence = sentence[size:]
        if current and len(current) + 1 + len(sentence) > size:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def chunk_text(text: str, size: int = 1500, overlap: int = 200) -> list[str]:
    """Greedy paragraph packing up to `size` chars; each chunk repeats the trailing
    paragraphs of the previous one (up to `overlap` chars) so context isn't cut mid-thought."""
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")

    paragraphs: list[str] = []
    for para in (p.strip() for p in text.split("\n\n")):
        if para:
            paragraphs.extend(_split_long(para, size) if len(para) > size else [para])

    chunks: list[str] = []
    window: list[str] = []
    for para in paragraphs:
        if window and len("\n\n".join([*window, para])) > size:
            chunks.append("\n\n".join(window))
            tail: list[str] = []
            for prev in reversed(window):
                if len("\n\n".join([prev, *tail])) > overlap:
                    break
                tail.insert(0, prev)
            if len("\n\n".join([*tail, para])) > size:
                tail = []
            window = tail
        window.append(para)
    if window:
        chunks.append("\n\n".join(window))
    return chunks
