import http.client
import io
import json
import os
import threading
import zipfile
from urllib.parse import quote

import pytest

from qrhost.server import App, Share, parse_range, safe_filename


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "share"
    (root / "sub").mkdir(parents=True)
    (root / "hello.txt").write_text("hello world")
    (root / "sub" / "deep.bin").write_bytes(bytes(range(256)) * 40)
    (root / ".env").write_text("SECRET=1")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("x")
    (root / "ünïcode name.txt").write_text("u")
    (tmp_path / "outside.txt").write_text("outside")
    return root


@pytest.fixture
def serve():
    apps = []

    def start(share, **kw):
        app = App(share, port=0, bind="127.0.0.1", host="127.0.0.1", **kw)
        threading.Thread(target=app.serve_forever, daemon=True).start()
        apps.append(app)
        return app

    yield start
    for app in apps:
        app.stop()


def request(app, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, data


def base(app):
    return f"/{app.token}/" if app.token else "/"


def test_token_is_required(tree, serve):
    app = serve(Share([str(tree)]))
    assert len(app.token) >= 8
    assert request(app, "GET", "/")[0].status == 404
    assert request(app, "GET", "/hello.txt")[0].status == 404
    assert request(app, "GET", "/wrong/hello.txt")[0].status == 404
    resp, _ = request(app, "GET", f"/{app.token}")
    assert resp.status == 301 and resp.getheader("Location") == f"/{app.token}/"
    assert app.url == f"http://127.0.0.1:{app.port}/{app.token}/"


def test_listing_and_download(tree, serve):
    app = serve(Share([str(tree)]))
    resp, page = request(app, "GET", base(app))
    assert resp.status == 200
    page = page.decode()
    assert "hello.txt" in page and "sub" in page and "ünïcode name.txt" in page
    assert ".env" not in page and ".git" not in page

    resp, data = request(app, "GET", base(app) + "hello.txt")
    assert resp.status == 200 and data == b"hello world"
    assert resp.getheader("Content-Type").startswith("text/plain")
    assert resp.getheader("Content-Disposition").startswith("inline")

    resp, _ = request(app, "GET", base(app) + "hello.txt?dl")
    assert resp.getheader("Content-Disposition").startswith("attachment")

    resp, data = request(app, "GET", base(app) + quote("ünïcode name.txt"))
    assert data == b"u"
    assert "filename*=UTF-8''%C3%BCn%C3%AFcode%20name.txt" in resp.getheader("Content-Disposition")


def test_head_and_folder_redirect(tree, serve):
    app = serve(Share([str(tree)]))
    resp, data = request(app, "HEAD", base(app) + "hello.txt")
    assert resp.status == 200 and data == b"" and resp.getheader("Content-Length") == "11"
    resp, _ = request(app, "GET", base(app) + "sub")
    assert resp.status == 301 and resp.getheader("Location") == base(app) + "sub/"
    resp, page = request(app, "GET", base(app) + "sub/")
    assert resp.status == 200 and b"deep.bin" in page


@pytest.mark.parametrize("path", [
    "../outside.txt", "%2e%2e/outside.txt", "sub/../../outside.txt", "..%2foutside.txt",
    "%2e%2e%2foutside.txt", ".env", ".git/config", "nope.txt", "hello.txt/",
])
def test_cannot_escape_or_see_hidden(tree, serve, path):
    app = serve(Share([str(tree)]))
    resp, data = request(app, "GET", base(app) + path)
    assert resp.status in (404, 301)
    assert b"outside" not in data and b"SECRET" not in data


def test_hidden_files_opt_in(tree, serve):
    app = serve(Share([str(tree)], show_hidden=True))
    assert request(app, "GET", base(app) + ".env")[1] == b"SECRET=1"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_symlink_out_of_share_is_blocked(tree, serve):
    try:
        os.symlink(tree.parent / "outside.txt", tree / "link.txt")
    except OSError:
        pytest.skip("cannot create symlinks here")
    app = serve(Share([str(tree)]))
    assert request(app, "GET", base(app) + "link.txt")[0].status == 404
    assert b"link.txt" not in request(app, "GET", base(app))[1]


def test_range_requests(tree, serve):
    app = serve(Share([str(tree)]))
    url = base(app) + "hello.txt"
    resp, data = request(app, "GET", url, headers={"Range": "bytes=0-4"})
    assert resp.status == 206 and data == b"hello"
    assert resp.getheader("Content-Range") == "bytes 0-4/11"
    resp, data = request(app, "GET", url, headers={"Range": "bytes=6-"})
    assert data == b"world"
    resp, data = request(app, "GET", url, headers={"Range": "bytes=-3"})
    assert data == b"rld"
    resp, _ = request(app, "GET", url, headers={"Range": "bytes=50-60"})
    assert resp.status == 416


def test_parse_range():
    assert parse_range(None, 10) is None
    assert parse_range("bytes=2-5", 10) == (2, 5)
    assert parse_range("bytes=2-500", 10) == (2, 9)
    assert parse_range("bytes=-4", 10) == (6, 9)
    assert parse_range("bytes=0-1,4-5", 10) is None
    assert parse_range("bytes=10-", 10) is False
    assert parse_range("bytes=0-", 0) is False


def test_zip_download(tree, serve):
    app = serve(Share([str(tree)]))
    resp, data = request(app, "GET", base(app) + "?zip")
    assert resp.status == 200 and resp.getheader("Content-Type") == "application/zip"
    names = sorted(zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert names == ["hello.txt", "sub/deep.bin", "ünïcode name.txt"]
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.read("sub/deep.bin") == bytes(range(256)) * 40

    resp, data = request(app, "GET", base(app) + "sub/?zip")
    assert zipfile.ZipFile(io.BytesIO(data)).namelist() == ["deep.bin"]
    assert 'filename="sub.zip"' in resp.getheader("Content-Disposition")


def test_single_file_share(tree, serve):
    app = serve(Share([str(tree / "hello.txt")]))
    resp, page = request(app, "GET", base(app))
    assert resp.status == 200 and b"hello.txt" in page and b"Download" in page
    assert request(app, "GET", base(app) + "hello.txt")[1] == b"hello world"
    # Siblings of a shared file are never reachable.
    assert request(app, "GET", base(app) + "sub/deep.bin")[0].status == 404
    assert request(app, "GET", base(app) + ".env")[0].status == 404


def test_multiple_paths(tree, serve, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "hello.txt").write_text("second")
    app = serve(Share([str(tree / "hello.txt"), str(other / "hello.txt"), str(tree / "sub")]))
    page = request(app, "GET", base(app))[1].decode()
    assert "hello (2).txt" in page and "sub" in page
    assert request(app, "GET", base(app) + quote("hello (2).txt"))[1] == b"second"
    assert request(app, "GET", base(app) + "sub/deep.bin")[0].status == 200
    names = zipfile.ZipFile(io.BytesIO(request(app, "GET", base(app) + "?zip")[1])).namelist()
    assert sorted(names) == ["hello (2).txt", "hello.txt", "sub/deep.bin"]


def test_qr_endpoint(tree, serve):
    app = serve(Share([str(tree)]))
    resp, data = request(app, "GET", base(app) + ".qrhost/qr.svg")
    assert resp.status == 200 and data.startswith(b"<svg")


def test_upload(tmp_path, serve):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    app = serve(Share(receive_dir=str(inbox)))
    resp, page = request(app, "GET", base(app))
    assert resp.status == 200 and b"Choose files" in page

    url = base(app) + ".qrhost/upload?name=" + quote("../../evil photo.jpg")
    resp, data = request(app, "POST", url, body=b"x" * 1000)
    assert resp.status == 200
    assert json.loads(data) == {"ok": True, "name": "evil photo.jpg"}
    assert (inbox / "evil photo.jpg").read_bytes() == b"x" * 1000

    resp, data = request(app, "POST", url, body=b"second")
    assert json.loads(data)["name"] == "evil photo (1).jpg"
    assert sorted(os.listdir(inbox)) == ["evil photo (1).jpg", "evil photo.jpg"]
    assert not (tmp_path / "evil photo.jpg").exists()
    assert app.count == 2


def test_upload_disabled_when_sharing(tree, serve):
    app = serve(Share([str(tree)]))
    resp, _ = request(app, "POST", base(app) + ".qrhost/upload?name=x", body=b"x")
    assert resp.status == 404
    assert not (tree / "x").exists()


def test_text_message(tmp_path, serve):
    got = []
    app = serve(Share(receive_dir=str(tmp_path)), out=lambda kind, text: got.append((kind, text)))
    resp, _ = request(app, "POST", base(app) + ".qrhost/text", body="hi 👋".encode())
    assert resp.status == 200
    assert got == [("msg", "message from 127.0.0.1:\nhi 👋")]


def test_once_stops_after_download(tree):
    app = App(Share([str(tree / "hello.txt")]), port=0, bind="127.0.0.1", host="127.0.0.1", once=True)
    t = threading.Thread(target=app.serve_forever, daemon=True)
    t.start()
    assert request(app, "GET", base(app) + "hello.txt")[1] == b"hello world"
    t.join(timeout=5)
    assert not t.is_alive()


def test_no_token(tree, serve):
    app = serve(Share([str(tree)]), token="")
    assert request(app, "GET", "/hello.txt")[1] == b"hello world"
    assert app.url.endswith(f":{app.port}/")


@pytest.mark.parametrize("raw,clean", [
    ("photo.jpg", "photo.jpg"),
    ("../../etc/passwd", "passwd"),
    ("C:\\Users\\me\\a.txt", "a.txt"),
    ("..", "upload"),
    ("", "upload"),
    ("bad<name>?.txt", "bad_name__.txt"),
    ("new\nline.txt", "new_line.txt"),
])
def test_safe_filename(raw, clean):
    assert safe_filename(raw) == clean
