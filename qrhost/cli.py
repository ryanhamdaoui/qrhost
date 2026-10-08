"""Command line interface."""

import argparse
import errno
import os
import re
import signal
import sys
import time

from . import __version__, qr
from .server import App, Share, human_size

DEFAULT_PORT = 6969
URI = re.compile(r"^(?:[a-z][a-z0-9+.-]*://\S+|(?:mailto|tel|sms|smsto|geo|bitcoin|otpauth):\S+)$", re.I)

EXAMPLES = """\
examples:
  qrhost                      share the current folder
  qrhost photo.jpg notes.pdf  share just these files
  qrhost ~/Music              browse, stream and zip-download a folder
  qrhost -r                   let phones send files to this computer
  qrhost https://example.com  turn a link into a QR code
  qrhost -t "hello"           turn text into a QR code
  qrhost -w MyWifi -P secret  QR code that joins a Wi-Fi network
"""


class Style:
    def __init__(self, stream):
        self.on = (
            not os.environ.get("NO_COLOR")
            and (bool(os.environ.get("FORCE_COLOR")) or (stream.isatty() and os.environ.get("TERM") != "dumb"))
        )

    def __call__(self, text, code):
        return f"\x1b[{code}m{text}\x1b[0m" if self.on else text

    def bold(self, t):
        return self(t, "1")

    def dim(self, t):
        return self(t, "2")

    def link(self, t):
        return self(t, "1;4;36")


def build_parser():
    p = argparse.ArgumentParser(
        prog="qrhost",
        description="Share files, folders, links, text and Wi-Fi over your local network with a QR code.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("targets", nargs="*", metavar="PATH|URL",
                   help="files or folders to share, or a link to encode (default: current folder)")
    mode = p.add_argument_group("other modes")
    mode.add_argument("-r", "--receive", nargs="?", const=".", metavar="DIR",
                      help="let phones upload files (and text) into DIR (default: current folder)")
    mode.add_argument("-t", "--text", metavar="TEXT", help="show TEXT as a QR code ('-' reads stdin)")
    mode.add_argument("-w", "--wifi", metavar="SSID", help="QR code that joins Wi-Fi network SSID")
    mode.add_argument("-P", "--password", help="Wi-Fi password (with --wifi)")
    mode.add_argument("--security", choices=["WPA", "WEP", "nopass"], help="Wi-Fi security (default: WPA, or nopass without a password)")
    mode.add_argument("--hidden-network", action="store_true", help="the Wi-Fi network does not broadcast its name")

    srv = p.add_argument_group("server")
    srv.add_argument("-p", "--port", type=int, help=f"port to listen on (default: {DEFAULT_PORT}, or any free port if busy)")
    srv.add_argument("--ip", metavar="ADDR", help="address to put in the QR code (default: auto-detected LAN address)")
    srv.add_argument("--bind", default="", metavar="ADDR", help="address to listen on (default: all interfaces)")
    srv.add_argument("--once", action="store_true", help="stop after the first completed download or upload")
    srv.add_argument("--no-token", action="store_true",
                     help="serve at / instead of a random secret path (anyone on the network can guess it)")
    srv.add_argument("--hidden", action="store_true", help="also share hidden files (dotfiles like .git and .env)")

    out = p.add_argument_group("output")
    out.add_argument("-s", "--save-qr", metavar="FILE", help="also save the QR code as a .png or .svg image")
    out.add_argument("--invert", action="store_true", help="invert the QR for light terminals without color")
    p.add_argument("-V", "--version", action="version", version=f"qrhost {__version__}")
    return p


def _prepare_console():
    if os.name == "nt":
        os.system("")  # turns on ANSI escape handling in the Windows console
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def show_qr(data, args):
    try:
        qr.print_qr(data, invert=args.invert)
    except Exception as exc:  # qrcode raises DataOverflowError for too much data
        sys.exit(f"qrhost: can't fit that in a QR code ({exc})")
    if args.save_qr:
        try:
            qr.save(data, args.save_qr)
        except (OSError, ValueError) as exc:
            sys.exit(f"qrhost: couldn't save QR image: {exc}")
        print(f"  QR code saved to {args.save_qr}")


