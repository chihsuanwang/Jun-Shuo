"""路徑解析:區分「打包成 exe」/「開發中直接跑」/「雲端主機」。

- 資料庫、備份 → DATA_DIR(要可寫、要持久)
- 樣板 / 靜態檔 / schema.sql → RES_DIR(唯讀資源)

打包後(PyInstaller onedir):
    桂圓帳房/
      桂圓帳房.exe          ← DATA_DIR(資料庫、備份放這)
      _internal/...          ← RES_DIR(templates / static / schema.sql)

雲端主機:設環境變數 GY_DATA_DIR 指到程式碼目錄「以外」的持久位置
(例:/home/<user>/guiyuan-data),這樣 git pull 更新程式碼不會蓋到 / 誤刪資料。
"""
import os
import sys

FROZEN = getattr(sys, "frozen", False)
_HERE = os.path.dirname(os.path.abspath(__file__))

if FROZEN:
    DATA_DIR = os.path.dirname(sys.executable)
    RES_DIR = getattr(sys, "_MEIPASS", DATA_DIR)
else:
    DATA_DIR = _HERE
    RES_DIR = _HERE

# 環境變數覆寫「資料放哪」—— 雲端 / WSGI 主機用
DATA_DIR = os.environ.get("GY_DATA_DIR", DATA_DIR)
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    pass

DB_PATH     = os.path.join(DATA_DIR, "guiyuan_ledger.db")
BACKUP_DIR  = os.path.join(DATA_DIR, "備份")
SCHEMA_PATH = os.path.join(RES_DIR, "schema.sql")
TEMPLATES   = os.path.join(RES_DIR, "templates")
STATIC      = os.path.join(RES_DIR, "static")
