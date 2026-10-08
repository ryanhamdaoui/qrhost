# QRHost

Share files from your terminal to any phone on the same network. Run `qrhost`, scan the code, done. No app to install on the phone, no account, no cloud.

```bash
pip install qrhost
qrhost vacation.mp4
```

![Example](https://github.com/user-attachments/assets/1290f47b-949b-45ed-9539-70b61637abae)

## What it does

- **Files and folders.** Share one file, several, or a whole folder. Phones get a clean file list with search, and can download any folder as a `.zip`, streamed as it's built so nothing is written to disk.
- **Phone to computer.** `qrhost -r` opens an upload page: pick photos or files on the phone and they land in a folder on your computer, with progress bars. You can also send text or a link, which prints in your terminal.
- **Video and audio stream.** Byte-range support means media plays in the phone's browser and you can seek through it.
- **Links, text and Wi-Fi.** `qrhost https://…`, `qrhost -t "text"` and `qrhost -w MyWifi -P password` just print a QR code. The Wi-Fi one joins the network when scanned.
- **Private by default.** Every share gets a random secret in its URL, so other people on the network can't stumble onto it. Only what you name is shared: sharing one file never exposes the rest of its folder, and hidden files (`.git`, `.env`, …) are left out unless you pass `--hidden`.
- **Works anywhere Python does.** Linux, macOS and Windows; one small dependency (`qrcode`). The QR code is drawn black-on-white, so it scans on light and dark terminals alike.

## Usage

```text
qrhost                      share the current folder
qrhost photo.jpg notes.pdf  share just these files
qrhost ~/Music              browse, stream and zip-download a folder
qrhost -r [DIR]             let phones send files into DIR (default: current folder)
qrhost https://example.com  turn a link into a QR code
qrhost -t "hello"           turn text into a QR code (-t - reads stdin)
qrhost -w MyWifi -P secret  QR code that joins a Wi-Fi network
```

Options:

| Option | |
| --- | --- |
| `-p, --port PORT` | Port to listen on. Defaults to 6969, or any free port if that's taken. |
| `--once` | Stop after the first completed download or upload. |
| `--ip ADDR` | Address to put in the QR code, if the auto-detected one is wrong (VPNs, several network cards). |
| `--bind ADDR` | Only listen on this address. |
| `--no-token` | Serve at `/` with no secret in the URL. |
| `--hidden` | Also share hidden files. |
| `-s, --save-qr FILE` | Also save the QR code as a `.png` or `.svg`. |
| `--invert` | Flip the QR for light terminals that don't support color. |

You can also run it as `python -m qrhost`.

## Troubleshooting

**The phone can't open the page.** Make sure both devices are on the same network, and that "client isolation" isn't turned on (common on guest and public Wi-Fi). On Windows and macOS, allow Python through the firewall when asked. If you have several network adapters, pick the right address with `--ip`.

**Don't share on networks you don't trust.** Traffic is plain HTTP on your local network. The secret URL keeps people from guessing their way in, but anyone who can see your traffic can see the files.

## Development

```bash
pip install -e . pytest
pytest
```

Creating a GitHub release runs the tests and publishes to PyPI.

## License

MIT
