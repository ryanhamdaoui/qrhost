"""The pages phones see. Self-contained HTML: no external fonts, scripts or CDNs."""

import html
import re
import time
from urllib.parse import quote, urlsplit

from . import __version__

e = html.escape


def content_disposition(name, attachment):
    kind = "attachment" if attachment else "inline"
    fallback = re.sub(r'[\x00-\x1f\x7f"\\]', "_", name).encode("ascii", "replace").decode("ascii")
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def duration(seconds):
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{int(seconds // 60)}m {int(seconds % 60)}s"


def _date(timestamp):
    t = time.localtime(timestamp)
    day = time.strftime("%b ", t) + str(t.tm_mday)
    return day if t.tm_year == time.localtime().tm_year else f"{day}, {t.tm_year}"


CSS = """
:root{--bg:#fff;--fg:#1b1b1b;--muted:#6e6e6e;--line:#e4e4e4;--hover:#f4f4f4;--ok:#1a7f37;--bad:#c62828;
color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#151515;--fg:#e8e8e8;--muted:#9a9a9a;--line:#2c2c2c;
--hover:#1f1f1f;--ok:#5cc97b;--bad:#ef6b6b;color-scheme:dark}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:inherit;text-decoration:none}
main{max-width:720px;margin:0 auto;padding:20px 16px 48px}
svg{flex:none}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:16px}
h1{font-size:20px;font-weight:600;margin:0;word-break:break-word}
.sub{color:var(--muted);font-size:13px}
.crumbs a{color:var(--muted)}.crumbs a:hover{color:var(--fg);text-decoration:underline}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;height:36px;padding:0 14px;
border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit;font-weight:500;
cursor:pointer;white-space:nowrap}
.btn:hover{background:var(--hover)}
.btn.solid{background:var(--fg);border-color:var(--fg);color:var(--bg)}
.btn.solid:hover{opacity:.88}
.bar{display:flex;gap:8px;margin-bottom:8px}
input[type=search],textarea{width:100%;min-width:0;border:1px solid var(--line);border-radius:6px;background:var(--bg);
color:var(--fg);font:inherit;padding:0 10px;outline:none}
input[type=search]{flex:1;height:36px}
input:focus,textarea:focus{border-color:var(--muted)}
.list{list-style:none;margin:0;padding:0;border-top:1px solid var(--line)}
.list li{display:flex;align-items:center;border-bottom:1px solid var(--line)}
.list li[hidden]{display:none}
.list li:hover{background:var(--hover)}
.item{flex:1;min-width:0;display:flex;align-items:center;gap:10px;padding:10px 4px 10px 6px}
.item svg{color:var(--muted)}
.name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.size,.date{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap;text-align:right}
.size{width:72px}.date{width:96px}
@media (max-width:520px){.date{display:none}}
.dl{display:grid;place-items:center;width:40px;height:40px;color:var(--muted);border-radius:6px}
.dl:hover{color:var(--fg)}
.empty{color:var(--muted);padding:24px 0}
.preview{display:block;max-width:100%;max-height:65vh;margin:0 0 16px;border:1px solid var(--line);border-radius:6px}
.actions{display:flex;gap:8px;flex-wrap:wrap}
.drop{display:block;border:1px dashed var(--muted);border-radius:6px;padding:36px 16px;text-align:center;cursor:pointer}
.drop.over{background:var(--hover);border-style:solid}
.drop p{margin:10px 0 0}
section{margin-top:28px}
h2{font-size:15px;font-weight:600;margin:0 0 8px}
textarea{display:block;min-height:88px;padding:8px 10px;margin-bottom:8px;resize:vertical}
.progress{height:2px;background:var(--line);margin-top:6px}
.progress i{display:block;height:100%;width:0;background:var(--fg)}
.upload .item{flex-direction:column;align-items:stretch;gap:0}
.upload .row{display:flex;gap:12px}
.ok{color:var(--ok)}.bad{color:var(--bad)}
dialog{border:1px solid var(--line);border-radius:8px;padding:20px;background:var(--bg);color:var(--fg);
width:min(92vw,340px)}
dialog::backdrop{background:rgba(0,0,0,.45)}
dialog img{display:block;width:100%;height:auto;image-rendering:pixelated}
dialog code{display:block;margin:12px 0;font-size:12px;word-break:break-all;color:var(--muted)}
footer{margin-top:32px;color:var(--muted);font-size:12px}
"""

# Shared by every page: the QR dialog.
BASE_JS = """
const dlg=document.getElementById('qr');
document.getElementById('qrbtn').onclick=()=>dlg.showModal();
dlg.addEventListener('click',ev=>{if(ev.target===dlg)dlg.close()});
document.getElementById('copy').onclick=async ev=>{
  try{await navigator.clipboard.writeText(dlg.dataset.url);ev.target.textContent='Copied'}
  catch(_){ev.target.textContent='Copy failed'}
};
"""

FILTER_JS = """
const q=document.getElementById('q'),list=document.getElementById('list'),none=document.getElementById('none');
const rows=[...list.children];
q.addEventListener('input',()=>{
  const term=q.value.trim().toLowerCase();let shown=0;
  for(const r of rows){r.hidden=!r.dataset.name.includes(term);shown+=!r.hidden}
  none.hidden=shown>0;
});
"""

RECEIVE_JS = """
const api=document.querySelector('main').dataset.api;
const pick=document.getElementById('pick'),drop=document.getElementById('drop'),queue=document.getElementById('queue');
const size=b=>b<1024?b+' B':b<1048576?(b/1024).toFixed(1)+' KB':b<1073741824?(b/1048576).toFixed(1)+' MB':(b/1073741824).toFixed(2)+' GB';
let chain=Promise.resolve(),pending=0;

function enqueue(files){
  for(const file of files){
    const li=document.createElement('li');
    li.innerHTML='<div class="item"><div class="row"><span class="name"></span><span class="sub"></span></div>'+
      '<div class="progress"><i></i></div></div>';
    li.querySelector('.name').textContent=file.name;
    li.querySelector('.sub').textContent='Waiting';
    queue.prepend(li);queue.hidden=false;pending++;
    chain=chain.then(()=>upload(file,li)).then(()=>pending--);
  }
}

function upload(file,li){
  const status=li.querySelector('.sub'),fill=li.querySelector('.progress i');
  return new Promise(done=>{
    const xhr=new XMLHttpRequest(),start=Date.now();
    xhr.open('POST',api+'/upload?name='+encodeURIComponent(file.name));
    xhr.upload.onprogress=ev=>{
      if(!ev.lengthComputable)return;
      const rate=ev.loaded/Math.max(.001,(Date.now()-start)/1000);
      fill.style.width=(100*ev.loaded/ev.total)+'%';
      status.textContent=size(ev.loaded)+' / '+size(ev.total)+' \\u00b7 '+size(rate)+'/s';
    };
    xhr.onload=()=>{
      if(xhr.status===200){
        const saved=JSON.parse(xhr.responseText).name;
        fill.parentNode.hidden=true;status.className='sub ok';
        status.textContent='Sent \\u00b7 '+size(file.size)+(saved!==file.name?' as '+saved:'');
      }else{status.className='sub bad';status.textContent='Failed ('+xhr.status+')'}
      done();
    };
    xhr.onerror=()=>{status.className='sub bad';status.textContent='Connection lost';done()};
    xhr.send(file);
  });
}

pick.addEventListener('change',()=>{enqueue(pick.files);pick.value=''});
for(const t of ['dragenter','dragover'])drop.addEventListener(t,ev=>{ev.preventDefault();drop.classList.add('over')});
for(const t of ['dragleave','drop'])drop.addEventListener(t,ev=>{ev.preventDefault();drop.classList.remove('over')});
drop.addEventListener('drop',ev=>enqueue(ev.dataTransfer.files));
addEventListener('beforeunload',ev=>{if(pending){ev.preventDefault();ev.returnValue=''}});

const msg=document.getElementById('msg'),sent=document.getElementById('sent');
document.getElementById('send').onclick=async()=>{
  if(!msg.value.trim())return;
  try{
    const r=await fetch(api+'/text',{method:'POST',headers:{'Content-Type':'text/plain;charset=utf-8'},body:msg.value});
    if(!r.ok)throw new Error();
    msg.value='';sent.className='sub ok';sent.textContent='Sent';
  }catch(_){sent.className='sub bad';sent.textContent='Could not send'}
};
"""


def _icon(path_d, size=18):
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'<path d="{path_d}"/></svg>')


FOLDER = _icon("M3 6.5A1.5 1.5 0 0 1 4.5 5h4l2 2h9A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z")
FILE = _icon("M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8zM14 3v5h5")
DOWNLOAD = _icon("M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 20h14")
QR_ICON = _icon("M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h2v2h-2zM18 18h2v2h-2zM14 18h2v2h-2zM18 14h2v2h-2z", 16)


def _page(title, body, share_url, script=""):
    qr_src = urlsplit(share_url).path + ".qrhost/qr.svg"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{e(title)}</title>
<link rel="icon" href="data:,">
<style>{CSS}</style></head>
<body>
{body}
<dialog id="qr" data-url="{e(share_url)}">
<img src="{e(qr_src)}" alt="QR code for this page" width="300" height="300">
<code>{e(share_url)}</code>
<div class="actions"><button class="btn solid" id="copy">Copy link</button>
<form method="dialog"><button class="btn">Close</button></form></div>
</dialog>
<script>{BASE_JS}{script}</script>
</body></html>"""


def _top(heading, subline, above=""):
    return f"""<div class="top"><div>{above}<h1>{heading}</h1><div class="sub">{subline}</div></div>
<button class="btn" id="qrbtn" title="Show a QR code so another device can open this">{QR_ICON}QR</button></div>"""


def _footer():
    return f'<footer>qrhost {__version__}</footer>'


def listing_page(title, crumbs, items, zip_url, share_url, total_size):
    *parents, (current, _) = crumbs
    trail = "".join(f'<a href="{e(url)}">{e(name)}</a> / ' for name, url in parents)
    crumbs_html = f'<div class="sub crumbs">{trail}</div>' if parents else ""
    count = len(items)
    subline = f"{count} item{'' if count == 1 else 's'}" + (f" · {e(total_size)}" if total_size else "")

    rows = []
    for item in items:
        name, url = item["name"], item["url"]
        if item["is_dir"]:
            action_url, label = url + "?zip", f"Download {name} as .zip"
        else:
            action_url, label = url + "?dl", f"Download {name}"
        rows.append(
            f'<li data-name="{e(name.casefold())}"><a class="item" href="{e(url)}">'
            f'{FOLDER if item["is_dir"] else FILE}<span class="name">{e(name)}</span>'
            f'<span class="size">{e(item["size_text"])}</span><span class="date">{_date(item["mtime"])}</span></a>'
            f'<a class="dl" href="{e(action_url)}" download title="{e(label)}" aria-label="{e(label)}">{DOWNLOAD}</a></li>'
        )

    if items:
        content = f"""<div class="bar"><input id="q" type="search" placeholder="Filter" aria-label="Filter files" autocomplete="off">
<a class="btn" href="{e(zip_url)}" download>{DOWNLOAD}Download all</a></div>
<ul class="list" id="list">{''.join(rows)}</ul>
<p class="empty" id="none" hidden>No matches.</p>"""
    else:
        content = '<p class="empty">This folder is empty.</p>'
    body = f"<main>{_top(e(current), subline, crumbs_html)}{content}{_footer()}</main>"
    return _page(current, body, share_url, FILTER_JS if items else "")


def file_page(name, size, url, mime, share_url):
    if mime.startswith("image/"):
        preview = f'<img class="preview" src="{e(url)}" alt="">'
    elif mime.startswith("video/"):
        preview = f'<video class="preview" src="{e(url)}" controls playsinline preload="metadata"></video>'
    elif mime.startswith("audio/"):
        preview = f'<audio src="{e(url)}" controls style="width:100%;margin-bottom:16px"></audio>'
    else:
        preview = ""
    kind = mime.split(";")[0] if mime else "file"
    body = f"""<main>{_top(e(name), f"{e(size)} · {e(kind)}")}{preview}
<div class="actions"><a class="btn solid" href="{e(url)}?dl" download>{DOWNLOAD}Download</a>
<a class="btn" href="{e(url)}" target="_blank" rel="noopener">Open</a></div>{_footer()}</main>"""
    return _page(name, body, share_url)


def receive_page(folder, api, share_url):
    body = f"""<main data-api="{e(api)}">{_top("Send files", f"They will be saved to “{e(folder)}” on the computer.")}
<label class="drop" id="drop"><input type="file" id="pick" multiple hidden>
<span class="btn solid">Choose files</span><p class="sub">or drag them here</p></label>
<ul class="list upload" id="queue" hidden style="margin-top:16px"></ul>
<section><h2>Send text</h2>
<textarea id="msg" placeholder="A link, a note… it shows up in the computer's terminal" aria-label="Text to send"></textarea>
<div class="actions" style="align-items:center"><button class="btn" id="send">Send</button><span id="sent" class="sub"></span></div>
</section>{_footer()}</main>"""
    return _page("Send files", body, share_url, RECEIVE_JS)


def error_page(title, message):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark">
<title>{e(title)}</title><link rel="icon" href="data:,"><style>{CSS}</style></head>
<body><main><h1>{e(title)}</h1><p class="sub">{e(message)}</p></main></body></html>"""
