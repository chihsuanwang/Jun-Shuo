"""核心手冊 → 自包含 HTML 匯出(給網站上的「下載手冊」功能用)。

固定只匯出 4 份「給別人看的核心手冊」(使用手冊 / 功能手冊 / 營運流程SOP / 建置資料SOP),
不是整個 docs/ 資料夾都能下載——系統文件.md、開發路線.md、工作日誌.md 這些是開發/維護用,
不對外。

每次請求都重新讀 .md 檔現轉,不快取、不是包裝時就固定好的靜態檔:手冊內容改了,
網站上下載到的就是新的。圖片直接轉成 base64 內嵌進 HTML(data: URI),下載下來的單一
.html 檔案本身就看得到圖,不用額外帶著 screenshots 資料夾、不會有圖片路徑失效的問題。
"""
import base64
import datetime as dt
import os
import re

import markdown as md

DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")

# key -> (檔名, 標題, 一句話說明)
CORE_MANUALS = {
    "setup":   ("建置資料SOP.md", "建置資料 SOP", "資料庫全空時,第一次一步步建置客戶/商品/訂單/進貨/營運費用的教學,圖文並茂"),
    "usage":   ("使用手冊.md", "使用手冊", "畫面一個個怎麼操作,給平常使用系統的人看"),
    "feature": ("功能手冊.md", "功能手冊", "系統怎麼記帳、科目對照表全覽,給抓帳的同事看"),
    "sop":     ("營運流程SOP.md", "營運流程 SOP", "情境式流程指南:什麼時候該做什麼、照什麼順序"),
}

_IMG_SRC = re.compile(r'(<img\b[^>]*\bsrc=")([^"]+)(")')
_MIME_BY_EXT = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "svg": "image/svg+xml", "webp": "image/webp",
}

_PAGE_TMPL = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · 桂圓帳房</title>
<style>
body{{font-family:"Noto Sans TC","Microsoft JhengHei",sans-serif;max-width:860px;margin:0 auto;
     padding:32px 20px 60px;line-height:1.8;color:#2b2420;background:#FBF6EA}}
h1,h2,h3{{font-family:"Noto Serif TC",serif;color:#5a3a26}}
h1{{border-bottom:2px solid #A8321E;padding-bottom:8px}}
h2{{border-bottom:1px solid #d8c9ae;padding-bottom:5px;margin-top:40px}}
table{{border-collapse:collapse;width:100%;margin:16px 0;font-size:14px}}
th,td{{border:1px solid #d8c9ae;padding:6px 10px;text-align:left;vertical-align:top}}
th{{background:#f1e7d2}}
img{{max-width:100%;border:1px solid #d8c9ae;border-radius:4px;margin:10px 0;display:block}}
code{{background:#f1e7d2;padding:1px 5px;border-radius:3px;font-size:0.92em}}
pre{{background:#f1e7d2;padding:12px;border-radius:6px;overflow-x:auto}}
pre code{{background:none;padding:0}}
hr{{border:none;border-top:1px solid #d8c9ae;margin:28px 0}}
a{{color:#A8321E}}
blockquote{{margin:12px 0;padding:4px 16px;border-left:3px solid #c9a876;color:#6b5c4a;background:#f6efdc}}
.meta{{color:#8a7a68;font-size:12.5px;margin-bottom:28px}}
</style></head>
<body>
<div class="meta">桂圓帳房 · {title} · 下載於 {date}(內容依下載當下的最新版本產生)</div>
{body}
</body></html>"""


def _embed_images(html, base_dir):
    def repl(m):
        src = m.group(2)
        if src.startswith(("http://", "https://", "data:")):
            return m.group(0)
        path = os.path.normpath(os.path.join(base_dir, src))
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return m.group(0)
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
        b64 = base64.b64encode(data).decode("ascii")
        return f'{m.group(1)}data:{mime};base64,{b64}{m.group(3)}'
    return _IMG_SRC.sub(repl, html)


def export_html(key):
    """回傳 (title, html_bytes)。key 不在 CORE_MANUALS 時丟 KeyError。"""
    fname, title, _ = CORE_MANUALS[key]
    path = os.path.join(DOCS_DIR, fname)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    body = md.markdown(text, extensions=["tables", "fenced_code", "toc"])
    body = _embed_images(body, DOCS_DIR)
    page = _PAGE_TMPL.format(title=title, body=body, date=dt.date.today().isoformat())
    return title, page.encode("utf-8")
