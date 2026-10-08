import io
import struct
import zlib

import pytest

from qrhost import cli, qr


def test_wifi_payload_escapes():
    assert qr.wifi_payload("Home", "pa;ss") == "WIFI:T:WPA;S:Home;P:pa\\;ss;;"
    assert qr.wifi_payload("Cafe") == "WIFI:T:nopass;S:Cafe;;"
    assert qr.wifi_payload('a:b,"c"\\', "x", "WEP", hidden=True) == 'WIFI:T:WEP;S:a\\:b\\,\\"c\\"\\\\;P:x;H:true;;'


def test_terminal_render_shapes():
    matrix = qr.make_matrix("hello")
    plain = qr.render_terminal(matrix, color=False)
    lines = plain.splitlines()
    assert len(lines) == (len(matrix) + 1) // 2
    assert all(len(line) == len(matrix) for line in lines)
    colored = qr.render_terminal(matrix, color=True)
    assert "\x1b[" in colored


def test_ascii_fallback_for_legacy_consoles():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="ascii")
    qr.print_qr("hello", stream=stream)
    assert b"##" in raw.getvalue()


def test_png_is_valid():
    png = qr.to_png("https://example.com", scale=4)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", png[16:24])
    assert width == height and width % 4 == 0
    idat_len = struct.unpack(">I", png[33:37])[0]
    raw = zlib.decompress(png[41:41 + idat_len])
    assert len(raw) == height * (width + 1)


def test_svg():
    svg = qr.to_svg("x")
    assert svg.startswith("<svg") and svg.endswith("</svg>")


def test_cli_url(capsys):
    assert cli.main(["https://example.com/a?b=1"]) == 0
    assert "https://example.com/a?b=1" in capsys.readouterr().out


def test_cli_text_and_save(tmp_path, capsys):
    out = tmp_path / "code.png"
    assert cli.main(["-t", "hello", "-s", str(out)]) == 0
    assert out.read_bytes().startswith(b"\x89PNG")
    svg = tmp_path / "code.svg"
    assert cli.main(["-w", "Home", "-P", "secret", "-s", str(svg)]) == 0
    assert svg.read_text().startswith("<svg")
    assert "Scan to join" in capsys.readouterr().out


def test_cli_errors(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["does-not-exist.txt"])
    assert "no such file" in str(exc.value)
    with pytest.raises(SystemExit):
        cli.main(["-t", "x", "-w", "y"])
    with pytest.raises(SystemExit) as exc:
        cli.main(["-t", "x" * 5000])
    assert "can't fit" in str(exc.value)
