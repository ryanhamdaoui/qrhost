"""The HTTP server: browse and download shared files, stream media, zip folders, receive uploads."""

import io
import json
import mimetypes
import os
import re
import secrets
import socket
import tempfile
import threading
import time
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import __version__, qr, ui

ROOT = object()  # the virtual root that lists several shared paths
RESERVED = ".qrhost"  # URL segment for qrhost's own endpoints
CHUNK = 1 << 16
MAX_TEXT = 64 * 1024

# Already-compressed formats are stored as-is in zips; deflating them only burns CPU.
STORED_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".avif",
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mp3", ".m4a", ".aac",
    ".ogg", ".opus", ".flac", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z",
    ".rar", ".zst", ".apk", ".ipa", ".jar", ".docx", ".xlsx", ".pptx", ".pdf",
}


def _file_mode():
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


FILE_MODE = _file_mode()  # uploads get normal permissions, not mkstemp's private 0600


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _within(path, base):
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def _hidden(name):
    return name.startswith(".")


def safe_filename(name):
    """Reduce an uploaded file name to a harmless single path component."""
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip().rstrip(".")
    if name in ("", ".", ".."):
        name = "upload"
    return name[:200]


def reserve_unique(directory, name):
    """Atomically create an empty file named ``name`` (or ``name (n)``) and return its path."""
    stem, ext = os.path.splitext(name)
    n = 0
    while True:
        candidate = name if n == 0 else f"{stem} ({n}){ext}"
        path = os.path.join(directory, candidate)
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return path
        except FileExistsError:
            n += 1


def parse_range(header, size):
    """Parse a single ``bytes=`` range. Returns (start, end), None to ignore, or False if unsatisfiable."""
    m = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", header or "")
    if not m or m.groups() == ("", ""):
        return None
    first, last = m.groups()
    if first == "":
        length = int(last)
        if length == 0 or size == 0:
            return False
        return max(0, size - length), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        return False
    return start, end


class Share:
    """The set of paths being shared, and safe resolution of URL paths onto them."""

    def __init__(self, paths=(), receive_dir=None, show_hidden=False):
        self.show_hidden = show_hidden
        self.receive_dir = os.path.realpath(receive_dir) if receive_dir else None
        self.root_dir = None
        self.entries = {}

        paths = [os.path.realpath(p) for p in paths]
        if len(paths) == 1 and os.path.isdir(paths[0]):
            self.root_dir = paths[0]
        else:
            for path in paths:
                name = os.path.basename(path) or path
                stem, ext = os.path.splitext(name)
                n = 1
                while name in self.entries:
                    n += 1
                    name = f"{stem} ({n}){ext}"
                self.entries[name] = path

    @property
    def receiving(self):
        return self.receive_dir is not None

    @property
    def single_file(self):
        if self.root_dir is None and len(self.entries) == 1:
            (path,) = self.entries.values()
            return path if os.path.isfile(path) else None
        return None

    @property
    def title(self):
        if self.receiving:
            return os.path.basename(self.receive_dir) or self.receive_dir
        if self.root_dir:
            return os.path.basename(self.root_dir) or self.root_dir
        if len(self.entries) == 1:
            return next(iter(self.entries))
        return "Shared files"

    def resolve(self, parts):
        """Map URL path segments to (real_path, base_dir), ROOT, or None if missing or forbidden."""
        for part in parts:
            if part in ("", ".", "..") or "/" in part or "\\" in part or "\0" in part:
                return None
        if self.root_dir is not None:
            base, rest = self.root_dir, parts
        else:
            if not parts:
                return ROOT
            base = self.entries.get(parts[0])
            rest = parts[1:]
            if base is None or (rest and not os.path.isdir(base)):
                return None
        if not self.show_hidden and any(_hidden(p) for p in rest):
            return None
        real = os.path.realpath(os.path.join(base, *rest))
        if not _within(real, base) or not os.path.exists(real):
            return None
        return real, base

    def listing(self, target):
        """Return the visible children of a directory target (or ROOT), folders first."""
        items = []
        if target is ROOT:
            children = [(name, path, path) for name, path in self.entries.items()]
        else:
            real, base = target
            try:
                names = os.listdir(real)
            except OSError:
                names = []
            children = [
                (name, os.path.realpath(os.path.join(real, name)), base)
                for name in names
                if self.show_hidden or not _hidden(name)
            ]
        for name, path, base in children:
            if not _within(path, base):
                continue  # symlink pointing outside the share
            try:
                st = os.stat(path)
            except OSError:
                continue
            is_dir = os.path.isdir(path)
            items.append({"name": name, "is_dir": is_dir,
                          "size": 0 if is_dir else st.st_size, "mtime": st.st_mtime})
        items.sort(key=lambda i: (not i["is_dir"], i["name"].casefold()))
        return items

    def walk(self, target, prefix=""):
        """Yield (real_path, archive_name) for every file under a target, for zipping."""
        if target is ROOT:
            for name, path in self.entries.items():
                if os.path.isdir(path):
                    yield from self.walk((path, path), name + "/")
                else:
                    yield path, name
            return
        top, base = target
        for dirpath, dirnames, filenames in os.walk(top):
            if not self.show_hidden:
                dirnames[:] = [d for d in dirnames if not _hidden(d)]
                filenames = [f for f in filenames if not _hidden(f)]
            dirnames.sort()
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                if os.path.isfile(full) and _within(os.path.realpath(full), base):
                    rel = os.path.relpath(full, top).replace(os.sep, "/")
                    yield full, prefix + rel


