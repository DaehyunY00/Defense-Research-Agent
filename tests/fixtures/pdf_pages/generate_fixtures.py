"""Generate the deterministic, dependency-free PDF parser fixtures in this folder."""

from __future__ import annotations

from hashlib import md5
from pathlib import Path
from struct import pack

FIXTURE_DIR = Path(__file__).parent
PDF_PASSWORD_PADDING = bytes.fromhex(
    "28bf4e5e4e758a4164004e56fffa01082e2e00b6d0683e802f0ca9fe6453697a"
)
# The fixture's PDF Standard Security Handler revision 2 requires MD5 by format.


def _stream(data: bytes) -> bytes:
    return (
        b"<< /Length " + str(len(data)).encode("ascii") + b" >>\nstream\n" + data + b"\nendstream"
    )


def _serialize_pdf(objects: list[bytes], *, trailer_extra: bytes = b"") -> bytes:
    output = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\x00\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{object_number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f\r\n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n\r\n".encode("ascii"))
    output.extend(
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode("ascii")
        + b" /Root 1 0 R"
        + trailer_extra
        + b" >>\nstartxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    return bytes(output)


def _text_stream(lines: tuple[str, ...]) -> bytes:
    commands = [b"BT /F1 14 Tf 72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append(b"0 -24 Td")
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(b"(" + escaped.encode("ascii") + b") Tj")
    commands.append(b"ET")
    return b"\n".join(commands)


def _plain_pdf(pages: tuple[tuple[str, ...] | None, ...], *, scanned: bool = False) -> bytes:
    page_object_numbers = [4 + index * 2 for index in range(len(pages))]
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{number} 0 R".encode("ascii") for number in page_object_numbers)
        + b"] /Count "
        + str(len(pages)).encode("ascii")
        + b" >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    for page_index, lines in enumerate(pages):
        page_number = page_object_numbers[page_index]
        content_number = page_number + 1
        if scanned and page_index == 0:
            image_number = page_object_numbers[-1] + 2
            resources = (
                b"<< /Font << /F1 3 0 R >> /XObject << /Im1 "
                + str(image_number).encode("ascii")
                + b" 0 R >> >>"
            )
            content = b"q 612 0 0 792 0 0 cm /Im1 Do Q"
        else:
            resources = b"<< /Font << /F1 3 0 R >> >>"
            content = b"" if lines is None else _text_stream(lines)
        objects.extend(
            [
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
                + resources
                + b" /Contents "
                + str(content_number).encode("ascii")
                + b" 0 R >>",
                _stream(content),
            ]
        )

    if scanned:
        objects.append(
            b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length 3 >>\n"
            b"stream\n\xff\xff\xff\nendstream"
        )
    return _serialize_pdf(objects)


def _rc4(key: bytes, value: bytes) -> bytes:
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) % 256
        state[i], state[j] = state[j], state[i]
    output = bytearray()
    i = j = 0
    for byte in value:
        i = (i + 1) % 256
        j = (j + state[i]) % 256
        state[i], state[j] = state[j], state[i]
        output.append(byte ^ state[(state[i] + state[j]) % 256])
    return bytes(output)


def _pad_password(password: bytes) -> bytes:
    return (password + PDF_PASSWORD_PADDING)[:32]


def _encrypted_pdf() -> bytes:
    user_password = b"fixture-password"
    owner_key = md5(_pad_password(user_password)).digest()[:5]
    owner_entry = _rc4(owner_key, _pad_password(user_password))
    permissions = -4
    file_id = bytes.fromhex("00112233445566778899aabbccddeeff")
    encryption_key = md5(
        _pad_password(user_password) + owner_entry + pack("<I", permissions & 0xFFFFFFFF) + file_id
    ).digest()[:5]
    user_entry = _rc4(encryption_key, PDF_PASSWORD_PADDING)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
        b"<< /Filter /Standard /V 1 /R 2 /Length 40 /O <"
        + owner_entry.hex().encode("ascii")
        + b"> /U <"
        + user_entry.hex().encode("ascii")
        + b"> /P -4 >>",
    ]
    trailer_extra = (
        b" /Encrypt 4 0 R /ID [<"
        + file_id.hex().encode("ascii")
        + b"><"
        + file_id.hex().encode("ascii")
        + b">]"
    )
    return _serialize_pdf(objects, trailer_extra=trailer_extra)


def main() -> None:
    fixtures = {
        "defense_forum.pdf": _plain_pdf((("Defense Forum page 1",), ("Defense Forum page 2",))),
        "kida_brief.pdf": _plain_pdf((("KIDA Brief page 1",),)),
        "defense_policy_research.pdf": _plain_pdf(
            (
                ("Policy Research page 1 line 1", "Policy Research page 1 line 2"),
                ("Policy Research page 2",),
            )
        ),
        "research_report.pdf": _plain_pdf(
            (("Research Report page 1",), None, ("Research Report page 3",))
        ),
        "empty_document.pdf": _plain_pdf((None, None)),
        "scanned_page.pdf": _plain_pdf((None,), scanned=True),
        "encrypted.pdf": _encrypted_pdf(),
        "corrupt.pdf": b"%PDF-1.7\nThis is not a valid PDF structure.\n",
        "invalid_header.pdf": b"not-a-pdf\n",
    }
    for name, content in fixtures.items():
        (FIXTURE_DIR / name).write_bytes(content)


if __name__ == "__main__":
    main()
