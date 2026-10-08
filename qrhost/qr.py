"""QR code rendering: terminal, SVG and PNG, with no image libraries required."""

import os
import struct
import sys
import zlib

import qrcode
from qrcode.constants import ERROR_CORRECT_M


def make_matrix(data, border=2):
    """Return the QR code for ``data`` as a list of rows of booleans (True = dark)."""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    return [list(row) for row in qr.get_matrix()]


def _supports_color(stream):
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty() and os.environ.get("TERM") != "dumb"


def render_terminal(matrix, color=True, invert=False):
    """Render a matrix with half-block characters, two QR rows per text line.

    With ``color`` the code is drawn black-on-white using ANSI colors, so it
    scans correctly on both light and dark terminal themes. Without color it
    falls back to plain characters, drawn for a dark terminal unless ``invert``.
    """
    rows = matrix if len(matrix) % 2 == 0 else matrix + [[False] * len(matrix[0])]
    lines = []
    for top, bottom in zip(rows[0::2], rows[1::2]):
        if color:
            parts, last = [], None
            for t, b in zip(top, bottom):
                colors = (30 if t else 97, 40 if b else 107)  # black / bright white
                if colors != last:
                    parts.append("\x1b[%d;%dm" % colors)
                    last = colors
                parts.append("▀")
            lines.append("".join(parts) + "\x1b[0m")
        else:
            line = []
            for t, b in zip(top, bottom):
                # On a dark terminal, drawn glyphs are light, so draw the light modules.
                t, b = (t, b) if invert else (not t, not b)
                line.append({(True, True): "█", (True, False): "▀",
                             (False, True): "▄", (False, False): " "}[(t, b)])
            lines.append("".join(line))
    return "\n".join(lines)


def _can_draw_blocks(stream):
    try:
        "\u2580\u2584\u2588".encode(getattr(stream, "encoding", None) or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def print_qr(data, stream=None, invert=False):
    stream = stream or sys.stdout
    matrix = make_matrix(data)
    if _can_draw_blocks(stream):
        stream.write(render_terminal(matrix, color=_supports_color(stream), invert=invert) + "\n")
    else:
        # Legacy console encodings can't draw block characters: use two ASCII chars per module.
        for row in matrix:
            stream.write("".join("##" if dark == invert else "  " for dark in row) + "\n")
    stream.flush()


def to_svg(data, scale=10):
    matrix = make_matrix(data, border=4)
    size = len(matrix)
    path = "".join(
        f"M{x},{y}h1v1h-1z"
        for y, row in enumerate(matrix)
        for x, dark in enumerate(row)
        if dark
    )
    px = size * scale
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'width="{px}" height="{px}" shape-rendering="crispEdges">'
        f'<rect width="{size}" height="{size}" fill="#fff"/>'
        f'<path d="{path}" fill="#000"/></svg>'
    )


def to_png(data, scale=10):
    """Encode the QR code as an 8-bit grayscale PNG using only the standard library."""
    matrix = make_matrix(data, border=4)
    raw = bytearray()
    for row in matrix:
        line = b"\x00" + b"".join((b"\x00" if dark else b"\xff") * scale for dark in row)
        raw += line * scale
    size = len(matrix) * scale

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def save(data, path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".svg":
        with open(path, "w", encoding="utf-8") as f:
            f.write(to_svg(data))
    elif ext == ".png":
        with open(path, "wb") as f:
            f.write(to_png(data))
    else:
        raise ValueError("QR image must end in .png or .svg")


def wifi_payload(ssid, password=None, security=None, hidden=False):
    """Build a Wi-Fi network QR payload that phone cameras join with one tap."""

    def esc(value):
        for ch in '\\;,:"':
            value = value.replace(ch, "\\" + ch)
        return value

    if security is None:
        security = "WPA" if password else "nopass"
    parts = [f"T:{security}", f"S:{esc(ssid)}"]
    if password and security != "nopass":
        parts.append(f"P:{esc(password)}")
    if hidden:
        parts.append("H:true")
    return "WIFI:" + ";".join(parts) + ";;"
