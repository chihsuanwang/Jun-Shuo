"""路徑解析:區分「打包成 exe」與「開發中直接跑」。

- 資料庫、備份 → 放 exe / 專案「旁邊」(要可寫、要持久、要能整包備份)
- 樣板 / 靜態檔 / schema.sql → 唯讀資源,打包時一起塞進 exe

打包後(PyInstaller onedir):
    桂圓帳房/
      桂圓帳房.exe          ← DATA_DIR(資料庫、備份放這)
      _internal/...          ← RES_DIR(templates / static / schema.sql)
      guiyuan_ledger.db      ← 第一次啟動自動建立
      備份/
"""
import os
import sys

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    DATA_DIR = os.path.dirname(sys.executable)
    RES_DIR = getattr(sys, "_MEIPASS", DATA_DIR)
else:
    DATA_DIR = os.path.dirname(os.path.abspath(__file__))
    RES_DIR = DATA_DIR

DB_PATH     = os.path.join(DATA_DIR, "guiyuan_ledger.db")
BACKUP_DIR  = os.path.join(DATA_DIR, "備份")
SCHEMA_PATH = os.path.join(RES_DIR, "schema.sql")
TEMPLATES   = os.path.join(RES_DIR, "templates")
STATIC      = os.path.join(RES_DIR, "static")
