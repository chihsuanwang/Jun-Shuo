# 上雲:PythonAnywhere(免費版)

> 目的:一個**固定的網址**,同仁隨時連得進來試用,不用開你的電腦。
> **免費版限制**:每天 100 CPU 秒(超過當天變慢、隔天 UTC 午夜重置)、512MB 硬碟、
> 網址固定 `你的帳號.pythonanywhere.com`、每 3 個月要登入按一次「續命」、沒有自動備份。
> **假資料試用足夠**;要長期穩定 / 多人常用再升 Developer($10/月)或改 Fly.io。
>
> 程式碼這邊已備好:`wsgi.py`(把 FastAPI 包成 WSGI)、`a2wsgi`(requirements 內)、
> `GY_DATA_DIR`(資料放程式碼目錄外)、`GY_PASSWORD`(整站共用密碼)、首次自動建示範庫。

以下 `你的帳號` 全部換成實際帳號。

---

## 1. 註冊

<https://www.pythonanywhere.com/registration/register/beginner/> —— 選 **Beginner(免費)**。

## 2. 抓程式碼(Bash console)

右上 **Consoles → Bash**,執行:

```bash
git clone https://github.com/chihsuanwang/Jun-Shuo.git
```

## 3. 建虛擬環境 + 裝套件

```bash
mkvirtualenv guiyuan --python=/usr/bin/python3.11
pip install -r ~/Jun-Shuo/app/requirements.txt
```

（`mkvirtualenv` 不能用的話:`python3.11 -m venv ~/.virtualenvs/guiyuan && ~/.virtualenvs/guiyuan/bin/pip install -r ~/Jun-Shuo/app/requirements.txt`）
（pip 找不到 `a2wsgi==1.10.8` → 改 `pip install a2wsgi`）

## 4. 先建好資料庫(同一個 Bash console)

```bash
export GY_DATA_DIR=/home/你的帳號/guiyuan-data
cd ~/Jun-Shuo/app
python seed.py --force
```

資料庫會建在 `~/guiyuan-data/`(**程式碼目錄外面**,之後 `git pull` 不會動到它)。

## 5. 建立 Web app

**Web** 分頁 → **Add a new web app** → 網域用預設的 `你的帳號.pythonanywhere.com` →
框架選 **Manual configuration** → Python 版本選 **3.11**。

建好後在 Web 分頁填:

| 欄位 | 值 |
|---|---|
| **Source code** | `/home/你的帳號/Jun-Shuo/app` |
| **Working directory** | `/home/你的帳號/Jun-Shuo/app` |
| **Virtualenv** | `/home/你的帳號/.virtualenvs/guiyuan` |
| **Force HTTPS** | 開 |

## 6. 改 WSGI 設定檔

Web 分頁點 **WSGI configuration file** 那個連結(`/var/www/你的帳號_pythonanywhere_com_wsgi.py`),
把內容**整個換成**:

```python
import os, sys

path = "/home/你的帳號/Jun-Shuo/app"
if path not in sys.path:
    sys.path.insert(0, path)

os.environ["GY_DATA_DIR"] = "/home/你的帳號/guiyuan-data"
os.environ["GY_PASSWORD"] = "改成你要的密碼"     # 給同仁的密碼,帳號欄隨便打

from wsgi import application
```

存檔。

## 7.(選)靜態檔交給 nginx

Web 分頁 **Static files** 加一列 —— URL `/static/`,Directory `/home/你的帳號/Jun-Shuo/app/static`。
(不加也能跑,app 自己會服務 `/static`,只是多耗一點 CPU 秒)

## 8. Reload

Web 分頁最上面綠色 **Reload** 按鈕。

開 `https://你的帳號.pythonanywhere.com` → 跳出要帳密 → 帳號隨便打、密碼填第 6 步設的那組 → 進得去就成功。
把網址 + 密碼傳給同仁。

---

## 日常維護

**更新程式碼**（在 Bash console)：
```bash
cd ~/Jun-Shuo && git pull
# 若 requirements.txt 有變:
pip install -r app/requirements.txt
```
然後 Web 分頁按 **Reload**。

**重置示範資料**：
```bash
rm ~/guiyuan-data/guiyuan_ledger.db
```
再按 Reload(會自動重建);或重跑第 4 步的 `seed.py --force`。

**手動備份**（免費版沒有排程)：
```bash
cp ~/guiyuan-data/guiyuan_ledger.db ~/backup_$(date +%Y%m%d).db
```
到 **Files** 分頁把它下載下來。

**每 3 個月**：Web 分頁會出現「Run until 3 months from now」按鈕,登入按一下,不然網站會下線。

---

## 遇到問題

| 狀況 | 看這裡 |
|---|---|
| Reload 後開網站是錯誤頁 | Web 分頁的 **Error log**(最常見:路徑打錯、virtualenv 路徑錯、忘了 `pip install`) |
| 下午開始變超慢 | 當天 100 CPU 秒用完被 tarpit,UTC 午夜(台灣早上 8 點)重置。叫同仁別開自動重整的分頁 |
| `a2wsgi` 裝不起來 | `pip install a2wsgi`(不指定版本) |
| 要看伺服器在跑什麼 | Web 分頁的 **Server log** / **Error log** |
