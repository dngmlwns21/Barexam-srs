"""
HWP text extractor.
Reads BodyText/Section0 (zlib-compressed tag stream) from HWP 5.x files.
Tag 0x43 payloads are UTF-16-LE text runs in these exam files.
Falls back to PrvText if decompression fails.
"""

import struct
import zlib
import zipfile
import tempfile
import shutil
from pathlib import Path
from typing import List, Optional

import olefile


# Characters to keep when cleaning raw UTF-16 text.
# Be strict: the HWP shape payload includes many binary bytes that
# decode to Latin Extended / Hebrew / Greek / Coptic characters — exclude them.
_KEEP_RANGES = [
    (0x0020, 0x007E),   # ASCII printable (digits, letters, punctuation)
    (0x00B7, 0x00B7),   # Middle dot (interpunct, common in Korean)
    (0x2010, 0x2027),   # Hyphens, bullet, ellipsis
    (0x2030, 0x205E),   # Per-mille, prime, guillemots, etc.
    (0x2460, 0x24FF),   # Enclosed Alphanumerics  ①②③④⑤
    (0x3000, 0x303F),   # CJK Symbols (ideographic comma, period, brackets)
    (0x3130, 0x318F),   # Hangul Compatibility Jamo
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs (used in Korean legal names)
    (0xAC00, 0xD7A3),   # Hangul Syllables  ← primary Korean
    (0xFF00, 0xFFEF),   # Halfwidth and Fullwidth Forms
]


def _is_valid_char(ch: int) -> bool:
    if ch == 0x09 or ch == 0x0A or ch == 0x0D:   # tab, LF, CR
        return True
    return any(lo <= ch <= hi for lo, hi in _KEEP_RANGES)


def _decode_payload(payload: bytes) -> str:
    text = []
    for i in range(0, len(payload) - 1, 2):
        ch = struct.unpack_from("<H", payload, i)[0]
        if _is_valid_char(ch):
            c = chr(ch)
            text.append(c)
        else:
            # Treat un-decodable char as segment separator
            text.append(" ")
    return "".join(text)


def _extract_from_body(data: bytes) -> str:
    """Parse HWP5 record stream; return text from tag 0x43 payloads."""
    # Tag 0x43 carries text runs in exam HWP files.
    # Record header (4 bytes LE):  bits[9:0]=tag, bits[11:10]=level, bits[31:20]=size
    # If size == 0xFFF -> real size in next 4 bytes.
    segments: List[str] = []
    offset = 0
    while offset < len(data):
        if offset + 4 > len(data):
            break
        t = struct.unpack_from("<I", data, offset)[0]
        tag = t & 0x3FF
        size = t >> 20
        if size == 0xFFF:
            if offset + 8 > len(data):
                break
            size = struct.unpack_from("<I", data, offset + 4)[0]
            payload_start = offset + 8
        else:
            payload_start = offset + 4

        payload_end = payload_start + size
        if payload_end > len(data):
            break

        if tag == 0x43:
            payload = data[payload_start:payload_end]
            seg = _decode_payload(payload).strip()
            if seg:
                segments.append(seg)

        offset = payload_end

    return "\n".join(segments)


def _extract_prv_text(ole: olefile.OleFileIO) -> str:
    """Read PrvText stream (UTF-16-LE plain-text preview, limited ~1K chars)."""
    if not ole.exists("PrvText"):
        return ""
    raw = ole.openstream("PrvText").read()
    return raw.decode("utf-16-le", errors="replace")


def extract_hwp_text(filepath: str) -> str:
    """
    Return full text content of an HWP file.
    Priority: BodyText/Section0 (full) → PrvText (truncated preview).
    """
    try:
        ole = olefile.OleFileIO(filepath)
    except Exception:
        return ""

    text = ""
    try:
        if ole.exists("BodyText/Section0"):
            compressed = ole.openstream("BodyText/Section0").read()
            try:
                data = zlib.decompress(compressed, -15)
            except zlib.error:
                try:
                    data = zlib.decompress(compressed)
                except zlib.error:
                    data = b""
            if data:
                text = _extract_from_body(data)
        if not text.strip():
            text = _extract_prv_text(ole)
    finally:
        ole.close()

    return text


def collect_hwp_files(downloads_dir: Path) -> List[Path]:
    """
    Recursively find all .hwp files under downloads_dir.
    Also unzip any .zip archives (in-place temp expansion).
    Returns list of actual .hwp file paths.
    """
    import subprocess

    # Use Windows dir command to get correct Korean filenames
    result = subprocess.run(
        ["cmd", "/c", "dir", "/s", "/b", str(downloads_dir)],
        capture_output=True,
    )
    all_paths = result.stdout.decode("cp949", errors="replace").splitlines()

    hwp_files = [Path(p.strip()) for p in all_paths if p.strip().lower().endswith(".hwp")]
    zip_files = [Path(p.strip()) for p in all_paths if p.strip().lower().endswith(".zip")]

    # Extract ZIPs to sibling dirs
    for zp in zip_files:
        extract_dir = zp.parent / (zp.stem + "_extracted")
        if extract_dir.exists():
            # Already extracted; collect hwps from there
            for f in extract_dir.rglob("*.hwp"):
                hwp_files.append(f)
            continue
        try:
            extract_dir.mkdir(exist_ok=True)
            with zipfile.ZipFile(zp) as z:
                for member in z.infolist():
                    # Decode filename (cp437 or cp949)
                    try:
                        name = member.filename.encode("cp437").decode("cp949")
                    except Exception:
                        name = member.filename
                    if name.lower().endswith(".hwp"):
                        dest = extract_dir / Path(name).name
                        dest.write_bytes(z.read(member.filename))
                        hwp_files.append(dest)
        except Exception as exc:
            print(f"  [WARN] Could not unzip {zp.name}: {exc}")

    return hwp_files
