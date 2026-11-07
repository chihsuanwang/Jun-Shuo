import sqlite3, os, contextvars, contextlib
from paths import DB_PATH as DB

# 交易模式(2026-11-07,訂單匯入用):with transaction(): 裡面所有 q / execute 共用同一條連線、不逐筆 commit,
# 全部成功才一起寫入,中途出錯整批還原。ledger / queries 都是 from db import q, execute,自動跟著走。
_tx = contextvars.ContextVar("gy_tx", default=None)


def conn():
    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA foreign_keys = ON")
    return cx


@contextlib.contextmanager
def transaction():
    """BEGIN IMMEDIATE:一開始就拿寫入鎖,同時送出的第二個請求要等這一批做完才開始
    (兩個分頁同時按確認匯入,第二個重新檢查時就會看到第一個已經寫進去的)。巢狀呼叫直接沿用外層。"""
    if _tx.get() is not None:
        yield _tx.get()
        return
    cx = sqlite3.connect(DB, isolation_level=None, timeout=30)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA foreign_keys = ON")
    cx.execute("BEGIN IMMEDIATE")
    tok = _tx.set(cx)
    try:
        yield cx
        cx.execute("COMMIT")
    except BaseException:
        cx.execute("ROLLBACK")
        raise
    finally:
        _tx.reset(tok)
        cx.close()


def q(sql, args=()):
    tx = _tx.get()
    if tx is not None:
        return [dict(r) for r in tx.execute(sql, args).fetchall()]
    cx = conn()
    try:
        return [dict(r) for r in cx.execute(sql, args).fetchall()]
    finally:
        cx.close()

def q1(sql, args=()):
    r = q(sql, args)
    return r[0] if r else {}

def execute(sql, args=()):
    tx = _tx.get()
    if tx is not None:
        return tx.execute(sql, args).lastrowid
    cx = conn()
    try:
        cur = cx.execute(sql, args)
        cx.commit()
        return cur.lastrowid
    finally:
        cx.close()
