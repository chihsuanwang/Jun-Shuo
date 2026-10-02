"""WSGI 進入點 —— 給只吃 WSGI 的雲端主機用(PythonAnywhere 等)。

本機開發不用這支(那邊走 uvicorn)。它把 ASGI 的 FastAPI app 包成 WSGI:

    from wsgi import application

主機的環境變數:
    GY_DATA_DIR  資料庫 / 備份放哪(程式碼目錄以外的持久位置)
    GY_PASSWORD  設了 → 整站要這組共用密碼才進得去(帳號隨便打)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from a2wsgi import ASGIMiddleware
from main import app

application = ASGIMiddleware(app)