class _StreamWriter(io.RawIOBase):
    """A non-seekable file object over the response, so zipfile streams straight to the client."""

    def __init__(self, wfile):
        self.wfile = wfile
        self.written = 0

    def writable(self):
        return True

    def write(self, b):
        self.wfile.write(b)
        self.written += len(b)
        return len(b)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets two servers share a port, hiding "port in use" errors.
    allow_reuse_address = os.name != "nt"


class Handler(BaseHTTPRequestHandler):
    server_version = "qrhost/" + __version__
    sys_version = ""
    timeout = 120  # drop connections idle this long so stuck clients don't pin threads

    # -- plumbing -------------------------------------------------------------

    @property
    def app(self):
        return self.server.app

    def log_message(self, format, *args):
        pass  # qrhost prints its own friendly activity log

    def _route(self):
        """Split the request path into (segments after the token, query) or None if not ours."""
        url = urlsplit(self.path)
        segments = [unquote(s) for s in url.path.split("/")]
        segments = segments[1:]  # leading empty segment from the initial "/"
        token = self.app.token
        if token:
            if not segments or segments[0] != token:
                return None
            if len(segments) == 1:  # "/token" -> "/token/"
                self._redirect(f"/{token}/")
                return "handled"
            segments = segments[1:]
        trailing = bool(segments) and segments[-1] == ""
        parts = [s for s in segments if s != ""]
        return parts, trailing, parse_qs(url.query, keep_blank_values=True)

    def _redirect(self, location):
        self.send_response(HTTPStatus.MOVED_PERMANENTLY)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_body(self, body, content_type, status=HTTPStatus.OK, head=False, headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _html(self, page, status=HTTPStatus.OK, head=False):
        self._send_body(page, "text/html; charset=utf-8", status, head)

    def _not_found(self, head=False):
        self._html(ui.error_page("Not found", "This link is wrong, or the file is no longer shared."),
                   HTTPStatus.NOT_FOUND, head)

    def _url(self, parts, is_dir=False):
        prefix = f"/{self.app.token}" if self.app.token else ""
        path = "/".join(quote(p) for p in parts)
        return f"{prefix}/{path}{'/' if is_dir and path else ''}"

    # -- GET / HEAD ---------------------------------------------------------------

    def do_GET(self):
        self._get(head=False)

    def do_HEAD(self):
        self._get(head=True)

    def _get(self, head):
        route = self._route()
        if route == "handled":
            return
        if route is None:
            return self._not_found(head)
        parts, trailing, query = route

        if parts and parts[0] == RESERVED:
            if parts[1:] == ["qr.svg"]:
                return self._send_body(qr.to_svg(self.app.url), "image/svg+xml", head=head)
            return self._not_found(head)

        share = self.app.share
        if share.receiving:
            if parts:
                return self._not_found(head)
            return self._html(ui.receive_page(share.title, self._url([RESERVED]), self.app.url), head=head)

        target = share.resolve(parts)
        if target is None:
            return self._not_found(head)

        if target is ROOT and share.single_file:
            name = next(iter(share.entries))
            path = share.single_file
            return self._html(ui.file_page(
                name=name, size=human_size(os.path.getsize(path)),
                url=self._url([name]), mime=mimetypes.guess_type(name)[0] or "",
                share_url=self.app.url), head=head)

        is_dir = target is ROOT or os.path.isdir(target[0])
        if is_dir:
            if parts and not trailing:
                return self._redirect(self._url(parts, is_dir=True))
            if "zip" in query:
                return self._send_zip(target, parts, head)
            items = share.listing(target)
            for item in items:
                item["url"] = self._url(parts + [item["name"]], item["is_dir"])
                item["size_text"] = "" if item["is_dir"] else human_size(item["size"])
            crumbs = [(share.title, self._url([], True))]
            crumbs += [(part, self._url(parts[: i + 1], True)) for i, part in enumerate(parts)]
            total = sum(item["size"] for item in items)
            return self._html(ui.listing_page(
                title=share.title, crumbs=crumbs, items=items, total_size=human_size(total) if total else "",
                zip_url=self._url(parts, True) + "?zip", share_url=self.app.url), head=head)

        if trailing:
            return self._redirect(self._url(parts))
        self._send_file(target[0], parts[-1], "dl" in query, head)

    def _send_file(self, path, name, attachment, head):
        try:
            f = open(path, "rb")
        except OSError:
            return self._not_found(head)
        with f:
            size = os.fstat(f.fileno()).st_size
            byte_range = parse_range(self.headers.get("Range"), size)
            if byte_range is False:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = byte_range or (0, size - 1)
            length = max(0, end - start + 1)

            self.send_response(HTTPStatus.PARTIAL_CONTENT if byte_range else HTTPStatus.OK)
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if mime.startswith("text/"):
                mime += "; charset=utf-8"
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Disposition", ui.content_disposition(name, attachment))
            if byte_range:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head:
                return

            if not byte_range:
                self.app.log("down", f"{self.client_address[0]} is downloading {name} ({human_size(size)})")
            elif start == 0:
                self.app.log("down", f"{self.client_address[0]} is streaming {name}")
            began = time.monotonic()
            try:
                self.wfile.flush()
                if length:
                    self.connection.sendfile(f, offset=start, count=length)
            except (ConnectionError, socket.timeout):
                if not byte_range:
                    self.app.log("warn", f"{self.client_address[0]} cancelled {name}")
                return
            if not byte_range:
                self.app.log("done", f"{name} sent in {ui.duration(time.monotonic() - began)}")
                self.app.completed()

    def _send_zip(self, target, parts, head):
        name = (parts[-1] if parts else self.app.share.title.replace(" ", "-")) + ".zip"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", ui.content_disposition(name, True))
        self.send_header("Connection", "close")  # length unknown up front: end of stream ends the file
        self.end_headers()
        if head:
            return
        self.close_connection = True
        self.app.log("down", f"{self.client_address[0]} is downloading {name}")
        out = _StreamWriter(self.wfile)
        try:
            with zipfile.ZipFile(out, "w", allowZip64=True) as zf:
                for path, arcname in self.app.share.walk(target):
                    ext = os.path.splitext(path)[1].lower()
                    method = zipfile.ZIP_STORED if ext in STORED_EXTS else zipfile.ZIP_DEFLATED
                    try:
                        zf.write(path, arcname, compress_type=method, compresslevel=6)
                    except (FileNotFoundError, PermissionError):
                        continue
        except (ConnectionError, socket.timeout):
            self.app.log("warn", f"{self.client_address[0]} cancelled {name}")
            return
        self.app.log("done", f"{name} sent ({human_size(out.written)})")
        self.app.completed()

    # -- POST: phone -> computer ------------------------------------------------------

    def do_POST(self):
        route = self._route()
        if route in (None, "handled"):
            return self._not_found() if route is None else None
        parts, _, query = route
        share = self.app.share
        if not share.receiving or parts[:1] != [RESERVED] or len(parts) != 2:
            return self._not_found()

        length = self.headers.get("Content-Length")
        if length is None or not length.isdigit():
            return self._send_body("Content-Length required", "text/plain", HTTPStatus.LENGTH_REQUIRED)
        length = int(length)

        if parts[1] == "text":
            if length > MAX_TEXT:
                return self._send_body("Too long", "text/plain", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            text = self.rfile.read(length).decode("utf-8", "replace")
            self.app.message(self.client_address[0], text)
            return self._send_body('{"ok":true}', "application/json")

        if parts[1] != "upload":
            return self._not_found()
        name = safe_filename((query.get("name") or ["upload"])[0])
        directory = share.receive_dir
        fd, tmp = tempfile.mkstemp(prefix=".qrhost-", suffix=".part", dir=directory)
        try:
            with os.fdopen(fd, "wb") as out:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ConnectionError("client went away")
                    out.write(chunk)
                    remaining -= len(chunk)
            os.chmod(tmp, FILE_MODE)
            final = reserve_unique(directory, name)
            os.replace(tmp, final)
        except (ConnectionError, socket.timeout, OSError) as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            self.app.log("warn", f"upload of {name} from {self.client_address[0]} failed: {exc}")
            self.close_connection = True
            return
        saved = os.path.basename(final)
        self.app.log("up", f"received {saved} ({human_size(length)}) from {self.client_address[0]}")
        self.app.completed()
        self._send_body(json.dumps({"ok": True, "name": saved}), "application/json")


class App:
    """A running share: the server plus everything the request handlers need."""

    def __init__(self, share, port=0, bind="", host=None, token=None, once=False, out=None):
        from .net import lan_ips

        self.share = share
        self.once = once
        self.token = secrets.token_urlsafe(6) if token is None else token
        self.out = out
        self._lock = threading.Lock()
        self.httpd = Server((bind, port), Handler)
        self.httpd.app = self
        self.port = self.httpd.server_address[1]
        self.hosts = [host] if host else lan_ips()
        self.count = 0

    @property
    def url(self):
        return self.url_for(self.hosts[0])

    def url_for(self, host):
        path = f"/{self.token}/" if self.token else "/"
        return f"http://{host}:{self.port}{path}"

    def log(self, kind, text):
        if self.out is not None:
            with self._lock:
                self.out(kind, text)

    def message(self, client, text):
        self.log("msg", f"message from {client}:\n{text}")

    def completed(self):
        with self._lock:
            self.count += 1
        if self.once:
            threading.Thread(target=self.httpd.shutdown, daemon=True).start()

    def serve_forever(self):
        try:
            self.httpd.serve_forever(poll_interval=0.2)
        finally:
            self.httpd.server_close()

    def stop(self):
        self.httpd.shutdown()
