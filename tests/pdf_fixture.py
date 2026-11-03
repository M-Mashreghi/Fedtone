"""Builds small text PDFs for parser tests (one text line per entry, Helvetica 10pt)."""


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]]) -> bytes:
    objects: list[bytes] = []
    page_ids = []
    font_id = 3
    next_id = 4
    contents = []
    for lines in pages:
        stream_lines = ["BT", "/F1 10 Tf", "12 TL", "50 800 Td"]
        for line in lines:
            stream_lines.append(f"({_escape(line)}) Tj T*")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("latin-1")
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        page_ids.append(page_id)
        contents.append((page_id, content_id, stream))

    def obj(number: int, body: bytes) -> None:
        while len(objects) < number:
            objects.append(b"")
        objects[number - 1] = body

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    obj(2, f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    obj(font_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for page_id, content_id, stream in contents:
        obj(
            page_id,
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode(),
        )
        obj(content_id, b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)
