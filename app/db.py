import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guiyuan_ledger.db")

def conn():
    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA foreign_keys = ON")
    return cx

def q(sql, args=()):
    cx = conn()
    try:
        return [dict(r) for r in cx.execute(sql, args).fetchall()]
    finally:
        cx.close()

def q1(sql, args=()):
    r = q(sql, args)
    return r[0] if r else {}

def execute(sql, args=()):
    cx = conn()
    try:
        cur = cx.execute(sql, args)
        cx.commit()
        return cur.lastrowid
    finally:
        cx.close()
