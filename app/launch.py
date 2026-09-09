"""桂圓帳房 · 啟動點(打包成 exe 用)。

雙擊 exe → 這支程式:
  1. 第一次啟動:自動建立示範資料庫
  2. 檢查是否已有一份在跑(埠被佔用)→ 直接開瀏覽器
  3. 啟動本機伺服器、開瀏覽器
  4. 關掉這個視窗就會關閉程式
"""
import os
import runpy
import socket
import sys
import threading
import time
import webbrowser

import paths

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"


def _port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
            return False
        except OSError:
            return True


def _ensure_db():
    if os.path.exists(paths.DB_PATH):
        return
    print("第一次啟動,正在建立示範資料庫...(約 3~5 秒)")
    old_argv = sys.argv
    sys.argv = ["seed", "--force"]
    try:
        runpy.run_module("seed", run_name="__main__")
    finally:
        sys.argv = old_argv
    print("示範資料庫建立完成。\n")


def _banner():
    line = "=" * 46
    print(line)
    print("  桂圓帳房 已啟動")
    print(f"  請用瀏覽器開:{URL}")
    print("  用完後,關掉這個視窗就會關閉程式。")
    print(f"  資料庫位置:{paths.DB_PATH}")
    print(line)


def main():
    os.makedirs(paths.BACKUP_DIR, exist_ok=True)

    if _port_in_use(PORT):
        print("偵測到桂圓帳房可能已經在執行,直接開啟瀏覽器。")
        webbrowser.open(URL)
        time.sleep(2)
        return

    _ensure_db()

    from main import app  # 匯入時會自動備份資料庫
    import uvicorn

    _banner()
    threading.Timer(1.5, lambda: webbrowser.open(URL)).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\n[錯誤]", e)
        input("按 Enter 關閉...")