def main(argv=None):
    _prepare_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    s = Style(sys.stdout)

    modes = sum(bool(x) for x in (args.targets, args.receive, args.text is not None, args.wifi))
    if modes > 1:
        parser.error("choose one thing to share: paths/URL, --receive, --text or --wifi")

    # ---- offline QR codes: no server needed ---------------------------------------------
    if args.wifi:
        print()
        show_qr(qr.wifi_payload(args.wifi, args.password, args.security, args.hidden_network), args)
        print(f"\n  Scan to join {s.bold(args.wifi)}" + (" (open network)" if not args.password else ""))
        return 0
    if args.text is not None:
        text = sys.stdin.read().rstrip("\n") if args.text == "-" else args.text
        if not text:
            parser.error("no text to encode")
        print()
        show_qr(text, args)
        print()
        return 0
    if len(args.targets) == 1 and URI.match(args.targets[0]) and not os.path.exists(args.targets[0]):
        print()
        show_qr(args.targets[0], args)
        print(f"\n  {s.link(args.targets[0])}\n")
        return 0

    # ---- serving -----------------------------------------------------------------------------
    if args.receive:
        target = os.path.abspath(os.path.expanduser(args.receive))
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as exc:
            sys.exit(f"qrhost: can't use {args.receive} for uploads: {exc}")
        if not os.access(target, os.W_OK):
            sys.exit(f"qrhost: can't write to {target}")
        share = Share(receive_dir=target)
    else:
        paths = args.targets or ["."]
        for path in paths:
            if not os.path.exists(path):
                hint = " (links need a scheme, like https://)" if "." in path and "/" not in path else ""
                sys.exit(f"qrhost: no such file or folder: {path}{hint}")
        share = Share(paths=[os.path.expanduser(p) for p in paths], show_hidden=args.hidden)

    port = DEFAULT_PORT if args.port is None else args.port
    try:
        try:
            app = App(share, port=port, bind=args.bind, host=args.ip,
                      token="" if args.no_token else None, once=args.once, out=None)
        except OSError as exc:
            in_use = exc.errno in (errno.EADDRINUSE, errno.EACCES, 10048, 10013)
            if args.port is not None or not in_use:
                raise
            app = App(share, port=0, bind=args.bind, host=args.ip,
                      token="" if args.no_token else None, once=args.once, out=None)
    except OSError as exc:
        sys.exit(f"qrhost: can't listen on port {port}: {exc.strerror or exc}")

    print()
    show_qr(app.url, args)
    print()
    print(f"  {s.bold('qrhost')} {s.dim(__version__)}  {describe(share)}")
    print(f"  Scan the code or open {s.link(app.url)}")
    for host in app.hosts[1:]:
        print(s.dim(f"  also at {app.url_for(host)}"))
    if app.hosts[0].startswith("127."):
        print(s("  No network address found: is this computer connected to Wi-Fi or Ethernet?", "33"))
    tips = []
    if args.once:
        tips.append("stops after one transfer")
    tips.append("Ctrl+C to stop")
    print(s.dim("  " + " · ".join(tips)))
    print()

    started = time.monotonic()
    app.out = Logger(s)
    signal.signal(signal.SIGTERM, _interrupt)
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        pass
    elapsed = time.monotonic() - started
    n = app.count
    print(f"\n  Stopped after {format_elapsed(elapsed)}, {n} transfer{'s' if n != 1 else ''} completed.")
    return 0


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def describe(share):
    if share.receiving:
        return f"receiving into {share.receive_dir}"
    if share.single_file:
        return f"sharing {share.title} ({human_size(os.path.getsize(share.single_file))})"
    if share.root_dir:
        return f"sharing folder {share.root_dir}"
    return f"sharing {len(share.entries)} items"


def format_elapsed(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {seconds // 60 % 60}m"


class Logger:
    SYMBOLS = {"down": ("⇩", "36"), "up": ("⇧", "35"), "done": ("✓", "32"), "warn": ("!", "33"), "msg": ("✉", "1;35")}

    def __init__(self, style):
        self.s = style

    def __call__(self, kind, text):
        symbol, color = self.SYMBOLS.get(kind, ("·", "0"))
        stamp = self.s.dim(time.strftime("%H:%M:%S"))
        first, _, rest = text.partition("\n")
        print(f"  {stamp}  {self.s(symbol, color)}  {first}", flush=True)
        if rest:
            for line in rest.splitlines():
                print(f"             {self.s('│', color)} {line}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
