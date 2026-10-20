"""桂圓帳房 · 本機銷售平台(FastAPI 骨架)
啟動:  py -m uvicorn main:app --port 8000    或雙擊 啟動.bat
"""
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os, io, csv, json, glob, shutil, sys, runpy, base64, datetime as dt

import paths
import queries as Q
import db as _db
import ledger
import chart
import order_import as OI
import manual_export as ME
from db import q, q1, execute

HERE = paths.RES_DIR
app = FastAPI(title="桂圓帳房")
app.mount("/static", StaticFiles(directory=paths.STATIC), name="static")
tpl = Jinja2Templates(directory=paths.TEMPLATES)

# ---------- 選用:整站共用密碼(設 GY_PASSWORD 環境變數才啟用)----------
# 本機 / exe 不設 → 無登入,行為不變。雲端主機設一組 → 全站 Basic Auth(帳號隨便打)。
_GY_PASSWORD = os.environ.get("GY_PASSWORD")
if _GY_PASSWORD:
    @app.middleware("http")
    async def _shared_password(request: Request, call_next):
        if request.url.path.startswith("/static"):
            return await call_next(request)
        hdr = request.headers.get("authorization", "")
        ok = False
        if hdr.startswith("Basic "):
            try:
                ok = base64.b64decode(hdr[6:]).decode().split(":", 1)[1] == _GY_PASSWORD
            except Exception:
                ok = False
        if not ok:
            return Response("需要密碼", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Guiyuan"'})
        return await call_next(request)

def money(v):
    try:
        if -0.5 < v < 0.5:   # 避免浮點數誤差(例如折舊除不盡)顯示成難看的 -0
            v = 0
        return f"{v:,.0f}"
    except Exception: return v
tpl.env.filters["money"] = money
tpl.env.filters["pct"] = lambda v: f"{v*100:.0f}%"


# ---------- 啟動時:資料庫不存在就用示範資料建一個 --------------
#   雲端首次部署 / 新機器忘了先 seed 時的保險(啟動.bat / launch.py 也各有一層)
def ensure_db():
    try:
        if os.path.exists(_db.DB):
            return
        print("[init] 找不到資料庫,建立示範資料 ...")
        old = sys.argv
        sys.argv = ["seed", "--force"]
        try:
            runpy.run_module("seed", run_name="__main__")
        finally:
            sys.argv = old
    except Exception as e:
        print("[init] 建立示範資料失敗:", e)

ensure_db()


# ---------- 啟動時自動備份資料庫(保留最近 30 份) --------------
def backup_db():
    try:
        src = _db.DB
        if not os.path.exists(src):
            return
        bdir = paths.BACKUP_DIR
        os.makedirs(bdir, exist_ok=True)
        stamp = dt.date.today().isoformat().replace("-", "")
        dest = os.path.join(bdir, f"guiyuan_ledger_{stamp}.db")
        if not os.path.exists(dest):
            shutil.copy2(src, dest)
        files = sorted(glob.glob(os.path.join(bdir, "guiyuan_ledger_*.db")))
        for f in files[:-30]:
            os.remove(f)
    except Exception as e:
        print("[backup] 略過:", e)

backup_db()


# ---------- 啟動時輕量遷移(補既有資料庫缺的欄位) -----------------
def migrate_db():
    try:
        if not os.path.exists(_db.DB):
            return
        cols = [r["name"] for r in q("PRAGMA table_info(op_expense)")]
        if "updated_at" not in cols:
            execute("ALTER TABLE op_expense ADD COLUMN updated_at TEXT")
            execute("UPDATE op_expense SET updated_at=datetime('now','localtime') WHERE updated_at IS NULL")
            print("[migrate] op_expense.updated_at 已補上")
        ocols = [r["name"] for r in q('PRAGMA table_info("order")')]
        if "invoiced" not in ocols:
            execute('ALTER TABLE "order" ADD COLUMN invoiced INTEGER NOT NULL DEFAULT 0')
            print("[migrate] order.invoiced 已補上")
        if "ship_payer" not in ocols:
            execute('ALTER TABLE "order" ADD COLUMN ship_payer TEXT NOT NULL DEFAULT \'店家吸收\'')
            print("[migrate] order.ship_payer 已補上")
        pcols = [r["name"] for r in q("PRAGMA table_info(product)")]
        if "unit_cost" not in pcols:
            execute("ALTER TABLE product ADD COLUMN unit_cost REAL NOT NULL DEFAULT 0")
            # 用舊批次資料的平均單位成本幫忙帶一個初值,之後可在商品目錄手動調整
            for r in q("""SELECT ol.product_id pid, AVG(b.unit_cost) v
                          FROM order_line ol JOIN batch b ON b.batch_id=ol.batch_id
                          WHERE b.unit_cost IS NOT NULL AND b.unit_cost > 0
                          GROUP BY ol.product_id"""):
                execute("UPDATE product SET unit_cost=? WHERE product_id=?", (r["v"], r["pid"]))
            print("[migrate] product.unit_cost 已補上(用舊批次成本帶初值)")
        execute("CREATE TABLE IF NOT EXISTS pricing_param (key TEXT PRIMARY KEY, value REAL NOT NULL)")
        lcols = [r["name"] for r in q("PRAGMA table_info(order_line)")]
        if "line_cost" not in lcols:
            execute("ALTER TABLE order_line ADD COLUMN line_cost REAL NOT NULL DEFAULT 0")
            execute("""UPDATE order_line SET line_cost = qty * COALESCE(unit_cost,
                       (SELECT unit_cost FROM product p WHERE p.product_id=order_line.product_id), 0)""")
            print("[migrate] order_line.line_cost 已補上(用舊的單位成本 × 數量)")
        if "unit_cost" not in lcols:
            execute("ALTER TABLE order_line ADD COLUMN unit_cost REAL")
            # 舊訂單先用商品目錄現在的成本補上,之後就固定不變
            execute("""UPDATE order_line SET unit_cost=(SELECT unit_cost FROM product p
                       WHERE p.product_id=order_line.product_id)""")
            print("[migrate] order_line.unit_cost 已補上(用商品目錄目前的成本)")
        icols = [r["name"] for r in q("PRAGMA table_info(other_income)")]
        for col in ("account_code", "doc_type", "invoice_no"):
            if col not in icols:
                execute(f"ALTER TABLE other_income ADD COLUMN {col} TEXT")
        acols = [r["name"] for r in q("PRAGMA table_info(fixed_asset)")]
        if "life_months" not in acols:
            execute("ALTER TABLE fixed_asset ADD COLUMN life_months INTEGER")
        if "tax_amount" not in acols:
            execute("ALTER TABLE fixed_asset ADD COLUMN tax_amount REAL NOT NULL DEFAULT 0")
        scols = [r["name"] for r in q("PRAGMA table_info(sales_return)")]
        for col in ("payment_account", "cost_account"):
            if col not in scols:
                execute(f"ALTER TABLE sales_return ADD COLUMN {col} TEXT")
        ecols = [r["name"] for r in q("PRAGMA table_info(op_expense)")]
        if "account_code" not in ecols:
            execute("ALTER TABLE op_expense ADD COLUMN account_code TEXT")
        if "expense_date" not in ecols:
            execute("ALTER TABLE op_expense ADD COLUMN expense_date TEXT")
        ccols = [r["name"] for r in q("PRAGMA table_info(customer)")]
        if "cust_no" not in ccols:
            execute("ALTER TABLE customer ADD COLUMN cust_no TEXT")
            for i, r in enumerate(q("SELECT customer_id FROM customer ORDER BY customer_id"), start=1):
                execute("UPDATE customer SET cust_no=? WHERE customer_id=?",
                        (f"C{dt.date.today().year}{i:03d}", r["customer_id"]))
            print("[migrate] customer.cust_no 已補上")
        if "cost_payment_account" not in ocols:
            execute('ALTER TABLE "order" ADD COLUMN cost_payment_account TEXT')
        if "sales_tax" not in ocols:
            execute('ALTER TABLE "order" ADD COLUMN sales_tax REAL NOT NULL DEFAULT 0')
            print("[migrate] order.sales_tax 已補上")
        # 2026-10-08 改成貿易公司模式(沒有存貨、退貨/折讓改成退款/退回小農):總帳的
        # source_type CHECK 跟 sales_return 的 kind CHECK 都換了,SQLite 沒辦法直接改 CHECK,
        # 舊資料庫要整個重建(seed.py --force 或 --empty),這裡只提醒、不自動動資料。
        # 2026-10-16:會計科目表(取代 bank_account)。舊資料庫補表、灌初值,原本的銀行帳戶併進 C 組。
        execute("""CREATE TABLE IF NOT EXISTS account (code TEXT PRIMARY KEY, name TEXT NOT NULL,
                   grp TEXT NOT NULL, sort INTEGER NOT NULL DEFAULT 0,
                   active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
                   created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))""")
        chart.ensure_chart()
        if q1("SELECT 1 FROM sqlite_master WHERE name='bank_account'"):
            for b in q("SELECT name, account_code FROM bank_account"):
                if not q1("SELECT 1 FROM account WHERE code=?", (b["account_code"],)):
                    execute("INSERT INTO account(code,name,grp,sort) VALUES(?,?,'C',999)",
                            (b["account_code"], b["name"]))
        # 2026-10-17:資本異動改「增資 / 減資 / 提列法定盈餘公積」,帳務調整改多行;
        # SQLite 不能改 CHECK / NOT NULL,舊表改名、建新表、資料照搬。
        esql = q1("SELECT sql FROM sqlite_master WHERE name='equity_txn'").get("sql") or ""
        if "現金增資" in esql:
            execute("ALTER TABLE equity_txn RENAME TO equity_txn_old")
            execute("""CREATE TABLE equity_txn (txn_id INTEGER PRIMARY KEY, txn_date TEXT NOT NULL,
                       txn_type TEXT NOT NULL CHECK (txn_type IN ('增資','減資','提列法定盈餘公積')),
                       amount REAL NOT NULL DEFAULT 0, payment_account TEXT, equity_account TEXT,
                       source_account TEXT, note TEXT,
                       created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))""")
            execute("""INSERT INTO equity_txn(txn_id,txn_date,txn_type,amount,payment_account,equity_account,
                                              source_account,note,created_at)
                       SELECT txn_id, txn_date,
                              CASE txn_type WHEN '現金增資' THEN '增資' ELSE '提列法定盈餘公積' END, amount,
                              CASE txn_type WHEN '現金增資' THEN payment_account END,
                              CASE txn_type WHEN '現金增資' THEN '3110' ELSE '3310-03' END,
                              CASE txn_type WHEN '現金增資' THEN NULL ELSE '3351-03' END, note, created_at
                         FROM equity_txn_old""")
            execute("DROP TABLE equity_txn_old")
            print("[migrate] equity_txn 已換新結構")
        mcols = [r["name"] for r in q("PRAGMA table_info(manual_entry)")]
        if "debit_code" in mcols:
            execute("""CREATE TABLE IF NOT EXISTS manual_entry_line (line_id INTEGER PRIMARY KEY,
                       entry_id INTEGER NOT NULL, line_no INTEGER NOT NULL, account_code TEXT NOT NULL,
                       account_name TEXT NOT NULL, debit REAL NOT NULL DEFAULT 0, credit REAL NOT NULL DEFAULT 0)""")
            execute("""INSERT INTO manual_entry_line(entry_id,line_no,account_code,account_name,debit,credit)
                       SELECT entry_id, 1, debit_code, debit_name, amount, 0 FROM manual_entry""")
            execute("""INSERT INTO manual_entry_line(entry_id,line_no,account_code,account_name,debit,credit)
                       SELECT entry_id, 2, credit_code, credit_name, 0, amount FROM manual_entry""")
            execute("ALTER TABLE manual_entry RENAME TO manual_entry_old")
            execute("""CREATE TABLE manual_entry (entry_id INTEGER PRIMARY KEY, entry_date TEXT NOT NULL,
                       amount REAL NOT NULL DEFAULT 0, note TEXT,
                       created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))""")
            execute("""INSERT INTO manual_entry(entry_id,entry_date,amount,note,created_at)
                       SELECT entry_id, entry_date, amount, note, created_at FROM manual_entry_old""")
            execute("DROP TABLE manual_entry_old")
            print("[migrate] manual_entry 已換成多行")
        # 2026-10-20:營業稅結算 / 繳納兩種傳票來源。SQLite 不能改 CHECK,總帳整張表在同一個交易裡
        # 重建、資料照搬(不用清空網站資料庫)。
        lsql = q1("SELECT sql FROM sqlite_master WHERE name='ledger_entry'").get("sql") or ""
        if lsql and "goods_purchase" in lsql and "vat_settlement" not in lsql:
            import sqlite3
            cx = sqlite3.connect(_db.DB)
            try:
                cx.executescript("""BEGIN;
                  CREATE TABLE ledger_entry_new (
                    entry_id INTEGER PRIMARY KEY, voucher_no TEXT NOT NULL, entry_date TEXT NOT NULL,
                    account_code TEXT NOT NULL, account_name TEXT NOT NULL,
                    debit REAL NOT NULL DEFAULT 0, credit REAL NOT NULL DEFAULT 0,
                    source_type TEXT NOT NULL CHECK (source_type IN ('op_expense','order_sale','order_payment',
                      'goods_purchase','other_income','equity','manual_adjustment',
                      'asset_acquire','asset_depreciation','sales_return','year_closing',
                      'vat_settlement','vat_payment')),
                    source_id INTEGER NOT NULL, note TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));
                  INSERT INTO ledger_entry_new(entry_id,voucher_no,entry_date,account_code,account_name,debit,credit,
                                               source_type,source_id,note,created_at)
                    SELECT entry_id,voucher_no,entry_date,account_code,account_name,debit,credit,
                           source_type,source_id,note,created_at FROM ledger_entry;
                  DROP TABLE ledger_entry;
                  ALTER TABLE ledger_entry_new RENAME TO ledger_entry;
                  CREATE INDEX ix_ledger_voucher ON ledger_entry(voucher_no);
                  CREATE INDEX ix_ledger_source  ON ledger_entry(source_type, source_id);
                  COMMIT;""")
                print("[migrate] ledger_entry 加上營業稅結算 / 繳納來源")
            except Exception:
                cx.rollback()
                raise
            finally:
                cx.close()
        execute("""CREATE TABLE IF NOT EXISTS vat_period (period_id INTEGER PRIMARY KEY, year INTEGER NOT NULL,
                   period INTEGER NOT NULL CHECK (period BETWEEN 1 AND 6), settle_date TEXT NOT NULL,
                   output_tax REAL NOT NULL DEFAULT 0, input_tax REAL NOT NULL DEFAULT 0,
                   credit_before REAL NOT NULL DEFAULT 0, payable REAL NOT NULL DEFAULT 0,
                   credit_after REAL NOT NULL DEFAULT 0, paid_date TEXT, paid_account TEXT,
                   created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')), UNIQUE (year, period))""")
        chart.apply_chart_additions()
        lsql = q1("SELECT sql FROM sqlite_master WHERE name='ledger_entry'").get("sql") or ""
        if lsql and "goods_purchase" not in lsql:
            print("[migrate] 警告:資料庫還是改版前的結構(有存貨/進貨那一版),"
                  "付小農貨款、退款/退回小農會存不進去,請重建資料庫")
    except Exception as e:
        print("[migrate] 略過:", e)

migrate_db()


# ---------- CSV 匯出 ------------------------------------------
def csv_response(filename, header, rows):
    from urllib.parse import quote
    buf = io.StringIO()
    buf.write("﻿")            # BOM，讓 Excel 正確顯示中文
    w = csv.writer(buf)
    if header:
        w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    cd = f"attachment; filename=export.csv; filename*=UTF-8''{quote(filename)}"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": cd})

def _season_arg(request):
    sv = request.query_params.get("season", "")
    return None if sv in ("", "all") else int(sv)

def _orders_filter_arg(request):
    return (request.query_params.get("filter", "all"),
            (request.query_params.get("q") or "").strip())

@app.get("/export/order_lines.csv")
def export_order_lines(request: Request):
    sc, sp = Q._S(_season_arg(request))
    fc, fp = Q.orders_filter(*_orders_filter_arg(request))
    rows = q(f"""SELECT o.order_no    AS c_no,
                        o.order_date  AS c_date,
                        o.season      AS c_season,
                        o.order_kind  AS c_kind,
                        cu.display_name AS c_cust,
                        ch.name       AS c_channel,
                        p.sku         AS c_sku,
                        p.name        AS c_product,
                        ol.qty        AS c_qty,
                        ol.unit_price AS c_price,
                        ol.list_price AS c_list,
                        ol.line_subtotal AS c_subtotal,
                        CASE ol.is_gift WHEN 1 THEN '是' ELSE '' END AS c_gift,
                        b.batch_code  AS c_batch,
                        o.discount_total AS c_odisc,
                        o.order_total AS c_ototal
                 FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                 JOIN product p ON p.product_id=ol.product_id
                 LEFT JOIN customer cu ON cu.customer_id=o.customer_id
                 LEFT JOIN channel ch ON ch.channel_id=o.channel_id
                 LEFT JOIN batch b ON b.batch_id=ol.batch_id
                 WHERE {sc} AND {fc}
                 ORDER BY o.order_date, o.order_no, ol.line_id""", sp + fp)
    # 訂單層欄位(折扣 / 應收合計)只放在每張單的第一列,避免加總時重複計
    seen, out = set(), []
    for r in rows:
        first = r["c_no"] not in seen
        seen.add(r["c_no"])
        out.append([r["c_no"], r["c_date"], r["c_season"], r["c_kind"], r["c_cust"],
                    r["c_channel"], r["c_sku"], r["c_product"], r["c_qty"], r["c_price"],
                    r["c_list"], r["c_subtotal"], r["c_gift"], r["c_batch"],
                    r["c_odisc"] if first else "",
                    r["c_ototal"] if first else ""])
    return csv_response("訂單明細.csv",
        ["單號","日期","產季","種類","客戶","管道","商品編號","品名",
         "數量","成交單價","定價","小計","贈品","批次",
         "訂單折扣","訂單應收合計"],
        out)

@app.get("/export/orders.csv")
def export_orders(request: Request):
    sc, sp = Q._S(_season_arg(request))
    fc, fp = Q.orders_filter(*_orders_filter_arg(request))
    rows = q(f"""SELECT o.order_no, o.order_date, o.season, o.order_kind,
                        cu.display_name, ch.name,
                        o.discount_total, o.order_total,
                        o.payment_method, o.payment_status, o.paid_date, o.paid_amount,
                        o.ship_method, o.carrier, o.tracking_no, o.shipped_date, o.ship_status,
                        o.shipping_cost_actual,
                        CASE o.invoiced WHEN 1 THEN '是' ELSE '否' END, o.tax_doc_no
                 FROM "order" o
                 LEFT JOIN customer cu ON cu.customer_id=o.customer_id
                 LEFT JOIN channel ch ON ch.channel_id=o.channel_id
                 WHERE {sc} AND {fc} ORDER BY o.order_date, o.order_no""", sp + fp)
    return csv_response("訂單.csv",
        ["單號","日期","產季","種類","客戶","管道","折扣","應收合計",
         "付款方式","收款狀態","收款日","實收金額","出貨方式","物流商","物流單號","出貨日","出貨狀態",
         "運費","已開發票","發票號碼"],
        [list(r.values()) for r in rows])

@app.get("/export/customers.csv")
def export_customers():
    rows = q("""SELECT display_name, customer_type, segment, phone, email, contact_person,
                       invoice_title, tax_id, tax_doc_pref, tags, note,
                       (SELECT COUNT(*) FROM "order" o WHERE o.customer_id=customer.customer_id) n_orders,
                       (SELECT COALESCE(SUM(order_total),0) FROM "order" o
                          WHERE o.customer_id=customer.customer_id AND o.order_kind='銷售') total
                FROM customer ORDER BY total DESC, display_name""")
    return csv_response("客戶.csv",
        ["名稱","類型","分級","電話","Email","聯絡人","發票抬頭","統一編號","單據需求",
         "標籤","備註","訂單數","累計金額"],
        [list(r.values()) for r in rows])

@app.get("/export/report.csv")
def export_report(request: Request):
    season = _season_arg(request)
    asof = request.query_params.get("asof", "latest") or "latest"
    tab = request.query_params.get("tab", "profit")
    if tab == "aging":
        _, rows = Q.ar_aging(season, asof)
        return csv_response("收款帳齡.csv",
            ["單號","客戶","日期","金額","狀態","距今天數"],
            [[r["order_no"], r["cust"] or "", r["order_date"], round(r["order_total"]),
              r["payment_status"], r["days"]] for r in rows])
    if tab == "compare":
        klist, _ = Q.season_compare()
        return csv_response("跨產季比較.csv",
            ["產季","營收","訂單","客單價","毛利","毛利率","公關贈品成本"],
            [[d["season"], round(d["revenue"]), d["orders"], round(d["aov"]),
              round(d["gross_profit"]), f"{d['margin']*100:.0f}%", round(d["pr_cost"])] for d in klist])
    # 預設:獲利分析
    prod = Q.profit_by_product(season)
    return csv_response("獲利分析.csv",
        ["商品","售出量","單位","營收","折扣","每件成本","毛利","毛利率","實際成交均價","定價"],
        [[p["name"], round(p["qty"]), p["uom"], round(p["rev"]), round(p["disc"]),
          round(p["unit_cost"] or 0), round(p["gp"]),
          f"{(p['gp']/p['rev'] if p['rev'] else 0)*100:.0f}%",
          round(p["avg_price"] or 0), round(p["list_price"] or 0)] for p in prod])


# ---------- 儀表板 ---------------------------------------------------
ASOF_OPTS = {"latest": "最新訂單日", "today": "今天"}
VIEW_OPTS = {"season": "依產季", "calendar": "依西曆年"}   # 2026-10:經營報表檢視切換


def _season_ctx(request: Request):
    """回傳 (view, period, asof, ctx)。view='season' 時 period 是單一產季 int/None;
    view='calendar' 時 period 是單一西曆年 int/None(讀 ?year=,跟 ?season= 分開,
    避免同一個數字在兩個模式下意義搞混)。"""
    view = request.query_params.get("view", "season")
    if view not in VIEW_OPTS:
        view = "season"
    asof = request.query_params.get("asof", "latest") or "latest"
    if view == "calendar":
        yv = request.query_params.get("year", "")
        year = None if yv in ("", "all") else int(yv)
        return view, year, asof, dict(
            view=view, view_opts=VIEW_OPTS,
            seasons=Q.seasons(), season_sel="all",
            years=Q.calendar_years(), year_sel=yv or "all",
            asof_sel=asof, asof_opts=ASOF_OPTS)
    sv = request.query_params.get("season", "")
    season = None if sv in ("", "all") else int(sv)
    return view, season, asof, dict(
        view=view, view_opts=VIEW_OPTS,
        seasons=Q.seasons(), season_sel=sv or "all",
        years=Q.calendar_years(), year_sel="all",
        asof_sel=asof, asof_opts=ASOF_OPTS)


def _seasons_ctx(request: Request):
    """產季(或西曆年)可複選(?season=2025&season=2026 / ?year=2025&year=2026)——
    儀表板 / 各產品線損益 / 財務健康共用。沒帶值 → 預設只看最新一個;明確帶 all → 全部。
    回傳 (view, period_sel, asof, ctx);period_sel 為 list[int] 或 None(=全部)。"""
    view = request.query_params.get("view", "season")
    if view not in VIEW_OPTS:
        view = "season"
    asof = request.query_params.get("asof", "latest") or "latest"
    all_seasons = Q.seasons()
    all_years = Q.calendar_years()

    if view == "calendar":
        default_sel = all_years[-1:]
        raw = request.query_params.getlist("year")
        if not raw:
            sel = default_sel
        elif "all" in raw:
            sel = []
        else:
            sel = sorted({int(v) for v in raw if v.strip().isdigit() and int(v) in all_years})
            if not sel:
                sel = default_sel
        season_qs = "&".join(f"year={y}" for y in sel) if sel else "year=all"
        return view, (sel or None), asof, dict(
            view=view, view_opts=VIEW_OPTS,
            seasons=all_seasons, seasons_sel=[], season_qs=season_qs,
            years=all_years, years_sel=sel,
            asof_sel=asof, asof_opts=ASOF_OPTS)

    default_sel = all_seasons[-1:]                    # 預設:最新產季
    raw = request.query_params.getlist("season")
    if not raw:
        sel = default_sel
    elif "all" in raw:
        sel = []                                     # 明確選「全部」
    else:
        sel = sorted({int(v) for v in raw if v.strip().isdigit() and int(v) in all_seasons})
        if not sel:
            sel = default_sel
    season_qs = "&".join(f"season={s}" for s in sel) if sel else "season=all"
    return view, (sel or None), asof, dict(
        view=view, view_opts=VIEW_OPTS,
        seasons=all_seasons, seasons_sel=sel, season_qs=season_qs,
        years=all_years, years_sel=[],
        asof_sel=asof, asof_opts=ASOF_OPTS)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    view, period, asof, ctx = _seasons_ctx(request)
    mode_kw = dict(mode=view, year=period) if view == "calendar" else dict(mode=view)
    season_arg = None if view == "calendar" else period
    k = Q.kpi(season_arg, asof, **mode_kw)
    mon = Q.monthly(season_arg, **mode_kw)
    monmax = max([m["rev"] for m in mon] + [1])
    fin = Q.finance_summary(season_arg, **mode_kw)
    prog = Q.season_progress(period) if view == "season" else None   # 旺季進度沒有西曆年版本,見計畫第 7 節
    # 各產品線賺不賺(pl)2026-09 暫時隱藏,先不算 —— 要恢復時把這行打開,
    # 連同 dashboard.html 裡對應那段一起解除註解。
    # pl = Q.product_line_pnl(season_arg, "rev", **mode_kw)
    return tpl.TemplateResponse("dashboard.html", dict(
        request=request, active="dash", k=k, fin=fin, prog=prog,
        mon_json=json.dumps(mon), monmax=monmax,
        alerts=Q.alerts(season_arg, asof, **mode_kw), **ctx,
    ))


# ---------- 報表 -----------------------------------------------
REPORT_TABS = {"profit": "獲利分析", "aging": "收款帳齡", "compare": "跨產季比較"}

@app.get("/reports", response_class=HTMLResponse)
def reports(request: Request):
    view, period, asof, ctx = _season_ctx(request)
    tab = request.query_params.get("tab", "profit")
    if tab not in REPORT_TABS:
        tab = "profit"
    if tab == "compare":
        view = "season"   # 跨產季比較沒有西曆年版本,這個 tab 固定維持產季(見計畫第 7 節)
        ctx = {**ctx, "view": view}
    mode_kw = dict(mode=view, year=period) if view == "calendar" else dict(mode=view)
    season_arg = None if view == "calendar" else period
    data = dict(request=request, active="report", tab=tab, tabs=REPORT_TABS, **ctx)
    if tab == "profit":
        pm = Q.product_mix(season_arg, **mode_kw)
        pmtot = sum(p["rev"] for p in pm) or 1
        for p in pm:
            p["share"] = p["rev"] / pmtot
        nr = Q.new_vs_repeat(season_arg, **mode_kw)
        nrtot = (nr["repeat_rev"] + nr["new_rev"]) or 1
        data.update(prod=Q.profit_by_product(season_arg, **mode_kw), ch=Q.by_channel(season_arg, **mode_kw),
                    pm=pm, nr=nr, nr_rep_pct=nr["repeat_rev"] / nrtot,
                    top=Q.top_customers(season_arg, **mode_kw))
    elif tab == "aging":
        buckets, rows = Q.ar_aging(season_arg, asof, **mode_kw)
        data.update(buckets=buckets, rows=rows,
                    agmax=max(list(buckets.values()) + [1]),
                    paid_recent=Q.ar_recently_paid(season_arg, **mode_kw))
    elif tab == "compare":
        klist, series = Q.season_compare()
        data.update(klist=klist, series=series,
                    smax=max([v for d in series.values() for v in d.values()] + [1]))
    return tpl.TemplateResponse("reports.html", data)


# ---------- 產品線:事業別 → 產品群組 → SKU ------------------
TAX_CLASSES = ['待確認', '應稅', '免稅', '零稅率']

@app.get("/product-lines", response_class=HTMLResponse)
def product_lines(request: Request):
    bus = q("SELECT bu_id, name, sort FROM business_unit ORDER BY sort, bu_id")
    groups = q("""SELECT g.pg_id, g.bu_id, g.name, g.sort,
                    (SELECT COUNT(*) FROM product p WHERE p.product_group_id=g.pg_id) n_sku
                  FROM product_group g ORDER BY g.sort, g.pg_id""")
    skus = q("""SELECT product_id, sku, name, status, product_group_id
                FROM product ORDER BY status, sku""")
    by_bu = {}
    for g in groups:
        g["skus"] = [s for s in skus if s["product_group_id"] == g["pg_id"]]
        by_bu.setdefault(g["bu_id"], []).append(g)
    for b in bus:
        b["groups"] = by_bu.get(b["bu_id"], [])
    unassigned = [s for s in skus if s["product_group_id"] is None]
    return tpl.TemplateResponse("product_lines.html", dict(
        request=request, active="prodline",
        bus=bus, groups=groups, skus=skus, unassigned=unassigned))

@app.post("/product-lines/bu")
async def product_line_bu_save(request: Request):
    f = await request.form()
    name = (f.get("name") or "").strip()
    bid = f.get("bu_id")
    if f.get("_delete") and bid:
        if not q("SELECT 1 FROM product_group WHERE bu_id=?", (int(bid),)):
            execute("DELETE FROM business_unit WHERE bu_id=?", (int(bid),))
        return RedirectResponse("/product-lines", status_code=303)
    if not name:
        return RedirectResponse("/product-lines", status_code=303)
    dup = q("SELECT bu_id FROM business_unit WHERE name=?", (name,))
    if bid:
        if not dup or dup[0]["bu_id"] == int(bid):
            execute("UPDATE business_unit SET name=? WHERE bu_id=?", (name, int(bid)))
    elif not dup:
        n = q("SELECT COALESCE(MAX(sort),0)+1 s FROM business_unit")[0]["s"]
        execute("INSERT INTO business_unit(name,sort) VALUES(?,?)", (name, n))
    return RedirectResponse("/product-lines", status_code=303)

@app.post("/product-lines/group")
async def product_line_group_save(request: Request):
    f = await request.form()
    gid = f.get("pg_id")
    if f.get("_delete") and gid:
        if not q("SELECT 1 FROM product WHERE product_group_id=?", (int(gid),)):
            execute("DELETE FROM product_group WHERE pg_id=?", (int(gid),))
        return RedirectResponse("/product-lines", status_code=303)
    name = (f.get("name") or "").strip()
    if gid:
        row = q("SELECT bu_id FROM product_group WHERE pg_id=?", (int(gid),))
        if not row:
            return RedirectResponse("/product-lines", status_code=303)
        dup = q("SELECT pg_id FROM product_group WHERE bu_id=? AND name=?", (row[0]["bu_id"], name))
        if name and (not dup or dup[0]["pg_id"] == int(gid)):
            execute("UPDATE product_group SET name=? WHERE pg_id=?", (name, int(gid)))
    else:
        bu_id = f.get("bu_id")
        if not (name and bu_id):
            return RedirectResponse("/product-lines", status_code=303)
        if not q("SELECT 1 FROM product_group WHERE bu_id=? AND name=?", (int(bu_id), name)):
            n = q("SELECT COALESCE(MAX(sort),0)+1 s FROM product_group")[0]["s"]
            execute("INSERT INTO product_group(bu_id,name,sort) VALUES(?,?,?)",
                    (int(bu_id), name, n))
    return RedirectResponse("/product-lines", status_code=303)

@app.post("/product-lines/assign")
async def product_line_assign(request: Request):
    f = await request.form()
    for k in f.keys():
        if not k.startswith("g_"):
            continue
        pid = int(k[2:])
        v = (f.get(k) or "").strip()
        execute("UPDATE product SET product_group_id=? WHERE product_id=?",
                (int(v) if v else None, pid))
    return RedirectResponse("/product-lines", status_code=303)


# ---------- 固定資產卡 + 折舊(v2 回合 3) -------------------
# 2026-10-08 同仁回饋:耐用年數最少 10 年(畫面跟存檔都擋),殘值一律 0
ASSET_MIN_LIFE = 10          # 年;2026-10-16 起表單填「月」,最少 ASSET_MIN_LIFE*12 = 120 個月
ASSET_MIN_MONTHS = ASSET_MIN_LIFE * 12
ASSET_CATS = {"機器設備": 120, "生財器具": 120, "運輸設備": 120,
              "電腦設備": 120, "房屋建築": 360, "其他": 120}   # 預設使用年限(月)

@app.get("/assets", response_class=HTMLResponse)
def assets_page(request: Request):
    Q.sync_depreciation_vouchers()
    rows = Q.asset_list()
    in_use = [a for a in rows if a["in_use"]]
    summary = dict(
        cost=sum(a["cost"] or 0 for a in in_use),
        grant=sum(a["grant_amount"] or 0 for a in in_use),
        accum=sum(a["accum_dep"] for a in in_use),
        book=sum(a["book_value"] for a in in_use),
        monthly=sum(a["monthly"] for a in in_use))
    return tpl.TemplateResponse("assets.html", dict(
        request=request, active="asset", rows=rows, summary=summary))

def _asset_form_ctx(request, a, prefill=None):
    return dict(request=request, active="asset", a=a, prefill=prefill,
               cats=ASSET_CATS, min_months=ASSET_MIN_MONTHS, today=dt.date.today().isoformat(),
               cash_accounts=chart.accounts(["C"]))

@app.get("/assets/new", response_class=HTMLResponse)
def asset_new(request: Request):
    return tpl.TemplateResponse("asset_form.html", _asset_form_ctx(request, None))

@app.get("/assets/{aid}/edit", response_class=HTMLResponse)
def asset_edit(request: Request, aid: int):
    a = q("SELECT * FROM fixed_asset WHERE asset_id=?", (aid,))
    if not a:
        return RedirectResponse("/assets", status_code=303)
    return tpl.TemplateResponse("asset_form.html", _asset_form_ctx(request, a[0]))

@app.post("/assets")
async def asset_save(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    fl = lambda k: float(f.get(k)) if (f.get(k) or "").strip() else 0.0
    aid = f.get("asset_id")
    if f.get("_delete") and aid:
        execute("DELETE FROM fixed_asset WHERE asset_id=?", (int(aid),))
        ledger.delete_voucher_for("asset_acquire", int(aid))
        ledger.delete_voucher_for("asset_depreciation", int(aid))
        return RedirectResponse("/assets", status_code=303)
    name = (f.get("name") or "").strip()
    date = (f.get("acquire_date") or "").strip()
    if not (name and date):
        return RedirectResponse("/assets", status_code=303)
    try:
        months = int(f.get("life_months") or ASSET_MIN_MONTHS)
    except ValueError:
        months = 0
    if months < ASSET_MIN_MONTHS:   # 畫面已經擋了,這裡是防有人繞過畫面直接送
        back = f"/assets/{aid}/edit" if aid else "/assets/new"
        return RedirectResponse(f"{back}?err=life", status_code=303)
    cost = fl("cost")
    tax = fl("tax_amount")
    category = g("category")
    payment_account = g("payment_account") or "現金"
    cols = dict(
        name=name, category=category,
        acquire_date=date, cost=cost, grant_amount=0, salvage=0, tax_amount=tax,
        life_months=months, life_years=months // 12, method="平均法",
        disposed_date=g("disposed_date"), payment_account=payment_account, note=g("note"))
    if aid:
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE fixed_asset SET {sets} WHERE asset_id=:id", {**cols, "id": int(aid)})
        new_id = int(aid)
    else:
        keys = ",".join(cols)
        new_id = execute(f"INSERT INTO fixed_asset({keys}) VALUES({','.join(':' + k for k in cols)})", cols)
    legs = ledger.compose_asset_acquire_entries(category, cost, tax, payment_account)
    ledger.save_voucher("asset_acquire", new_id, date, legs, note=f"設備取得:{name}")
    return RedirectResponse("/assets", status_code=303)


# ---------- 商品目錄 + 定價 -----------------------------------
PRICE_SEGS = ['零售']   # 2026-10-16 客戶不分級,商品只剩一個售價(存在 price_list 的「零售」那列)
PROD_TYPES = ['單品', '組合', '禮盒', '裸裝', '試吃包', '加購贈品']
PKG_FORMS  = ['夾鏈袋', '罐', '禮盒', '真空袋', '裸裝']
UOMS       = ['包', '斤', '盒', '罐', '支']
STORAGE    = ['常溫', '冷藏', '冷凍']
# customer.segment -> price_list.customer_segment
SEG_MAP = {}   # 2026-10-16 客戶不分級:一律用「零售」那一欄的價(商品目錄只剩一個售價)

@app.get("/products", response_class=HTMLResponse)
def products(request: Request):
    return tpl.TemplateResponse("products.html", dict(
        request=request, active="prod",
        rows=q("""SELECT p.*, pg.name pg_name FROM product p
                  LEFT JOIN product_group pg ON pg.pg_id=p.product_group_id
                  ORDER BY p.status, p.sku""")))

def _price_map(pid):
    rows = q("SELECT customer_segment, unit_price FROM price_list WHERE product_id=? AND channel_id IS NULL", (pid,))
    return {r["customer_segment"]: r["unit_price"] for r in rows}

def _pg_options():
    """產品線下拉選項(事業 → 產品群組),給商品目錄表單挑「歸屬產品線」用。"""
    return q("""SELECT g.pg_id, g.name, b.name bu_name
                FROM product_group g JOIN business_unit b ON b.bu_id=g.bu_id
                ORDER BY b.sort, g.sort, g.pg_id""")

@app.get("/products/new", response_class=HTMLResponse)
def product_new(request: Request):
    return tpl.TemplateResponse("product_form.html", dict(
        request=request, active="prod", p=None, prices={}, groups=_pg_options(),
        segs=PRICE_SEGS, types=PROD_TYPES, forms=PKG_FORMS, uoms=UOMS, storage=STORAGE))

def _product_delete_block_reason(pid):
    """商品不能刪除的白話原因;沒有卡住的地方回傳 None。"""
    n_orders = q("SELECT COUNT(DISTINCT order_id) n FROM order_line WHERE product_id=?", (pid,))[0]["n"]
    n_batch = q("SELECT COUNT(*) n FROM batch WHERE product_id=?", (pid,))[0]["n"]
    n_target = q("SELECT COUNT(*) n FROM sales_target WHERE product_id=?", (pid,))[0]["n"]
    reasons = []
    if n_orders:
        reasons.append(f"有 {n_orders} 張訂單用過這個商品")
    if n_batch:
        reasons.append(f"有 {n_batch} 筆舊的焙製批次紀錄")
    if n_target:
        reasons.append("設過產季目標")
    return "、".join(reasons) if reasons else None

@app.get("/products/{pid}/edit", response_class=HTMLResponse)
def product_edit(request: Request, pid: int):
    p = q("SELECT * FROM product WHERE product_id=?", (pid,))
    if not p:
        return RedirectResponse("/products", status_code=303)
    block_reason = _product_delete_block_reason(pid) if request.query_params.get("perr") else None
    return tpl.TemplateResponse("product_form.html", dict(
        request=request, active="prod", p=p[0], prices=_price_map(pid), groups=_pg_options(),
        segs=PRICE_SEGS, types=PROD_TYPES, forms=PKG_FORMS, uoms=UOMS, storage=STORAGE,
        block_reason=block_reason))

@app.post("/products")
async def product_save(request: Request):
    f = await request.form()
    if f.get("_delete"):
        pid = f.get("product_id")
        if pid:
            pid = int(pid)
            if _product_delete_block_reason(pid):
                return RedirectResponse(f"/products/{pid}/edit?perr=1", status_code=303)
            execute("DELETE FROM price_list WHERE product_id=?", (pid,))
            execute("DELETE FROM product WHERE product_id=?", (pid,))
        return RedirectResponse("/products", status_code=303)
    g = lambda k: (f.get(k) or "").strip() or None
    ig = lambda k: int(f.get(k)) if (f.get(k) or "").strip() else None
    cols = dict(sku=(f.get("sku") or "").strip() or next_product_code(), name=(f.get("name") or "").strip(),
                product_type=g("product_type"), type_code_raw=g("type_code_raw"),
                product_group_id=ig("product_group_id"),
                net_weight_g=ig("net_weight_g"), gross_weight_g=ig("gross_weight_g"),
                package_form=g("package_form"), uom=(f.get("uom") or "包").strip(),
                grams_per_uom=ig("grams_per_uom"), shelf_life_days=ig("shelf_life_days"),
                storage_condition=g("storage_condition"), ingredients=g("ingredients"),
                origin=g("origin"), barcode=g("barcode"),
                gift_only=1 if f.get("gift_only") else 0,
                status=(f.get("status") or "在售").strip(), note=g("note"))
    pid = f.get("product_id")
    if pid:
        pid = int(pid)
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE product SET {sets} WHERE product_id=:id", {**cols, "id": pid})
    else:
        keys = ",".join(cols)
        pid = execute(f"INSERT INTO product({keys}) VALUES({','.join(':'+k for k in cols)})", cols)
    # 定價:重寫這個商品的通用定價(每分級一列)
    execute("DELETE FROM price_list WHERE product_id=? AND channel_id IS NULL", (pid,))
    for seg in PRICE_SEGS:
        v = f.get(f"price_{seg}")
        if (v or "").strip():
            execute("""INSERT INTO price_list(product_id,customer_segment,unit_price,effective_from)
                       VALUES(?,?,?,'2000-01-01')""", (pid, seg, float(v)))
    return RedirectResponse("/products", status_code=303)


# ---------- 通路 -------------------------------------------
CH_CATS = ['官網', 'LINE社群', '電商平台', '超商賣貨便', '實體寄售', '市集展售', '媒體導流', '批發', '直售']

@app.get("/channels", response_class=HTMLResponse)
def channels_page(request: Request):
    rows = q("""SELECT c.*,
                  (SELECT COUNT(*) FROM "order" o WHERE o.channel_id=c.channel_id) n_orders
                FROM channel c ORDER BY c.channel_id""")
    return tpl.TemplateResponse("channels.html", dict(request=request, active="chan", rows=rows))

@app.get("/channels/new", response_class=HTMLResponse)
def channel_new(request: Request):
    return tpl.TemplateResponse("channel_form.html", dict(request=request, active="chan", c=None, cats=CH_CATS))

def _channel_delete_block_reason(chid):
    """通路不能刪除的白話原因;沒有卡住的地方回傳 None。"""
    n_orders = q('SELECT COUNT(*) n FROM "order" WHERE channel_id=?', (chid,))[0]["n"]
    n_cust = q("SELECT COUNT(*) n FROM customer WHERE primary_channel_id=?", (chid,))[0]["n"]
    n_price = q("SELECT COUNT(*) n FROM price_list WHERE channel_id=?", (chid,))[0]["n"]
    reasons = []
    if n_orders:
        reasons.append(f"有 {n_orders} 張訂單用這個管道")
    if n_cust:
        reasons.append(f"有 {n_cust} 位客戶的主要管道是它")
    if n_price:
        reasons.append(f"有 {n_price} 筆這個管道的專屬定價")
    return "、".join(reasons) if reasons else None

@app.get("/channels/{chid}/edit", response_class=HTMLResponse)
def channel_edit(request: Request, chid: int):
    c = q("SELECT * FROM channel WHERE channel_id=?", (chid,))
    if not c:
        return RedirectResponse("/channels", status_code=303)
    block_reason = _channel_delete_block_reason(chid) if request.query_params.get("perr") else None
    return tpl.TemplateResponse("channel_form.html", dict(
        request=request, active="chan", c=c[0], cats=CH_CATS, block_reason=block_reason))

@app.post("/channels")
async def channel_save(request: Request):
    f = await request.form()
    if f.get("_delete"):
        chid = f.get("channel_id")
        if chid:
            chid = int(chid)
            if _channel_delete_block_reason(chid):
                return RedirectResponse(f"/channels/{chid}/edit?perr=1", status_code=303)
            execute("DELETE FROM channel WHERE channel_id=?", (chid,))
        return RedirectResponse("/channels", status_code=303)
    g = lambda k: (f.get(k) or "").strip() or None
    cols = dict(code=(f.get("code") or "").strip(), name=(f.get("name") or "").strip(),
                category=g("category"),
                settlement_lag_days=int(f.get("settlement_lag_days") or 0), note=g("note"))
    chid = f.get("channel_id")
    if chid:
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE channel SET {sets} WHERE channel_id=:id", {**cols, "id": int(chid)})
    else:
        keys = ",".join(cols)
        execute(f"INSERT INTO channel({keys}) VALUES({','.join(':'+k for k in cols)})", cols)
    return RedirectResponse("/channels", status_code=303)


# ---------- 客戶資料庫 -----------------------------------------
CUST_TYPES = ['企業戶', '個人戶']   # 客戶分類(2026-10-16 依 Excel,客戶不分級)


def next_cust_no(year=None):
    """客戶編號 = C + 西曆年 + 當年 3 碼流水號(例 C2026001)。"""
    y = str(year or dt.date.today().year)
    m = q1("SELECT MAX(CAST(SUBSTR(cust_no, 6) AS INTEGER)) m FROM customer WHERE cust_no LIKE ?",
           (f"C{y}%",)).get("m") or 0
    return f"C{y}{m + 1:03d}"


def next_product_code():
    """品項代碼 = F + 3 碼流水號(例 F001,2026-10-16 依 Excel 自動編號)。"""
    nums = [int(r["sku"][1:]) for r in q("SELECT sku FROM product WHERE sku LIKE 'F%'") if r["sku"][1:].isdigit()]
    return f"F{(max(nums) if nums else 0) + 1:03d}"
CUST_SEGS  = ['零售', '批發', '團購主', '機構', '公關對象']
DOC_PREFS  = ['電子發票二聯', '電子發票三聯', '農民收據', '免開立']

def _cust_stats_sql(where=""):
    return f"""SELECT cu.*,
        (SELECT c.name FROM channel c WHERE c.channel_id=cu.primary_channel_id) chan,
        (SELECT COUNT(*) FROM "order" o WHERE o.customer_id=cu.customer_id AND o.order_kind='銷售') n_orders,
        (SELECT COALESCE(SUM(order_total),0) FROM "order" o
           WHERE o.customer_id=cu.customer_id AND o.order_kind='銷售') total,
        (SELECT MAX(order_date) FROM "order" o WHERE o.customer_id=cu.customer_id) last_order
      FROM customer cu {where}"""

@app.get("/customers", response_class=HTMLResponse)
def customers_list(request: Request, q_: str = ""):
    kw = (request.query_params.get("q") or "").strip()
    if kw:
        rows = q(_cust_stats_sql("""WHERE cu.customer_id IN
                   (SELECT customer_id FROM customer WHERE display_name LIKE :k
                    UNION SELECT customer_id FROM customer_alias WHERE alias_text LIKE :k)
                 ORDER BY total DESC, display_name"""), {"k": f"%{kw}%"})
    else:
        rows = q(_cust_stats_sql("ORDER BY total DESC, display_name"))
    return tpl.TemplateResponse("customers.html",
        dict(request=request, active="cust", rows=rows, kw=kw))

@app.get("/customers/new", response_class=HTMLResponse)
def customer_new(request: Request, next: str = "/customers"):
    return tpl.TemplateResponse("customer_form.html", dict(
        request=request, active="cust", cust=None, addr=None, aliases=[], next=next, dups=None,
        channels=q("SELECT channel_id,name FROM channel ORDER BY channel_id"),
        types=CUST_TYPES, segs=CUST_SEGS, docs=DOC_PREFS))


# ---- 訂單頁客戶搜尋 API（可搜尋選單 + 確認卡） -----------------
@app.get("/api/customers")
def api_customers(request: Request):
    kw = (request.query_params.get("q") or "").strip()
    if kw:
        rows = q("""SELECT cu.customer_id id, cu.display_name name, cu.customer_type segment, cu.cust_no, cu.phone,
                      (SELECT MAX(order_date) FROM "order" o WHERE o.customer_id=cu.customer_id) last_order,
                      (SELECT COUNT(*) FROM "order" o WHERE o.customer_id=cu.customer_id AND o.order_kind='銷售') n_orders
                    FROM customer cu
                    WHERE cu.customer_id IN (
                       SELECT customer_id FROM customer WHERE display_name LIKE :k OR phone LIKE :k
                       UNION SELECT customer_id FROM customer_alias WHERE alias_text LIKE :k)
                    ORDER BY n_orders DESC, cu.display_name LIMIT 25""", {"k": f"%{kw}%"})
    else:
        rows = q("""SELECT cu.customer_id id, cu.display_name name, cu.customer_type segment, cu.cust_no, cu.phone,
                      (SELECT MAX(order_date) FROM "order" o WHERE o.customer_id=cu.customer_id) last_order,
                      (SELECT COUNT(*) FROM "order" o WHERE o.customer_id=cu.customer_id AND o.order_kind='銷售') n_orders
                    FROM customer cu ORDER BY last_order DESC NULLS LAST, cu.display_name LIMIT 8""")
    return JSONResponse(rows)

@app.get("/api/customers/{cid}")
def api_customer(cid: int):
    c = q("""SELECT customer_id id, display_name name, customer_type segment, cust_no, phone, contact_person
             FROM customer WHERE customer_id=?""", (cid,))
    if not c:
        return JSONResponse({}, status_code=404)
    c = c[0]
    a = q("""SELECT recipient_name, recipient_phone, address_full
             FROM address WHERE customer_id=? ORDER BY is_default DESC LIMIT 1""", (cid,))
    c["address"] = a[0] if a else None
    c["orders"] = q("""SELECT order_no, order_date, order_total, payment_status
                       FROM "order" WHERE customer_id=? ORDER BY order_date DESC LIMIT 3""", (cid,))
    c["total"] = q("""SELECT COALESCE(SUM(order_total),0) v FROM "order"
                      WHERE customer_id=? AND order_kind='銷售'""", (cid,))[0]["v"]
    return JSONResponse(c)

@app.get("/customers/{cid}", response_class=HTMLResponse)
def customer_profile(request: Request, cid: int):
    cust = q("SELECT * FROM customer WHERE customer_id=?", (cid,))
    if not cust:
        return RedirectResponse("/customers", status_code=303)
    orders = q("""SELECT o.order_id, o.order_no, o.order_date, o.order_kind, o.order_total,
                         o.payment_status, o.ship_status,
                         (SELECT c.name FROM channel c WHERE c.channel_id=o.channel_id) chan
                  FROM "order" o WHERE o.customer_id=? ORDER BY o.order_date DESC""", (cid,))
    tot = sum(o["order_total"] for o in orders if o["order_kind"] == "銷售")
    return tpl.TemplateResponse("customer_profile.html", dict(
        request=request, active="cust", c=cust[0],
        addrs=q("SELECT * FROM address WHERE customer_id=? ORDER BY is_default DESC", (cid,)),
        aliases=q("SELECT alias_text FROM customer_alias WHERE customer_id=? ORDER BY alias_id", (cid,)),
        orders=orders, total=tot))

@app.get("/customers/{cid}/edit", response_class=HTMLResponse)
def customer_edit(request: Request, cid: int, next: str = ""):
    cust = q("SELECT * FROM customer WHERE customer_id=?", (cid,))
    if not cust:
        return RedirectResponse("/customers", status_code=303)
    addr = q("SELECT * FROM address WHERE customer_id=? ORDER BY is_default DESC LIMIT 1", (cid,))
    n_orders = q('SELECT COUNT(*) n FROM "order" WHERE customer_id=?', (cid,))[0]["n"] if request.query_params.get("perr") else 0
    return tpl.TemplateResponse("customer_form.html", dict(
        request=request, active="cust", cust=cust[0], addr=(addr[0] if addr else None),
        next=next or f"/customers/{cid}", dups=None, n_orders=n_orders,
        aliases=q("SELECT alias_text FROM customer_alias WHERE customer_id=? ORDER BY alias_id", (cid,)),
        channels=q("SELECT channel_id,name FROM channel ORDER BY channel_id"),
        types=CUST_TYPES, segs=CUST_SEGS, docs=DOC_PREFS))

@app.post("/customers")
async def customer_save(request: Request):
    f = await request.form()
    if f.get("_delete"):
        cid = f.get("customer_id")
        if cid:
            cid = int(cid)
            used = q('SELECT 1 FROM "order" WHERE customer_id=? LIMIT 1', (cid,))
            if used:
                return RedirectResponse(f"/customers/{cid}/edit?perr=1", status_code=303)
            execute("DELETE FROM address WHERE customer_id=?", (cid,))
            execute("DELETE FROM customer_alias WHERE customer_id=?", (cid,))
            execute("DELETE FROM customer WHERE customer_id=?", (cid,))
        return RedirectResponse("/customers", status_code=303)
    g = lambda k: (f.get(k) or "").strip() or None
    ch = int(f.get("primary_channel_id")) if f.get("primary_channel_id") else None
    name = (f.get("display_name") or "").strip()
    cid = f.get("customer_id")
    if not name:
        back = f"/customers/{cid}/edit" if cid else "/customers/new"
        return RedirectResponse(back, status_code=303)
    cols = dict(display_name=name, customer_type=g("customer_type"),
                primary_channel_id=ch, phone=g("phone"), email=g("email"),
                contact_person=g("contact_person"), invoice_title=g("invoice_title"),
                tax_id=g("tax_id"), tax_doc_pref=g("tax_doc_pref"),
                tags=g("tags"), note=g("note"))
    if cid:
        cid = int(cid)
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f'UPDATE customer SET {sets} WHERE customer_id=:id', {**cols, "id": cid})
    else:
        # 擋重名:同名已存在且未確認 -> 退回表單並列出既有客戶
        if not f.get("confirm_dup"):
            dups = q("""SELECT c.customer_id, c.display_name, c.segment, c.phone,
                          (SELECT COUNT(*) FROM "order" o WHERE o.customer_id=c.customer_id) n,
                          (SELECT MAX(order_date) FROM "order" o WHERE o.customer_id=c.customer_id) last_order,
                          (SELECT address_full FROM address a WHERE a.customer_id=c.customer_id
                             ORDER BY is_default DESC LIMIT 1) addr
                        FROM customer c WHERE c.display_name=?""", (name,))
            if dups:
                pending = dict(cols)
                for k in ("addr_recipient", "addr_phone", "addr_zip", "addr_full"):
                    pending[k] = f.get(k) or ""
                return tpl.TemplateResponse("customer_form.html", dict(
                    request=request, active="cust", cust=pending, addr=None, aliases=[],
                    next=f.get("next") or "/customers", dups=dups,
                    channels=q("SELECT channel_id,name FROM channel ORDER BY channel_id"),
                    types=CUST_TYPES, segs=CUST_SEGS, docs=DOC_PREFS))
        cols["cust_no"] = next_cust_no()
        keys = ",".join(cols)
        cid = execute(f'INSERT INTO customer({keys}) VALUES({",".join(":"+k for k in cols)})', cols)
        execute("INSERT OR IGNORE INTO customer_alias(customer_id,alias_text) VALUES(?,?)", (cid, name))
    # 預設收件地址(單筆 upsert)
    if any(f.get(k) for k in ("addr_recipient", "addr_full", "addr_phone", "addr_zip")):
        row = q("SELECT address_id FROM address WHERE customer_id=? AND is_default=1", (cid,))
        av = dict(recipient_name=g("addr_recipient"), recipient_phone=g("addr_phone"),
                  zipcode=g("addr_zip"), address_full=g("addr_full"))
        if row:
            sets = ",".join(f"{k}=:{k}" for k in av)
            execute(f"UPDATE address SET {sets} WHERE address_id=:id", {**av, "id": row[0]["address_id"]})
        else:
            execute("""INSERT INTO address(customer_id,label,is_default,recipient_name,recipient_phone,zipcode,address_full)
                       VALUES(?,?,1,?,?,?,?)""", (cid, "預設", av["recipient_name"], av["recipient_phone"],
                                                  av["zipcode"], av["address_full"]))
    nxt = f.get("next") or f"/customers/{cid}"
    return RedirectResponse(nxt, status_code=303)


# ---------- 客戶合併 --------------------------------------------
def _cust_brief(cid):
    c = q("SELECT customer_id, display_name, segment, phone FROM customer WHERE customer_id=?", (cid,))
    if not c:
        return None
    c = c[0]
    c["n_orders"] = q('SELECT COUNT(*) n FROM "order" WHERE customer_id=?', (cid,))[0]["n"]
    c["n_addr"] = q("SELECT COUNT(*) n FROM address WHERE customer_id=?", (cid,))[0]["n"]
    c["aliases"] = [r["alias_text"] for r in
                    q("SELECT alias_text FROM customer_alias WHERE customer_id=?", (cid,))]
    return c

@app.get("/customers/{cid}/merge", response_class=HTMLResponse)
def customer_merge_form(request: Request, cid: int):
    src = _cust_brief(cid)
    if not src:
        return RedirectResponse("/customers", status_code=303)
    return tpl.TemplateResponse("customer_merge.html", dict(request=request, active="cust", src=src))

@app.post("/customers/{cid}/merge")
async def customer_merge(request: Request, cid: int):
    f = await request.form()
    tgt = f.get("target_id")
    if not tgt or int(tgt) == cid:
        return RedirectResponse(f"/customers/{cid}/merge?err=1", status_code=303)
    tgt = int(tgt)
    if not q("SELECT 1 FROM customer WHERE customer_id=?", (tgt,)):
        return RedirectResponse(f"/customers/{cid}/merge?err=1", status_code=303)
    execute('UPDATE "order"  SET customer_id=? WHERE customer_id=?', (tgt, cid))
    execute('UPDATE address  SET customer_id=?, is_default=0 WHERE customer_id=?', (tgt, cid))
    execute("""INSERT OR IGNORE INTO customer_alias(customer_id, alias_text)
               SELECT ?, alias_text FROM customer_alias WHERE customer_id=?""", (tgt, cid))
    execute("INSERT OR IGNORE INTO customer_alias(customer_id, alias_text) "
            "SELECT ?, display_name FROM customer WHERE customer_id=?", (tgt, cid))
    execute("DELETE FROM customer_alias WHERE customer_id=?", (cid,))
    execute("DELETE FROM customer WHERE customer_id=?", (cid,))
    return RedirectResponse(f"/customers/{tgt}?merged=1", status_code=303)


def next_order_no(date=None):
    """訂單編號 = S + 訂單日期 YYYYMMDD + 當天 3 碼流水號(例 S20260904001,1015 版 Excel)。"""
    ymd = (date or dt.date.today().isoformat()).replace("-", "")
    m = q1("SELECT MAX(CAST(SUBSTR(order_no, 10) AS INTEGER)) m FROM \"order\" WHERE order_no LIKE ?",
           (f"S{ymd}%",)).get("m") or 0
    return f"S{ymd}{m + 1:03d}"


# ---------- 新增訂單 -------------------------------------------
@app.get("/orders/new", response_class=HTMLResponse)
def order_new(request: Request):
    customers = q("SELECT customer_id,display_name,segment FROM customer ORDER BY display_name")
    prices = {}
    for r in q("SELECT product_id,customer_segment,unit_price FROM price_list WHERE channel_id IS NULL"):
        prices.setdefault(r["product_id"], {})[r["customer_segment"]] = r["unit_price"]
    cust_seg = {c["customer_id"]: SEG_MAP.get(c["segment"], "零售") for c in customers}
    return tpl.TemplateResponse("orders_new.html", dict(cash_accounts=chart.accounts(["C"]), 
        request=request, active="order", customers=customers,
        channels=q("SELECT channel_id,name FROM channel ORDER BY channel_id"),
        products=q("SELECT product_id,sku,name,uom FROM product WHERE status='在售' ORDER BY sku"),
        today=dt.date.today().isoformat(),
        prices_json=json.dumps(prices), cust_seg_json=json.dumps(cust_seg),

    ))


@app.post("/orders")
async def order_create(request: Request):
    f = await request.form()
    def one(name, d=""):
        v = f.get(name); return v if v not in (None, "") else d
    def flt(name):
        try: return float(one(name, "0") or 0)
        except ValueError: return 0.0

    order_date = one("order_date")
    if not one("customer_id") or not one("channel_id"):
        return RedirectResponse("/orders/new?err=required", status_code=303)
    customer_id = int(one("customer_id"))
    channel_id = int(one("channel_id"))
    order_kind = one("order_kind", "銷售")

    # 客戶分級 -> 標準價
    seg_row = q("SELECT segment FROM customer WHERE customer_id=?", (customer_id,))
    pseg = SEG_MAP.get(seg_row[0]["segment"] if seg_row else None, "零售")
    stdprice = {r["product_id"]: r["unit_price"]
                for r in q("SELECT product_id,unit_price FROM price_list WHERE customer_segment=? AND channel_id IS NULL", (pseg,))}

    prods = f.getlist("product_id"); qtys = f.getlist("qty")
    ups   = f.getlist("unit_price")
    costs = f.getlist("line_cost")   # 每列成本,家易自己填(2026-10-16)
    lines, line_costs = [], []
    for i, p in enumerate(prods):
        if not p:
            continue
        try:
            qv = float(qtys[i] or 0)
        except (ValueError, IndexError):
            continue
        if qv <= 0:
            continue
        pid = int(p)
        lp = stdprice.get(pid)
        try:
            up = float(ups[i]) if (i < len(ups) and str(ups[i]).strip()) else None
        except (ValueError, IndexError):
            up = None
        if up is None:                       # 沒填單價 -> 帶標準價
            up = lp if (lp is not None and order_kind == "銷售") else 0
        lines.append((pid, qv, up, None, lp))
        line_costs.append(_num(costs[i]) if i < len(costs) else 0)

    discount_total = flt("discount_total")
    subtotal = sum(qv * up for _, qv, up, _, _ in lines)
    # 每列的成交價已是實收價;discount_total 只放使用者另外填的整單折讓。
    # 「賣得比定價低」的差額改由 list_price 於報表即時計算,不重複扣。
    # 運費(2026-10-16,1015 版 Excel):這裡的運費是「跟客人收的」,算進應收、也算收入。
    # 實際付給物流的運費由家易從營運支出記(6114 運費),訂單不再自動記運費支出。
    shipping_fee = flt("shipping_fee_charged")
    total = subtotal - discount_total + shipping_fee
    invoiced = 1 if one("invoiced") else 0
    if invoiced and not one("tax_doc_no"):
        return RedirectResponse("/orders/new?err=invoice", status_code=303)
    # 銷項稅額(2026-10-08):售價含稅,家易自己填稅額,系統不算;只擋明顯打錯(負數或比商品金額還大)
    sales_tax = flt("sales_tax") if order_kind == "銷售" else 0.0
    if sales_tax < 0 or (sales_tax and sales_tax >= total):
        return RedirectResponse("/orders/new?err=tax", status_code=303)
    # 收款(2026-10-17 同仁修改意見 0921 第 6 條):不再填「付款方式」「收款狀態」,改選收款科目——
    # E 應收帳款 = 還沒收(之後到應收帳款頁勾選收款);C 現金 / 銀行 = 當場收齊,收款日 = 訂單日。
    # 贈送 / 樣品等非銷售訂單一律免收款。
    recv = one("receive_account") if order_kind == "銷售" else None
    pay_status = "免收款" if order_kind != "銷售" else ("已收款" if recv else "待收款")
    oid = execute("""INSERT INTO "order"(order_no,order_date,season,customer_id,channel_id,order_kind,
                     discount_total,order_total,sales_tax,cost_payment_account,payment_method,payment_status,
                     shipping_fee_charged,ship_method,ship_status,invoiced,tax_doc_no)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, '待出貨',?,?)""",
                  (next_order_no(order_date), order_date, Q.season_of(order_date), customer_id, channel_id, order_kind,
                   discount_total, total, sales_tax, one("cost_payment_account") or "現金",
                   None, pay_status,
                   shipping_fee, one("ship_method") or None,
                   invoiced, one("tax_doc_no") or None))
    if recv:
        execute('UPDATE "order" SET paid_date=order_date, paid_amount=order_total, payment_account=? WHERE order_id=?',
                (recv, oid))
    for (p, qv, up, b, lp), lc in zip(lines, line_costs):
        execute("""INSERT INTO order_line(order_id,product_id,batch_id,qty,unit_price,list_price,line_subtotal,line_cost)
                   VALUES(?,?,?,?,?,?,?,?)""", (oid, p, b, qv, up, lp, qv * up, lc))
    _post_order_ledger(oid)
    return RedirectResponse(f"/orders/{oid}?ok=1", status_code=303)


def _num(v):
    """表單數字欄:空白 / 打錯 → 0,負數也當 0。"""
    try:
        return max(0.0, float(v or 0))
    except (TypeError, ValueError):
        return 0.0


def _post_order_ledger(oid):
    """訂單存檔/標記已收款後呼叫:重開 order_sale(成立分錄)+ order_payment(收款分錄)。
    贈送/樣品等非「銷售」訂單、或還沒收到錢,對應那張就不會產生(ledger.save_voucher
    在 legs 為空時只清空、不新增)。"""
    o, lines = Q.order_get(oid)
    if not o:
        return
    sale_lines = [(l["pg_name"], 0 if l["is_gift"] else (l["line_subtotal"] or 0), l["line_cost"] or 0)
                  for l in lines]
    sale_legs = ledger.compose_order_sale_entries(sale_lines, o["order_kind"], o["shipping_fee_charged"],
                                                  o["sales_tax"], o["cost_payment_account"])
    ledger.save_voucher("order_sale", oid, o["order_date"], sale_legs, note=f"訂單 {o['order_no']}")

    pay_legs = ledger.compose_order_payment_entries(o["paid_amount"], o["payment_account"])
    pay_date = o["paid_date"] or o["order_date"]
    ledger.save_voucher("order_payment", oid, pay_date, pay_legs, note=f"訂單 {o['order_no']} 收款")


# ---------- 訂單管理:清單 / 明細 / 更新 -----------------------
PAY_METHODS  = ['現金', '銀行匯款', '貨到付款', '行動支付', '信用卡', '平台代收', '未收款']
PAY_STATUS   = ['待收款', '已收款', '免收款']
SHIP_METHODS = ['自行配送', '客戶自取', '宅配', '超商店到店', '超商賣貨便', '冷藏宅配']
SHIP_STATUS  = ['待出貨', '已出貨', '已送達', '退回', '遺失', '破損']
SHIP_PAYERS  = ['店家吸收', '客戶付']
ORDER_KINDS  = ['銷售', '贈送-公關', '贈送-捐贈', '樣品', '理賠重寄', '換貨補出', '內部領用']
FILTERS = {"all": "全部", "overdue": "貨款逾期", "unpaid": "未收款", "unshipped": "超過 3 天未出貨"}

# ---------- 訂單匯入(Google 表單 / Excel,回合 13)-----------------
@app.get("/orders/import", response_class=HTMLResponse)
def order_import_page(request: Request):
    return tpl.TemplateResponse("orders_import.html", dict(
        request=request, active="order",
        channels=q("SELECT channel_id,name FROM channel ORDER BY channel_id")))


@app.get("/orders/import/template")
def order_import_template():
    products = q("SELECT name FROM product WHERE status='在售' ORDER BY sku")
    header = ["時間戳記", "客戶姓名", "客戶電話", "收件地址", "付款方式", "備註"] + [p["name"] for p in products]
    example = ["2026/10/2 下午 3:00:00", "王小明", "0933-111-222", "南投縣中寮鄉", "銀行匯款", "這是範例,請刪除這一列"]
    example += (["2"] + [""] * (len(products) - 1)) if products else []
    return csv_response("訂單匯入範本.csv", header, [example])


@app.post("/orders/import/preview")
async def order_import_preview(request: Request):
    f = await request.form()
    channel_id = f.get("channel_id")
    upload = f.get("file")
    if not channel_id or upload is None or not getattr(upload, "filename", ""):
        return RedirectResponse("/orders/import?err=required", status_code=303)
    content = await upload.read()
    try:
        headers, raw_rows = OI.parse_file(upload.filename, content)
    except Exception as e:
        return tpl.TemplateResponse("orders_import.html", dict(
            request=request, active="order",
            channels=q("SELECT channel_id,name FROM channel ORDER BY channel_id"),
            parse_err=str(e)))
    if not raw_rows:
        return tpl.TemplateResponse("orders_import.html", dict(
            request=request, active="order",
            channels=q("SELECT channel_id,name FROM channel ORDER BY channel_id"),
            parse_err="這個檔案解析不出任何一列資料,確認檔案跟表頭對不對。"))
    result = OI.match_rows(headers, raw_rows, PAY_METHODS)
    actionable = [r for r in result["rows"] if r["status"] in ("ok", "new_customer")]
    display_only = [r for r in result["rows"] if r["status"] in ("error", "duplicate")]
    chan = q1("SELECT name FROM channel WHERE channel_id=?", (int(channel_id),))
    return tpl.TemplateResponse("orders_import_preview.html", dict(
        request=request, active="order",
        filename=upload.filename, channel_id=int(channel_id), channel_name=chan.get("name"),
        summary=result["summary"], unmatched_headers=result["unmatched_headers"],
        actionable=actionable, display_only=display_only,
        customers=q("SELECT customer_id,display_name,phone FROM customer ORDER BY display_name"),
        rows_json=json.dumps(actionable, ensure_ascii=False)))


@app.post("/orders/import/confirm")
async def order_import_confirm(request: Request):
    f = await request.form()
    try:
        rows = json.loads(f.get("rows_json") or "[]")
    except (ValueError, TypeError):
        rows = []
    channel_id = int(f.get("channel_id") or 0) if (f.get("channel_id") or "").isdigit() else 0
    if not channel_id or not q1("SELECT channel_id FROM channel WHERE channel_id=?", (channel_id,)):
        return RedirectResponse("/orders/import?err=required", status_code=303)

    imported = 0
    for i, row in enumerate(rows):
        if f.get(f"skip_{i}"):
            continue
        resolve = f.get(f"resolve_{i}", "new")
        if resolve == "new":
            cid = execute("""INSERT INTO customer(cust_no,display_name,customer_type,primary_channel_id,phone)
                              VALUES(?,?,?,?,?)""",
                          (next_cust_no(), row["customer_name"] or row["phone"], "個人戶", channel_id,
                           row["phone_raw"] or row["phone"]))
            execute("INSERT OR IGNORE INTO customer_alias(customer_id,alias_text) VALUES(?,?)",
                    (cid, row["customer_name"] or row["phone"]))
            if row.get("address"):
                execute("""INSERT INTO address(customer_id,label,is_default,address_full)
                           VALUES(?,'匯入',1,?)""", (cid, row["address"]))
        elif resolve.isdigit():
            cid = int(resolve)
        else:
            continue

        seg_row = q1("SELECT segment FROM customer WHERE customer_id=?", (cid,))
        pseg = SEG_MAP.get(seg_row.get("segment"), "零售")
        stdprice = {r["product_id"]: r["unit_price"]
                    for r in q("SELECT product_id,unit_price FROM price_list WHERE customer_segment=? AND channel_id IS NULL",
                               (pseg,))}
        built_lines, subtotal = [], 0.0
        for ln in (row.get("lines") or []):
            up = stdprice.get(ln["product_id"], 0)
            built_lines.append((ln["product_id"], ln["qty"], up))
            subtotal += ln["qty"] * up
        if not built_lines:
            continue

        oid = execute("""INSERT INTO "order"(order_no,order_date,season,customer_id,channel_id,order_kind,
                         order_total,payment_method,payment_status,ship_status,source_ref,note)
                         VALUES(?,?,?,?,?, '銷售', ?,?, '待收款', '待出貨', ?,?)""",
                      (next_order_no(row["order_date"]), row["order_date"], Q.season_of(row["order_date"]), cid, channel_id,
                       subtotal, row.get("payment_method"), row["source_ref"], row.get("note") or None))
        for pid, qty, up in built_lines:
            execute("""INSERT INTO order_line(order_id,product_id,qty,unit_price,line_subtotal,line_cost)
                       VALUES(?,?,?,?,?,0)""", (oid, pid, qty, up, qty * up))
        _post_order_ledger(oid)
        imported += 1

    return RedirectResponse(f"/orders?imported={imported}", status_code=303)


@app.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request):
    flt = request.query_params.get("filter", "all")
    kw = (request.query_params.get("q") or "").strip()
    return tpl.TemplateResponse("orders_list.html", dict(
        request=request, active="order",
        rows=Q.orders_list(flt, kw), flt=flt, kw=kw, filters=FILTERS))

@app.get("/orders/{oid}", response_class=HTMLResponse)
def order_detail(request: Request, oid: int):
    o, lines = Q.order_get(oid)
    if not o:
        return RedirectResponse("/orders", status_code=303)
    return tpl.TemplateResponse("order_detail.html", dict(
        request=request, active="order", o=o, lines=lines,
        products=q("SELECT product_id,sku,name FROM product ORDER BY sku"),
        pay_methods=PAY_METHODS, pay_status=PAY_STATUS,
        ship_methods=SHIP_METHODS, ship_status=SHIP_STATUS, kinds=ORDER_KINDS,
        cash_accounts=chart.accounts(["C"]),
        # 退回小農沖的是各產品線的採購成本;「原料報廢損失」是退款用的,不列
        va_accounts=[a for a in chart.accounts(["VA"]) if a["code"] != ledger.REFUND_LOSS_ACCOUNT[0]],
        returns=Q.returns_list(oid), return_kinds=ledger.RETURN_KINDS,
        order_cost=sum(l["line_cost"] or 0 for l in lines),
        today=dt.date.today().isoformat()))


# ---------- 訂單退款 / 退回小農(2026-10-08 貿易公司模式) -----------
#   退貨又退款 → 直接刪訂單(order_delete 會連總帳一起刪),不走這裡。
#   退款 = 只退款不退貨(其他損失);退回小農 = 退貨不退款(小農退成本,沖應付帳款)。
def _post_return_ledger(rid, oid, date, kind, amount, payment_account=None, cost_account=None):
    o = q1('SELECT payment_status, payment_account FROM "order" WHERE order_id=?', (oid,))
    already_paid = o.get("payment_status") == "已收款"
    legs = ledger.compose_sales_return_entries(kind, amount, already_paid,
                                                payment_account or o.get("payment_account"), cost_account)
    ledger.save_voucher("sales_return", rid, date, legs, note=f"{kind}:訂單#{oid}")

@app.get("/returns", response_class=HTMLResponse)
def returns_page(request: Request):
    rows = Q.returns_list()
    return tpl.TemplateResponse("returns.html", dict(
        request=request, active="returns", rows=rows,
        refund_total=sum(r["amount"] for r in rows if r["kind"] == "退款"),
        farmer_total=sum(r["amount"] for r in rows if r["kind"] == "退回小農")))

@app.post("/orders/{oid}/return")
async def order_return_create(request: Request, oid: int):
    f = await request.form()
    o, _ = Q.order_get(oid)
    if not o:
        return RedirectResponse("/orders", status_code=303)
    date = (f.get("return_date") or "").strip() or dt.date.today().isoformat()
    kind = (f.get("kind") or "").strip()
    if kind not in ledger.RETURN_KINDS:
        kind = "退款"
    try:
        amount = float(f.get("amount") or 0)
    except ValueError:
        amount = 0.0
    if amount <= 0:
        return RedirectResponse(f"/orders/{oid}?rerr=1", status_code=303)
    reason = (f.get("reason") or "").strip() or None
    acct = (f.get("payment_account") or "").strip() or None
    cost = ((f.get("cost_account") or "").strip() or None) if kind == "退回小農" else None

    rid = execute("""INSERT INTO sales_return(order_id,return_date,season,kind,amount,payment_account,cost_account,reason)
                     VALUES(?,?,?,?,?,?,?,?)""", (oid, date, Q.season_of(date), kind, amount, acct, cost, reason))
    _post_return_ledger(rid, oid, date, kind, amount, acct, cost)
    return RedirectResponse(f"/orders/{oid}?rok=1", status_code=303)


def _return_delete(rid):
    """回傳被刪那筆退貨所屬的 order_id(找不到就 None)。"""
    r = q("SELECT order_id FROM sales_return WHERE return_id=?", (rid,))
    execute("DELETE FROM sales_return WHERE return_id=?", (rid,))
    ledger.delete_voucher_for("sales_return", rid)
    return r[0]["order_id"] if r else None

@app.post("/returns/{rid}/delete")
async def order_return_delete(request: Request, rid: int):
    oid = _return_delete(rid)
    back = f"/orders/{oid}" if oid else "/returns"
    f = await request.form()
    return RedirectResponse(f.get("next") or back, status_code=303)

@app.post("/orders/{oid}")
async def order_update(request: Request, oid: int):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    def flt(k):
        try: return float(f.get(k) or 0)
        except ValueError: return 0.0
    o, _ = Q.order_get(oid)
    if not o:
        return RedirectResponse("/orders", status_code=303)

    # --- 重建訂單明細 ---
    prods, qtys = f.getlist("product_id"), f.getlist("qty")
    ups, gl = f.getlist("unit_price"), f.getlist("is_gift")
    costs = f.getlist("line_cost")
    new_lines, new_costs = [], []
    for i, p in enumerate(prods):
        if not p:
            continue
        try:
            qv = float(qtys[i] or 0)
        except (ValueError, IndexError):
            continue
        if qv <= 0:
            continue
        try:
            up = float(ups[i]) if (i < len(ups) and str(ups[i]).strip()) else 0
        except (ValueError, IndexError):
            up = 0
        pid = int(p)
        is_gift = 1 if (i < len(gl) and gl[i] == "是") else 0
        new_lines.append((pid, qv, up if not is_gift else 0, None, is_gift))
        new_costs.append(_num(costs[i]) if i < len(costs) else 0)
    execute("DELETE FROM order_line WHERE order_id=?", (oid,))
    for (p, qv, up, b, gf), lc in zip(new_lines, new_costs):
        execute("""INSERT INTO order_line(order_id,product_id,batch_id,qty,unit_price,line_subtotal,is_gift,line_cost)
                   VALUES(?,?,?,?,?,?,?,?)""", (oid, p, b, qv, up, qv * up, gf, lc))

    subtotal = sum(qv * up for _, qv, up, _, gf in new_lines if not gf)
    disc = o["discount_total"] or 0  # 折扣欄位已不開放編輯,沿用原值(通常是 0)
    shipping_fee = flt("shipping_fee_charged")   # 跟客人收的運費,算進應收(2026-10-16)
    total = subtotal - disc + shipping_fee

    if f.get("invoiced") and not g("tax_doc_no"):
        return RedirectResponse(f"/orders/{oid}?err=invoice", status_code=303)
    sales_tax = flt("sales_tax") if (g("order_kind") or o["order_kind"]) == "銷售" else 0.0
    if sales_tax < 0 or (sales_tax and sales_tax >= total):
        return RedirectResponse(f"/orders/{oid}?err=tax", status_code=303)
    # 收款要一次收齊(2026-10-16):收款帳戶選了現金 / 銀行(C)就要有收款日,實收金額 = 應收合計;
    # 選 E 應收帳款 = 還沒收。2026-10-17 起不再有「收款狀態」欄位,狀態由這裡推出來。
    kind = g("order_kind") or o["order_kind"]
    paid = bool(g("payment_account")) and kind == "銷售"
    pay_status = "免收款" if kind != "銷售" else ("已收款" if paid else "待收款")
    if paid and not g("paid_date"):
        return RedirectResponse(f"/orders/{oid}?payerr=1", status_code=303)
    if g("ship_status") == "已出貨" and not (g("ship_method") and g("shipped_date")):
        return RedirectResponse(f"/orders/{oid}?shiperr=1", status_code=303)
    cust_id = f.get("customer_id")
    cols = dict(order_date=g("order_date"), order_kind=g("order_kind"),
                payment_status=pay_status,
                paid_date=(g("paid_date") if paid else None), paid_amount=(total if paid else None),
                payment_account=(g("payment_account") if paid else None),
                order_total=total, sales_tax=sales_tax,
                cost_payment_account=(g("cost_payment_account") or "現金"),
                ship_method=g("ship_method"), carrier=g("carrier"), tracking_no=g("tracking_no"),
                shipped_date=g("shipped_date"), delivered_date=g("delivered_date"),
                ship_status=g("ship_status") or "待出貨",
                shipping_fee_charged=shipping_fee, note=g("note"),
                invoiced=(1 if f.get("invoiced") else 0), tax_doc_no=g("tax_doc_no"))
    if cust_id and cust_id.isdigit():
        cols["customer_id"] = int(cust_id)
    if cols.get("order_date"):
        cols["season"] = Q.season_of(cols["order_date"])
    sets = ",".join(f'{k}=:{k}' for k in cols)
    execute(f'UPDATE "order" SET {sets} WHERE order_id=:id', {**cols, "id": oid})
    _post_order_ledger(oid)
    return RedirectResponse(f"/orders/{oid}?ok=1", status_code=303)

@app.post("/orders/{oid}/copy")
def order_copy(oid: int):
    o, lines = Q.order_get(oid)
    if not o:
        return RedirectResponse("/orders", status_code=303)
    noid = execute("""INSERT INTO "order"(order_no,order_date,season,customer_id,channel_id,order_kind,
                      order_total,payment_method,payment_status,ship_method,ship_status,note)
                      VALUES(?,?,?,?,?,?,?,?, '待收款', ?, '待出貨', ?)""",
                   (next_order_no(), dt.date.today().isoformat(), o["season"], o["customer_id"],
                    o["channel_id"], o["order_kind"], 0, o["payment_method"],
                    o["ship_method"], f"複製自 {o['order_no']}"))
    subtotal = 0
    for l in lines:
        subtotal += 0 if l["is_gift"] else (l["qty"] * l["unit_price"])
        execute("""INSERT INTO order_line(order_id,product_id,batch_id,qty,unit_price,list_price,line_subtotal,is_gift,line_cost)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (noid, l["product_id"], l["batch_id"], l["qty"], l["unit_price"],
                 l["list_price"], 0 if l["is_gift"] else l["qty"] * l["unit_price"], l["is_gift"],
                 l["line_cost"] or 0))
    ship = o["shipping_fee_charged"] or 0
    execute('UPDATE "order" SET order_total=?, sales_tax=?, shipping_fee_charged=?, cost_payment_account=? WHERE order_id=?',
            (subtotal + ship, o["sales_tax"] or 0, ship, o["cost_payment_account"], noid))
    _post_order_ledger(noid)
    return RedirectResponse(f"/orders/{noid}?ok=1", status_code=303)

@app.post("/orders/{oid}/delete")
def order_delete(oid: int):
    o = q('SELECT order_id FROM "order" WHERE order_id=?', (oid,))
    if not o:
        return RedirectResponse("/orders", status_code=303)
    execute("DELETE FROM shipment_issue WHERE order_id=?", (oid,))
    execute("UPDATE shipment_issue SET linked_reship_order_id=NULL WHERE linked_reship_order_id=?", (oid,))
    execute("UPDATE review_queue SET resolved_order_id=NULL WHERE resolved_order_id=?", (oid,))
    ledger.delete_voucher_for("order_sale", oid)
    ledger.delete_voucher_for("order_payment", oid)
    # sales_return 列本身靠 ON DELETE CASCADE 清,但那不會連動清總帳,要在 cascade 前先查出
    # return_id 把對應的傳票也清掉
    for r in q("SELECT return_id FROM sales_return WHERE order_id=?", (oid,)):
        ledger.delete_voucher_for("sales_return", r["return_id"])
    execute('DELETE FROM "order" WHERE order_id=?', (oid,))  # order_line / sales_return 靠 ON DELETE CASCADE 一起清掉
    return RedirectResponse("/orders?deleted=1", status_code=303)

@app.post("/orders/{oid}/paid")
async def order_mark_paid(request: Request, oid: int):
    f = await request.form()
    o, _ = Q.order_get(oid)
    if not o:
        return RedirectResponse("/orders", status_code=303)
    # 2026-10-16:收款一次收齊,實收 = 應收合計;收到哪個帳戶用選的(會計科目表 C 組)
    pdate = (f.get("paid_date") or "").strip() or dt.date.today().isoformat()
    acct = (f.get("payment_account") or "").strip() or "現金"
    execute("""UPDATE "order" SET payment_status='已收款', paid_date=?, paid_amount=order_total,
               payment_account=? WHERE order_id=?""", (pdate, acct, oid))
    _post_order_ledger(oid)
    return RedirectResponse(f"/orders/{oid}?done=paid", status_code=303)

@app.post("/orders/{oid}/shipped")
async def order_mark_shipped(request: Request, oid: int):
    f = await request.form()
    ship_method = (f.get("ship_method") or "").strip()
    shipped_date = (f.get("shipped_date") or "").strip() or dt.date.today().isoformat()
    if not ship_method:
        return RedirectResponse(f"/orders/{oid}?shiperr=1", status_code=303)
    execute("""UPDATE "order" SET ship_status='已出貨', ship_method=?,
               shipped_date=COALESCE(shipped_date, ?) WHERE order_id=?""",
            (ship_method, shipped_date, oid))
    return RedirectResponse(f"/orders/{oid}?done=shipped", status_code=303)


# ---------- 待確認 -------------------------------------------
@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request):
    return tpl.TemplateResponse("review.html", dict(
        request=request, active="review", rows=Q.review_list()))

@app.post("/review/{rid}")
def review_update(rid: int, status: str = Form(...)):
    execute("UPDATE review_queue SET status=? WHERE review_id=?", (status, rid))
    return RedirectResponse("/review", status_code=303)


# ---------- 物流異常 ----------------------------------------
RESOLUTIONS = ['重寄', '退款', '換貨', '折讓', '無']

@app.get("/issues", response_class=HTMLResponse)
def issues_page(request: Request):
    return tpl.TemplateResponse("issues.html", dict(
        request=request, active="issue", rows=Q.issues_list(), resolutions=RESOLUTIONS))

@app.post("/issues/{iid}")
async def issue_update(request: Request, iid: int):
    f = await request.form()
    si = q("SELECT * FROM shipment_issue WHERE issue_id=?", (iid,))
    if not si:
        return RedirectResponse("/issues", status_code=303)
    si = si[0]
    resolution = (f.get("resolution") or "").strip() or None
    execute("UPDATE shipment_issue SET resolution=?, reason_note=? WHERE issue_id=?",
            (resolution, (f.get("reason_note") or "").strip() or si["reason_note"], iid))
    if f.get("make_reship") == "1" and not si["linked_reship_order_id"]:
        o, lines = Q.order_get(si["order_id"])
        noid = execute("""INSERT INTO "order"(order_no,order_date,season,customer_id,channel_id,
                          order_kind,order_total,payment_method,payment_status,
                          ship_method,ship_status,note)
                          VALUES(?,?,?,?,?, '理賠重寄', 0, '未收款', '免收款',
                                 '自行配送', '待出貨', ?)""",
                       (next_order_no(), dt.date.today().isoformat(), o["season"], o["customer_id"],
                        o["channel_id"], f"由 {o['order_no']} 的{si['issue_type']}理賠重寄"))
        for l in lines:
            execute("""INSERT INTO order_line(order_id,product_id,batch_id,qty,unit_price,line_subtotal,is_gift,line_cost)
                       VALUES(?,?,?,?,0,0,1,0)""", (noid, l["product_id"], l["batch_id"], l["qty"]))
        execute("UPDATE shipment_issue SET linked_reship_order_id=? WHERE issue_id=?", (noid, iid))
        return RedirectResponse("/issues", status_code=303)


# ---------- 各產品線損益(v2 回合 6) ------------------------
PNL_BASIS = {"rev": "依營收", "qty": "依銷量", "dm": "依直接成本"}

@app.get("/lines", response_class=HTMLResponse)
def product_lines_pnl(request: Request):
    view, period, asof, ctx = _seasons_ctx(request)
    mode_kw = dict(mode=view, year=period) if view == "calendar" else dict(mode=view)
    season_arg = None if view == "calendar" else period
    basis = request.query_params.get("basis", "rev")
    if basis not in PNL_BASIS:
        basis = "rev"
    data = Q.product_line_pnl(season_arg, basis, **mode_kw)
    return tpl.TemplateResponse("lines.html", dict(
        request=request, active="lines", basis=basis, basis_opts=PNL_BASIS,
        d=data, **ctx))


# ---------- 財務健康:月損益 ----------------------------------
@app.get("/finance", response_class=HTMLResponse)
def finance_page(request: Request):
    view, period, asof, ctx = _seasons_ctx(request)
    mode_kw = dict(mode=view, year=period) if view == "calendar" else dict(mode=view)
    season_arg = None if view == "calendar" else period
    fin = []
    for ym in Q.finance_months(season_arg, **mode_kw):
        d = Q.finance_month(ym)
        fin.append(dict(ym=ym, revenue=d["revenue"], returns=d["returns"], cogs=d["cogs"],
                        gross_profit=d["gross_profit"], ship_cost=d["ship_cost"],
                        opex_total=d["opex_total"], pretax=d["pretax"],
                        opex={r["category"]: r["amt"] for r in d["opex"]}))
    fin_totals = ([Q.calendar_year_finance(y) for y in Q.calendar_years()] if view == "calendar"
                  else [Q.season_finance(s) for s in Q.seasons()])
    return tpl.TemplateResponse("finance.html", dict(
        request=request, active="finance",
        fin_json=json.dumps(fin), cats=Q.opex_cats(),
        seasons_fin=fin_totals, **ctx))

DOC_TYPES = ['發票', '收據']   # 憑證類型(1015 版 Excel:發票、收據;發票要填號碼)

# ---------- 營運支出(VD)/ 其他支出(VE)(2026-10-17 同仁修改意見:同一頁分模組區塊)--------
#   營運支出 = VD 費用 + C 付款帳戶 + G 進項稅額;其他支出 = VE + C + G,兩個是獨立的清單跟入口,
#   共用同一張 op_expense 表(用科目分組區分)。共用欄位(日期、備註)只填一次;憑證類型 / 發票號碼
#   放在 G 區塊。金額填「發票金額(支付金額)」= 含稅付出去的錢,費用 = 發票金額 − 進項稅額。
#   沒有攤提月數,也不再經過確認頁(分錄在同一頁即時預覽)。
EXP_KINDS = {"VD": dict(label="營運支出", list_url="/finance/expenses", active="expenses"),
             "VE": dict(label="其他支出", list_url="/other-expenses", active="otherexp")}


def _expense_list(request, grp):
    k = EXP_KINDS[grp]
    rows = q("""SELECT e.* FROM op_expense e
                WHERE COALESCE((SELECT grp FROM account a WHERE a.code=e.account_code), 'VD') = ?
                ORDER BY COALESCE(e.expense_date, e.ym || '-01') DESC, e.expense_id DESC""", (grp,))
    return tpl.TemplateResponse("finance_expenses.html", dict(
        request=request, active=k["active"], rows=rows, grp=grp, kind=k))

@app.get("/finance/expenses", response_class=HTMLResponse)
def finance_expenses(request: Request):
    return _expense_list(request, "VD")

@app.get("/other-expenses", response_class=HTMLResponse)
def other_expenses(request: Request):
    return _expense_list(request, "VE")

def _expense_form_ctx(request, e, grp="VD"):
    if e and e.get("account_code"):
        grp = q1("SELECT grp FROM account WHERE code=?", (e["account_code"],)).get("grp") or grp
    grp = grp if grp in EXP_KINDS else "VD"
    return dict(request=request, active=EXP_KINDS[grp]["active"], e=e, grp=grp, kind=EXP_KINDS[grp],
                accts=chart.accounts([grp]), cash_accounts=chart.accounts(["C"]), docs=DOC_TYPES,
                bank_codes={r["name"]: r["code"] for r in chart.accounts(["C"])},
                acct_names={r["code"]: r["name"] for r in chart.accounts([grp])},
                today=dt.date.today().isoformat())

@app.get("/finance/expenses/new", response_class=HTMLResponse)
def finance_expense_new(request: Request):
    return tpl.TemplateResponse("finance_expense_form.html",
                                _expense_form_ctx(request, None, request.query_params.get("grp") or "VD"))

@app.get("/other-expenses/new", response_class=HTMLResponse)
def other_expense_new(request: Request):
    return tpl.TemplateResponse("finance_expense_form.html", _expense_form_ctx(request, None, "VE"))

@app.get("/finance/expenses/{eid}/edit", response_class=HTMLResponse)
def finance_expense_edit(request: Request, eid: int):
    e = q("SELECT * FROM op_expense WHERE expense_id=?", (eid,))
    if not e:
        return RedirectResponse("/finance/expenses", status_code=303)
    return tpl.TemplateResponse("finance_expense_form.html", _expense_form_ctx(request, e[0]))


def _expense_delete(eid):
    execute("DELETE FROM op_expense WHERE expense_id=?", (int(eid),))
    ledger.delete_voucher_for("op_expense", int(eid))

@app.post("/finance/expenses")
async def finance_expense_save(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    fl = lambda k: _num(f.get(k))
    eid = f.get("expense_id")
    code = g("account_code")
    acct = q1("SELECT code, name, grp FROM account WHERE code=? AND grp IN ('VD','VE')", (code,))
    grp = acct.get("grp") or (f.get("grp") if f.get("grp") in EXP_KINDS else "VD")
    list_url = EXP_KINDS[grp]["list_url"]
    if f.get("_delete") and eid:
        _expense_delete(eid)
        return RedirectResponse(list_url, status_code=303)
    back = f"/finance/expenses/{eid}/edit" if eid else (
        "/other-expenses/new" if grp == "VE" else "/finance/expenses/new")
    date = g("expense_date")
    total = fl("invoice_amount")      # 發票金額(支付金額),含稅
    tax = fl("tax_amount")
    if not (date and acct) or total <= 0:
        return RedirectResponse(f"{back}?err=1", status_code=303)
    if tax >= total:
        return RedirectResponse(f"{back}?err=tax", status_code=303)
    doc = g("doc_type")
    if doc == "發票" and not g("invoice_no"):
        return RedirectResponse(f"{back}?ierr=1", status_code=303)
    cols = dict(expense_date=date, ym=date[:7], category=acct["name"], account_code=code,
                amount=total - tax, amortize_months=None, tax_amount=tax,
                payment_account=g("payment_account") or "現金", doc_type=doc,
                invoice_no=(g("invoice_no") if doc == "發票" else None), note=g("note"))
    if eid:
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE op_expense SET {sets}, updated_at=datetime('now','localtime') WHERE expense_id=:id",
                {**cols, "id": int(eid)})
        new_id = int(eid)
    else:
        keys = ",".join(cols)
        new_id = execute(f"INSERT INTO op_expense({keys}, updated_at) "
                         f"VALUES({','.join(':' + k for k in cols)}, datetime('now','localtime'))", cols)
    legs = ledger.compose_expense_entries(code, cols["amount"], tax, cols["payment_account"])
    # 2026-10-20(1019 同仁問題第 4 條):總帳備註要帶使用者填的備註,改備註總帳跟著改
    ledger.save_voucher("op_expense", new_id, date, legs,
                        note=f"{EXP_KINDS[grp]['label']}:{acct['name']}" + (f" {cols['note']}" if cols["note"] else ""))
    return RedirectResponse(f"{list_url}?ok=1", status_code=303)


# ---------- 9 宮格 B 類:首頁入口 ----------------------------
# 順序照同事 1015 版帳務設定表的交易類型(0921 修改意見第 5 條)
ENTRY_CARDS = [
    dict(title="銷售商品", href="/orders/new", desc="開一張訂單,存檔就自動記應收帳款、銷貨收入、銷項稅額,跟付給小農的成本。"),
    dict(title="營運支出", href="/finance/expenses/new", desc="房租、郵電、運費、薪資…營運相關的花費。"),
    dict(title="其他收益", href="/other-income/new", desc="利息收入、政府補助等非銷售的進帳。"),
    dict(title="其他支出", href="/other-expenses/new", desc="利息費用、所得稅這類不屬於營運的支出。"),
    dict(title="設備相關", href="/assets/new", desc="機器/器具採購,建固定資產卡,系統會自動記分錄、每月提折舊。"),
    dict(title="資本異動", href="/equity/new", desc="增資、減資、提列法定盈餘公積。"),
    dict(title="帳務調整", href="/adjustments/new", desc="上面都套不上時,手動指定借/貸科目記一筆。"),
]

@app.get("/entry", response_class=HTMLResponse)
def entry_page(request: Request):
    return tpl.TemplateResponse("entry.html", dict(request=request, active="entry", cards=ENTRY_CARDS))


# ---------- 9 宮格 B 類:其他收益 ----------------------------
OTHER_INCOME_CATS = list(ledger.OTHER_INCOME_ACCOUNTS.keys())   # 舊資料用;2026-10-16 起選 UB 科目

@app.get("/other-income", response_class=HTMLResponse)
def other_income_list(request: Request):
    rows = q("SELECT * FROM other_income ORDER BY income_date DESC, income_id DESC")
    return tpl.TemplateResponse("other_income_list.html", dict(
        request=request, active="other_income", rows=rows))

def _other_income_form_ctx(request, r):
    return dict(request=request, active="other_income", r=r, accts=chart.accounts(["UB"]),
                cash_accounts=chart.accounts(["C"]), docs=DOC_TYPES,
                bank_codes={x["name"]: x["code"] for x in chart.accounts(["C"])},
                acct_names={x["code"]: x["name"] for x in chart.accounts(["UB"])},
                today=dt.date.today().isoformat())

@app.get("/other-income/new", response_class=HTMLResponse)
def other_income_new(request: Request):
    return tpl.TemplateResponse("other_income_form.html", _other_income_form_ctx(request, None))

@app.get("/other-income/{iid}/edit", response_class=HTMLResponse)
def other_income_edit(request: Request, iid: int):
    r = q("SELECT * FROM other_income WHERE income_id=?", (iid,))
    if not r:
        return RedirectResponse("/other-income", status_code=303)
    return tpl.TemplateResponse("other_income_form.html", _other_income_form_ctx(request, r[0]))

def _other_income_delete(iid):
    execute("DELETE FROM other_income WHERE income_id=?", (int(iid),))
    ledger.delete_voucher_for("other_income", int(iid))

@app.post("/other-income")
async def other_income_save(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    fl = lambda k: float(f.get(k)) if (f.get(k) or "").strip() else 0.0
    iid = f.get("income_id")
    if f.get("_delete") and iid:
        _other_income_delete(iid)
        return RedirectResponse("/other-income", status_code=303)
    date = (f.get("income_date") or "").strip()
    code = g("account_code")
    acct = q1("SELECT code, name FROM account WHERE code=? AND grp='UB'", (code,))
    if not (date and acct):
        return RedirectResponse("/other-income", status_code=303)
    doc = g("doc_type")
    if doc == "發票" and not g("invoice_no"):
        back = f"/other-income/{iid}/edit" if iid else "/other-income/new"
        return RedirectResponse(f"{back}?ierr=1", status_code=303)
    cat = acct["name"]
    total = _num(f.get("invoice_amount"))   # 發票金額(實收金額),含稅(2026-10-17)
    tax = _num(f.get("tax_amount"))
    if total <= 0 or tax >= total:
        back = f"/other-income/{iid}/edit" if iid else "/other-income/new"
        return RedirectResponse(f"{back}?err=1", status_code=303)
    cols = dict(income_date=date, category=cat, account_code=code, amount=total - tax, tax_amount=tax,
                doc_type=doc, invoice_no=(g("invoice_no") if doc == "發票" else None),
                payment_account=g("payment_account"), note=g("note"))
    if iid:
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE other_income SET {sets} WHERE income_id=:id", {**cols, "id": int(iid)})
        new_id = int(iid)
    else:
        keys = ",".join(cols)
        new_id = execute(f"INSERT INTO other_income({keys}) VALUES({','.join(':' + k for k in cols)})", cols)
    legs = ledger.compose_other_income_entries(code, cols["amount"], cols["tax_amount"], cols["payment_account"])
    ledger.save_voucher("other_income", new_id, date, legs,
                        note=f"其他收益:{cat}" + (f" {cols['note']}" if cols["note"] else ""))
    return RedirectResponse("/other-income", status_code=303)


# ---------- 9 宮格 B 類:資本異動 ----------------------------
#   2026-10-17 同仁修改意見:先選「增減資」(C 現金銀行 + T 權益,選增加或減少)或
#   「提列法定盈餘公積」(T 從哪裡提 + T 轉入哪裡),都用下拉選科目。
@app.get("/equity", response_class=HTMLResponse)
def equity_list(request: Request):
    rows = q("SELECT * FROM equity_txn ORDER BY txn_date DESC, txn_id DESC")
    names = {r["code"]: r["name"] for r in chart.accounts(["T"], active_only=False)}
    return tpl.TemplateResponse("equity_list.html", dict(request=request, active="equity", rows=rows, names=names))

def _equity_ctx(request, r):
    t = chart.accounts(["T"], postable_only=True)
    return dict(request=request, active="equity", r=r, cash_accounts=chart.accounts(["C"]), t_accounts=t,
                t_names={x["code"]: x["name"] for x in t},
                bank_codes={x["name"]: x["code"] for x in chart.accounts(["C"])},
                today=dt.date.today().isoformat())

@app.get("/equity/new", response_class=HTMLResponse)
def equity_new(request: Request):
    return tpl.TemplateResponse("equity_form.html", _equity_ctx(request, None))

@app.get("/equity/{tid}/edit", response_class=HTMLResponse)
def equity_edit(request: Request, tid: int):
    r = q("SELECT * FROM equity_txn WHERE txn_id=?", (tid,))
    if not r:
        return RedirectResponse("/equity", status_code=303)
    return tpl.TemplateResponse("equity_form.html", _equity_ctx(request, r[0]))

def _equity_delete(tid):
    execute("DELETE FROM equity_txn WHERE txn_id=?", (int(tid),))
    ledger.delete_voucher_for("equity", int(tid))

@app.post("/equity")
async def equity_save(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    tid = f.get("txn_id")
    if f.get("_delete") and tid:
        _equity_delete(tid)
        return RedirectResponse("/equity", status_code=303)
    back = f"/equity/{tid}/edit" if tid else "/equity/new"
    date = g("txn_date")
    kind = g("kind")                              # 增減資 / 提列法定盈餘公積
    amount = _num(f.get("amount"))
    tcodes = {x["code"] for x in chart.accounts(["T"], postable_only=True)}
    if kind == "增減資":
        ttype = "減資" if f.get("direction") == "減少" else "增資"
        eq, src, pay = g("equity_account"), None, g("payment_account") or "現金"
        ok = eq in tcodes
    else:
        ttype = "提列法定盈餘公積"
        eq, src, pay = g("reserve_to"), g("reserve_from"), None
        ok = eq in tcodes and src in tcodes and eq != src
    if not (date and ok) or amount <= 0:
        return RedirectResponse(f"{back}?err=1", status_code=303)
    cols = dict(txn_date=date, txn_type=ttype, amount=amount, payment_account=pay,
                equity_account=eq, source_account=src, note=g("note"))
    if tid:
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE equity_txn SET {sets} WHERE txn_id=:id", {**cols, "id": int(tid)})
        new_id = int(tid)
    else:
        keys = ",".join(cols)
        new_id = execute(f"INSERT INTO equity_txn({keys}) VALUES({','.join(':' + k for k in cols)})", cols)
    legs = ledger.compose_equity_entries(ttype, amount, pay, eq, src)
    ledger.save_voucher("equity", new_id, date, legs,
                        note=f"資本異動:{ttype}" + (f" {cols['note']}" if cols["note"] else ""))
    return RedirectResponse("/equity?ok=1", status_code=303)


# ---------- 9 宮格 B 類:帳務調整(手動指定借/貸科目)---------
#   2026-10-17 同仁修改意見:一筆可以有好幾行(預設 3 行,至少填 2 行),每行先選模組(科目分組),
#   再選科目、借或貸、金額;借方合計 = 貸方合計才能存。
def _adjust_lines(mid):
    return q("SELECT * FROM manual_entry_line WHERE entry_id=? ORDER BY line_no", (mid,))

@app.get("/adjustments", response_class=HTMLResponse)
def adjustment_list(request: Request):
    rows = q("SELECT * FROM manual_entry ORDER BY entry_date DESC, entry_id DESC")
    for r in rows:
        r["lines"] = _adjust_lines(r["entry_id"])
    return tpl.TemplateResponse("adjustment_list.html", dict(request=request, active="adjust", rows=rows))

def _adjust_ctx(request, r):
    accts = chart.accounts(postable_only=True)
    used = [g for g, _ in chart.GROUPS if any(a["grp"] == g for a in accts)]
    lines = []
    if r:
        for x in _adjust_lines(r["entry_id"]):
            grp = next((a["grp"] for a in accts if a["code"] == x["account_code"]), "")
            side, amt = ("借", x["debit"]) if x["debit"] else ("貸", x["credit"])
            lines.append(dict(grp=grp, code=x["account_code"], side=side, amount=amt))
    # 2026-10-20(1019 同仁問題第 8 條):不限 2 或 3 項,預設 3 列,每列可刪、下面可再加
    while len(lines) < 3:
        lines.append(dict(grp="", code="", side="借" if not lines else "貸", amount=""))
    return dict(request=request, active="adjust", r=r, lines=lines,
                groups=[(g, chart.GROUP_LABEL[g]) for g in used],
                accts=[dict(code=a["code"], name=a["name"], grp=a["grp"]) for a in accts],
                today=dt.date.today().isoformat())

@app.get("/adjustments/new", response_class=HTMLResponse)
def adjustment_new(request: Request):
    return tpl.TemplateResponse("adjustment_form.html", _adjust_ctx(request, None))

@app.get("/adjustments/{mid}/edit", response_class=HTMLResponse)
def adjustment_edit(request: Request, mid: int):
    r = q("SELECT * FROM manual_entry WHERE entry_id=?", (mid,))
    if not r:
        return RedirectResponse("/adjustments", status_code=303)
    return tpl.TemplateResponse("adjustment_form.html", _adjust_ctx(request, r[0]))

def _adjustment_delete(mid):
    execute("DELETE FROM manual_entry_line WHERE entry_id=?", (int(mid),))
    execute("DELETE FROM manual_entry WHERE entry_id=?", (int(mid),))
    ledger.delete_voucher_for("manual_adjustment", int(mid))

@app.post("/adjustments")
async def adjustment_save(request: Request):
    f = await request.form()
    mid = f.get("entry_id")
    if f.get("_delete") and mid:
        _adjustment_delete(mid)
        return RedirectResponse("/adjustments", status_code=303)
    back = f"/adjustments/{mid}/edit" if mid else "/adjustments/new"
    date = (f.get("entry_date") or "").strip()
    valid = {a["code"] for a in chart.accounts(postable_only=True)}
    lines = []
    for code, side, amt in zip(f.getlist("line_code"), f.getlist("line_side"), f.getlist("line_amount")):
        amt = _num(amt)
        if not code and not amt:
            continue
        if code not in valid or amt <= 0:
            return RedirectResponse(f"{back}?err=line", status_code=303)
        lines.append((code, amt if side == "借" else 0, amt if side == "貸" else 0))
    legs = ledger.compose_manual_entries(lines)
    if not date or not legs:
        return RedirectResponse(f"{back}?err=bal", status_code=303)
    total = sum(l["debit"] for l in legs)
    note = (f.get("note") or "").strip() or None
    if mid:
        new_id = int(mid)
        execute("UPDATE manual_entry SET entry_date=?, amount=?, note=? WHERE entry_id=?", (date, total, note, new_id))
        execute("DELETE FROM manual_entry_line WHERE entry_id=?", (new_id,))
    else:
        new_id = execute("INSERT INTO manual_entry(entry_date, amount, note) VALUES(?,?,?)", (date, total, note))
    for i, l in enumerate(legs, start=1):
        execute("""INSERT INTO manual_entry_line(entry_id,line_no,account_code,account_name,debit,credit)
                   VALUES(?,?,?,?,?,?)""", (new_id, i, l["account_code"], l["account_name"], l["debit"], l["credit"]))
    ledger.save_voucher("manual_adjustment", new_id, date, legs, note=note)
    return RedirectResponse("/adjustments?ok=1", status_code=303)


# ---------- 總帳(進貨/營運費用/訂單/9宮格B類 自動過帳)-------
LEDGER_SOURCES = {"order_sale": "訂單成立(含付小農)", "order_payment": "訂單收款",
                  "op_expense": "營運支出 / 其他支出", "other_income": "其他收益", "equity": "資本異動",
                  "manual_adjustment": "帳務調整", "asset_acquire": "設備取得", "asset_depreciation": "折舊",
                  "sales_return": "退款 / 退回小農", "year_closing": "年度結轉",
                  "vat_settlement": "營業稅結算", "vat_payment": "營業稅繳納"}


def _ledger_explain(code, debit, credit):
    """解釋文字(2026-10-16 同仁 1001 回饋第 3 條):依科目方向用中文說明這一筆讓科目增加還是減少。
    借方正常的科目(資產 / 成本 / 費用)借方 = 增加、貸方 = 減少;貸方正常的(負債 / 權益 / 收入)相反。
    例:現金借 10 →「增加 $10」;收入貸 5 →「增加 $5」;現金貸 3 →「減少 $3」。"""
    amt = debit - credit
    if not amt:
        return ""
    up = (amt > 0) == Q._is_debit_normal(code)
    return f"{'增加' if up else '減少'} ${abs(amt):,.0f}"


def _ledger_invoice_map():
    """識別碼 = 發票號碼(2026-10-16 同仁確認)。依來源抓各自的發票號碼;沒有發票的留空。"""
    m = {}
    for r in q('SELECT order_id, tax_doc_no FROM "order" WHERE tax_doc_no IS NOT NULL AND tax_doc_no<>\'\''):
        m[("order_sale", r["order_id"])] = m[("order_payment", r["order_id"])] = r["tax_doc_no"]
    for st, sql in [("op_expense", "SELECT expense_id i, invoice_no v FROM op_expense"),
                    ("other_income", "SELECT income_id i, invoice_no v FROM other_income"),
                    ("goods_purchase", "SELECT purchase_id i, invoice_no v FROM goods_purchase")]:
        for r in q(sql + " WHERE invoice_no IS NOT NULL AND invoice_no<>''"):
            m[(st, r["i"])] = r["v"]
    return m


# ---------- 總帳(2026-10-16 照同事 1015 版「總帳」工作表)-------------------
#   一行一筆分錄:勾選 / 傳票號碼 / 交易日期 / 科目代碼 / 會計項目 / 識別碼(發票號碼)/
#   借方 / 貸方 / 異動金額(借 − 貸)/ 備註。預設依傳票號碼由小到大;點欄位標題排序、
#   表頭下一排篩選(前端 JS)。勾選一行會把同一張傳票的所有行一起勾,刪除一次刪整張。
def _ledger_rows(src):
    where, args = ["1=1"], []
    if src in LEDGER_SOURCES:
        where.append("source_type=?"); args.append(src)
    rows = q(f"""SELECT * FROM ledger_entry WHERE {' AND '.join(where)}
                 ORDER BY voucher_no, entry_id""", args)
    inv = _ledger_invoice_map()
    edit_cache = {}
    for r in rows:
        key = (r["source_type"], r["source_id"])
        r["invoice_no"] = inv.get(key, "")
        r["amount"] = (r["debit"] or 0) - (r["credit"] or 0)
        r["deletable"] = r["source_type"] in _LEDGER_DELETERS
        if key not in edit_cache:
            edit_cache[key] = _voucher_edit_url(*key)
        r["edit_url"] = edit_cache[key]
        r["src_label"] = LEDGER_SOURCES.get(r["source_type"], r["source_type"])
        r["explain"] = _ledger_explain(r["account_code"], r["debit"] or 0, r["credit"] or 0)
    return rows


LEDGER_CSV_HEAD = ["傳票號碼", "交易日期", "科目代碼", "會計項目", "識別碼", "借方", "貸方", "異動金額", "解釋", "備註"]

@app.get("/ledger.csv")
def ledger_csv(request: Request):
    """總帳下載(1019 同仁問題第 7 條):跟畫面同欄位,依傳票號碼排。"""
    src = request.query_params.get("source") or ""
    rows = [[r["voucher_no"], r["entry_date"], r["account_code"], r["account_name"], r["invoice_no"],
             round(r["debit"] or 0), round(r["credit"] or 0), round(r["amount"]), r["explain"], r["note"] or ""]
            for r in _ledger_rows(src)]
    return csv_response(f"總帳_{dt.date.today().isoformat()}.csv", LEDGER_CSV_HEAD, rows)

@app.get("/ledger", response_class=HTMLResponse)
def ledger_page(request: Request):
    if request.query_params.get("tab") == "trial":
        year, years = _trial_year(request)
        closed = bool(q("SELECT 1 FROM ledger_entry WHERE source_type='year_closing' AND source_id=? LIMIT 1", (year,)))
        return tpl.TemplateResponse("statements.html", dict(
            request=request, active="ledger", book_tab="trial", years=years, year=year, closed=closed,
            tb=Q.trial_balance(year)))
    src = request.query_params.get("source") or ""
    rows = _ledger_rows(src)
    accts = sorted({(r["account_code"], r["account_name"]) for r in rows})
    return tpl.TemplateResponse("ledger.html", dict(book_tab="gl",
        request=request, active="ledger", rows=rows, src=src, sources=LEDGER_SOURCES, accts=accts,
        total_debit=sum(r["debit"] or 0 for r in rows), total_credit=sum(r["credit"] or 0 for r in rows)))


def _voucher_edit_url(source_type, source_id):
    """傳票的「編輯」= 回到產生它的那筆原始資料改,改完系統會自動重開傳票。"""
    if source_type == "sales_return":
        r = q1("SELECT order_id FROM sales_return WHERE return_id=?", (source_id,))
        return f"/orders/{r['order_id']}" if r else None
    return {
        "op_expense": f"/finance/expenses/{source_id}/edit",
        "order_sale": f"/orders/{source_id}",
        "order_payment": f"/orders/{source_id}",
        "goods_purchase": None,
        "other_income": f"/other-income/{source_id}/edit",
        "equity": f"/equity/{source_id}/edit",
        "manual_adjustment": f"/adjustments/{source_id}/edit",
        "asset_acquire": f"/assets/{source_id}/edit",
        "asset_depreciation": f"/assets/{source_id}/edit",
        "year_closing": "/ledger?tab=trial",
        "vat_settlement": "/vat", "vat_payment": "/vat",
    }.get(source_type)


# ---------- 會計科目設定(2026-10-16,依同事 1015 版帳務設定表)-------------
#   科目存在 account 表(初值見 chart.DEFAULT_CHART)。可以新增、改名(連總帳已記過的名稱
#   一起改)、停用(下拉選單不再出現,舊傳票照常顯示)、刪除(只限從沒記過帳的科目)。
@app.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request):
    used = {r["account_code"]: r["n"] for r in
            q("SELECT account_code, COUNT(*) n FROM ledger_entry GROUP BY account_code")}
    rows = chart.accounts(active_only=False)
    groups = [dict(key=k, label=lbl, rows=[dict(r, used=used.get(r["code"], 0)) for r in rows if r["grp"] == k])
              for k, lbl in chart.GROUPS]
    return tpl.TemplateResponse("accounts.html", dict(
        request=request, active="accounts", groups=groups, computed=chart.COMPUTED_CODES))

@app.post("/accounts")
async def account_add(request: Request):
    f = await request.form()
    grp = (f.get("grp") or "").strip()
    code = (f.get("code") or "").strip()
    name = (f.get("name") or "").strip()
    if grp not in chart.GROUP_LABEL or not code or not name:
        return RedirectResponse("/accounts?err=missing", status_code=303)
    if q1("SELECT 1 FROM account WHERE code=?", (code,)):
        return RedirectResponse(f"/accounts?err=dup&code={code}", status_code=303)
    sort = (q1("SELECT MAX(sort) m FROM account").get("m") or 0) + 1
    execute("INSERT INTO account(code,name,grp,sort) VALUES(?,?,?,?)", (code, name, grp, sort))
    return RedirectResponse(f"/accounts?added={code}#g-{grp}", status_code=303)

@app.post("/accounts/{code}/rename")
async def account_rename(request: Request, code: str):
    f = await request.form()
    name = (f.get("name") or "").strip()
    row = q1("SELECT grp FROM account WHERE code=?", (code,))
    if row and name:
        if row["grp"] == "C" and q1("SELECT 1 FROM account WHERE grp='C' AND name=? AND code!=?", (name, code)):
            return RedirectResponse("/accounts?err=bankdup#g-C", status_code=303)
        execute("UPDATE account SET name=? WHERE code=?", (name, code))
        execute("UPDATE ledger_entry SET account_name=? WHERE account_code=?", (name, code))
    return RedirectResponse(f"/accounts?renamed={code}#g-{row.get('grp', '')}", status_code=303)

@app.post("/accounts/{code}/toggle")
def account_toggle(code: str):
    row = q1("SELECT grp FROM account WHERE code=?", (code,))
    execute("UPDATE account SET active=1-active WHERE code=?", (code,))
    return RedirectResponse(f"/accounts#g-{row.get('grp', '')}", status_code=303)

@app.post("/accounts/{code}/delete")
def account_delete(code: str):
    row = q1("SELECT grp FROM account WHERE code=?", (code,))
    if q1("SELECT 1 FROM ledger_entry WHERE account_code=? LIMIT 1", (code,)):
        return RedirectResponse(f"/accounts?err=used&code={code}#g-{row.get('grp', '')}", status_code=303)
    execute("DELETE FROM account WHERE code=?", (code,))
    return RedirectResponse(f"/accounts?deleted={code}#g-{row.get('grp', '')}", status_code=303)


# ---------- 銀行帳戶(2026-10-16 起是會計科目表 C 組的 1103-NN)-------
#   付款帳戶欄位打一個新名字,存檔時 chart.cash_account() 會自動接著編號新增。這頁給
#   同事 / 家易看清單、改名字、把同一家銀行打成兩個名字的帳戶併成一個。
@app.get("/bank-accounts", response_class=HTMLResponse)
def bank_accounts_page(request: Request):
    return tpl.TemplateResponse("bank_accounts.html", dict(
        request=request, active="bankacct", rows=chart.bank_accounts(active_only=False)))

@app.post("/bank-accounts")
async def bank_account_save(request: Request):
    """改名:連已經記過的舊傳票 account_name 一起回填。"""
    f = await request.form()
    code = (f.get("code") or "").strip()
    name = (f.get("name") or "").strip()
    row = q1("SELECT name FROM account WHERE code=? AND grp='C'", (code,))
    if row and name and row["name"] != name:
        if q1("SELECT 1 FROM account WHERE grp='C' AND name=? AND code!=?", (name, code)):
            return RedirectResponse("/bank-accounts?err=dup", status_code=303)
        execute("UPDATE account SET name=? WHERE code=?", (name, code))
        execute("UPDATE ledger_entry SET account_name=? WHERE account_code=?", (name, code))
    return RedirectResponse("/bank-accounts?renamed=1", status_code=303)

@app.post("/bank-accounts/merge")
async def bank_account_merge(request: Request):
    """合併:把來源帳戶底下所有傳票的科目代碼/名稱改成目標帳戶的,再刪掉來源科目。
    chart.cash_account() 用目前最大號 +1 發號,刪掉的號碼如果剛好是最大號會被重新使用,
    但那時候它底下已經沒有任何傳票,不會混帳。"""
    f = await request.form()
    src_code, dst_code = f.get("from_code"), f.get("to_code")
    if not (src_code and dst_code) or src_code == dst_code:
        return RedirectResponse("/bank-accounts?err=1", status_code=303)
    src = q1("SELECT * FROM account WHERE code=? AND grp='C'", (src_code,))
    dst = q1("SELECT * FROM account WHERE code=? AND grp='C'", (dst_code,))
    if not (src and dst) or "1102" in (src_code, dst_code):
        return RedirectResponse("/bank-accounts?err=1", status_code=303)
    execute("UPDATE ledger_entry SET account_code=?, account_name=? WHERE account_code=?",
            (dst["code"], dst["name"], src["code"]))
    execute("DELETE FROM account WHERE code=?", (src["code"],))
    return RedirectResponse(f"/bank-accounts?merged={src['name']}", status_code=303)


# ---------- 正式三表:試算表 / 資產負債表 / 綜合損益表(2026-09-23)------
#   給同事做帳/報稅用,直接從總帳彙總,用西曆年切期間(跟系統其他地方的「產季」不是
#   同一條時間軸)。詳見 ledger.compose_year_closing_entries / queries.trial_balance 等註解。
# 2026-10-20(1019 同仁問題第 7 條):總帳跟試算表合併成一頁(總帳頁的兩個分頁),拿掉資產負債表 /
# 綜合損益表;舊的 /statements 網址轉到試算表分頁。
def _trial_year(request):
    years = Q.statement_years()
    this_year = dt.date.today().year          # 1019 同仁問題第 9 條:預設今年
    years = sorted(set(years) | {this_year})
    yv = request.query_params.get("year")
    return (int(yv) if yv and yv.isdigit() and int(yv) in years else this_year), years

@app.get("/statements")
def statements_redirect(request: Request):
    yv = request.query_params.get("year")
    return RedirectResponse("/ledger?tab=trial" + (f"&year={yv}" if yv else ""), status_code=303)

@app.get("/statements/trial.csv")
def trial_balance_csv(request: Request):
    """試算表下載:跟畫面一樣(期初、本期借貸、期末,借正貸負)。"""
    year, _ = _trial_year(request)
    tb = Q.trial_balance(year)
    rows = [[r["account_code"], r["account_name"] + ("(算出來的,不計入檢核)" if r["synthetic"] else ""),
             "" if r["synthetic"] else round(r["open_bal"]), "" if r["synthetic"] else round(r["dr"]),
             "" if r["synthetic"] else round(r["cr"]), round(r["close_bal"])] for r in tb["rows"]]
    rows += [[], ["", "合計(不含本期損益)", round(tb["total_open"]), round(tb["total_dr"]), round(tb["total_cr"]),
                  round(tb["check"])], ["", "檢核:期末餘額加總(應該 = 0)", "", "", "", round(tb["check"])]]
    return csv_response(f"試算表_{year}年.csv", ["科目代碼", "科目名稱", "期初餘額", "本期借方", "本期貸方", "期末餘額"], rows)

@app.post("/statements/close-year")
async def statements_close_year(request: Request):
    f = await request.form()
    year = int(f.get("year"))
    if not q("SELECT 1 FROM ledger_entry WHERE source_type='year_closing' AND source_id=?", (year,)):
        legs = ledger.compose_year_closing_entries(year)
        ledger.save_voucher("year_closing", year, f"{year}-12-31", legs, note=f"{year} 年度結轉")
    return RedirectResponse(f"/ledger?tab=trial&year={year}", status_code=303)

@app.post("/statements/reopen-year")
async def statements_reopen_year(request: Request):
    f = await request.form()
    year = int(f.get("year"))
    ledger.delete_voucher_for("year_closing", year)
    return RedirectResponse(f"/ledger?tab=trial&year={year}", status_code=303)


# ---------- 營業稅結算(2026-10-20 同仁回覆待同仁確認第 6 題)------------------
#   每兩個月一期。結算看「期末那天為止」的 2214 / 1423 / 1424 餘額(之前每期結算都會把 2214、1423
#   歸零,所以餘額就是這期還沒結的部分)。只能照順序結:比最後一期已結算的還早的期別不能再結;
#   撤銷只能撤最後一期、而且還沒繳納的。
import calendar as _cal

def _vat_range(year, p):
    m1, m2 = 2 * p - 1, 2 * p
    return f"{year}-{m1:02d}-01", f"{year}-{m2:02d}-{_cal.monthrange(year, m2)[1]:02d}"

def _vat_balances(as_of):
    def bal(code):
        r = q1("SELECT COALESCE(SUM(debit),0)-COALESCE(SUM(credit),0) b FROM ledger_entry WHERE account_code=? AND entry_date<=?",
               (code, as_of))
        return round(r.get("b") or 0, 2)
    return dict(output_tax=-bal("2214"), input_tax=bal("1423"), credit_before=bal("1424"))

def _vat_latest():
    return q1("SELECT * FROM vat_period ORDER BY year DESC, period DESC LIMIT 1") or None

@app.get("/vat", response_class=HTMLResponse)
def vat_page(request: Request):
    today = dt.date.today().isoformat()
    years = sorted(set(Q.statement_years()) | {dt.date.today().year})
    yv = request.query_params.get("year")
    year = int(yv) if yv and yv.isdigit() else dt.date.today().year
    done = {r["period"]: r for r in q("SELECT * FROM vat_period WHERE year=?", (year,))}
    latest = _vat_latest()
    rows = []
    for p in range(1, 7):
        lo, hi = _vat_range(year, p)
        r = dict(period=p, label=f"{2 * p - 1}–{2 * p} 月", lo=lo, hi=hi)
        if p in done:
            r.update(done[p], settled=True)
            r["can_reopen"] = latest and latest["period_id"] == done[p]["period_id"] and not done[p]["paid_date"]
        else:
            b = _vat_balances(hi)
            _, payable, credit_after = ledger.compose_vat_settlement_entries(**b)
            r.update(b, payable=payable, credit_after=credit_after, settled=False,
                     future=lo > today,
                     blocked=bool(latest and (latest["year"], latest["period"]) > (year, p)))
        rows.append(r)
    return tpl.TemplateResponse("vat.html", dict(
        request=request, active="ledger", year=year, years=years, rows=rows,
        cash_accounts=chart.accounts(["C"]), today=today))

@app.post("/vat/settle")
async def vat_settle(request: Request):
    f = await request.form()
    year, p = int(f.get("year")), int(f.get("period"))
    latest = _vat_latest()
    if latest and (latest["year"], latest["period"]) >= (year, p):
        return RedirectResponse(f"/vat?year={year}&err=order", status_code=303)
    lo, hi = _vat_range(year, p)
    b = _vat_balances(hi)
    legs, payable, credit_after = ledger.compose_vat_settlement_entries(**b)
    pid = execute("""INSERT INTO vat_period(year,period,settle_date,output_tax,input_tax,credit_before,payable,credit_after)
                     VALUES(?,?,?,?,?,?,?,?)""",
                  (year, p, hi, b["output_tax"], b["input_tax"], b["credit_before"], payable, credit_after))
    ledger.save_voucher("vat_settlement", pid, hi, legs, note=f"營業稅結算 {year} 年 {2 * p - 1}–{2 * p} 月")
    return RedirectResponse(f"/vat?year={year}&ok=settled", status_code=303)

@app.post("/vat/{pid}/reopen")
async def vat_reopen(pid: int):
    r = q1("SELECT * FROM vat_period WHERE period_id=?", (pid,))
    latest = _vat_latest()
    if r and latest and latest["period_id"] == pid and not r["paid_date"]:
        ledger.delete_voucher_for("vat_settlement", pid)
        execute("DELETE FROM vat_period WHERE period_id=?", (pid,))
    return RedirectResponse(f"/vat?year={r['year'] if r else ''}", status_code=303)

@app.post("/vat/{pid}/pay")
async def vat_pay(request: Request, pid: int):
    f = await request.form()
    r = q1("SELECT * FROM vat_period WHERE period_id=?", (pid,))
    if not r or r["payable"] <= 0:
        return RedirectResponse("/vat", status_code=303)
    date = (f.get("paid_date") or "").strip() or dt.date.today().isoformat()
    acct = (f.get("paid_account") or "").strip() or "現金"
    execute("UPDATE vat_period SET paid_date=?, paid_account=? WHERE period_id=?", (date, acct, pid))
    ledger.save_voucher("vat_payment", pid, date, ledger.compose_vat_payment_entries(r["payable"], acct),
                        note=f"繳納營業稅 {r['year']} 年 {2 * r['period'] - 1}–{2 * r['period']} 月")
    return RedirectResponse(f"/vat?year={r['year']}&ok=paid", status_code=303)

@app.post("/vat/{pid}/unpay")
async def vat_unpay(pid: int):
    r = q1("SELECT * FROM vat_period WHERE period_id=?", (pid,))
    if r:
        ledger.delete_voucher_for("vat_payment", pid)
        execute("UPDATE vat_period SET paid_date=NULL, paid_account=NULL WHERE period_id=?", (pid,))
    return RedirectResponse(f"/vat?year={r['year'] if r else ''}", status_code=303)


# ---------- 銀行帳戶明細(現金 / 各銀行帳戶,依科目篩選總帳)---
@app.get("/cash", response_class=HTMLResponse)
def cash_page(request: Request):
    accounts = Q.cash_accounts()
    sel = request.query_params.get("account") or ""
    if not sel and accounts:
        a = accounts[0]
        sel = f"{a['account_code']}|{a['account_name']}"
    code, _, name = sel.partition("|")
    rows, bal = (Q.cash_ledger(code, name) if code else ([], 0.0))
    return tpl.TemplateResponse("cash.html", dict(
        request=request, active="cash", accounts=accounts, sel=sel, rows=rows, bal=bal))


# ---------- 科目明細(2026-10-16,照 1015 版各明細工作表的「產出報表樣式」)---------
#   選一個科目,列出它的每一筆交易 + 累計餘額。銀行帳戶、應收帳款、進銷項稅額、採購成本、
#   業外收益…都可以從這頁看(對應 Excel 的 C / E / G、P / VA / UB 那幾張表)。
@app.get("/account-ledger", response_class=HTMLResponse)
def account_ledger_page(request: Request):
    code = request.query_params.get("code") or "1102"
    lo = request.query_params.get("from") or ""
    hi = request.query_params.get("to") or ""
    acct = q1("SELECT code, name, grp FROM account WHERE code=?", (code,)) or \
        dict(code=code, name=q1("SELECT account_name n FROM ledger_entry WHERE account_code=? LIMIT 1",
                                (code,)).get("n") or code, grp="")
    d = Q.account_ledger(code, lo or None, hi or None)
    inv = _ledger_invoice_map()
    for r in d["rows"]:
        r["invoice_no"] = inv.get((r["source_type"], r["source_id"]), "")
        r["src_label"] = LEDGER_SOURCES.get(r["source_type"], r["source_type"])
    used = {r["account_code"] for r in q("SELECT DISTINCT account_code FROM ledger_entry")}
    groups = [dict(key=k, label=lbl, accts=[a for a in chart.accounts([k], active_only=False)
                                              if a["code"] not in chart.COMPUTED_CODES])
              for k, lbl in chart.GROUPS]
    return tpl.TemplateResponse("account_ledger.html", dict(
        request=request, active="acctledger", acct=acct, d=d, lo=lo, hi=hi,
        groups=[g for g in groups if g["accts"]], used=used))


# ---------- 應收帳款(2026-10-16,照 1015 版「E 應收帳款」工作表)-----------------
#   尚未收回 / 已收回兩區。勾選未收的帳款 → 跳出視窗選收款帳戶(C)→ 變成已收款,
#   系統自動記「借 現金或銀行 / 貸 應收帳款」(收款一次收齊,金額 = 應收合計)。
def _receivables():
    base = """SELECT o.order_id, o.order_no, o.order_date, o.order_total, o.paid_date, o.paid_amount,
                     o.payment_account, o.note, cu.cust_no, cu.display_name cust
              FROM "order" o LEFT JOIN customer cu ON cu.customer_id=o.customer_id
              WHERE o.order_kind='銷售' AND o.order_total>0 AND o.payment_status=?
              ORDER BY o.order_date {}, o.order_id {}"""
    return q(base.format("", ""), ("待收款",)), q(base.format("DESC", "DESC"), ("已收款",))

@app.get("/receivables", response_class=HTMLResponse)
def receivables_page(request: Request):
    unpaid, paid = _receivables()
    return tpl.TemplateResponse("receivables.html", dict(
        request=request, active="receivables", unpaid=unpaid, paid=paid,
        unpaid_total=sum(r["order_total"] or 0 for r in unpaid),
        cash_accounts=chart.accounts(["C"]), today=dt.date.today().isoformat()))

@app.post("/receivables/collect")
async def receivables_collect(request: Request):
    f = await request.form()
    pdate = (f.get("paid_date") or "").strip() or dt.date.today().isoformat()
    acct = (f.get("payment_account") or "").strip() or "現金"
    n = 0
    for v in dict.fromkeys(f.getlist("order_id")):
        if not str(v).isdigit():
            continue
        oid = int(v)
        if not q1("""SELECT 1 FROM "order" WHERE order_id=? AND payment_status='待收款'""", (oid,)):
            continue
        execute("""UPDATE "order" SET payment_status='已收款', paid_date=?, paid_amount=order_total,
                   payment_account=? WHERE order_id=?""", (pdate, acct, oid))
        _post_order_ledger(oid)
        n += 1
    return RedirectResponse(f"/receivables?collected={n}", status_code=303)


# ---------- 定價試算(管理估算,不進帳本) --------------------
PRICING_KEYS = {k for k, *_ in Q.PRICING_FIELDS}

@app.get("/pricing", response_class=HTMLResponse)
def pricing_page(request: Request):
    p = Q.pricing_params()
    grouped = []
    for grp in Q.PRICING_GROUPS:
        items = [dict(key=k, label=lab, unit=u, hint=h, value=p[k])
                 for k, g, lab, u, d, h in Q.PRICING_FIELDS if g == grp]
        grouped.append((grp, items))
    return tpl.TemplateResponse("pricing.html", dict(
        request=request, active="pricing", grouped=grouped,
        params_json=json.dumps(p)))

@app.post("/pricing")
async def pricing_save(request: Request):
    f = await request.form()
    if f.get("_reset"):
        execute("DELETE FROM pricing_param")
        return RedirectResponse("/pricing", status_code=303)
    for k in PRICING_KEYS:
        v = (f.get(f"p_{k}") or "").strip()
        if v == "":
            continue
        try:
            val = float(v)
        except ValueError:
            continue
        execute("INSERT INTO pricing_param(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, val))
    return RedirectResponse("/pricing", status_code=303)


# ---------- 產季目標 ------------------------------------------
@app.get("/targets", response_class=HTMLResponse)
def targets_page(request: Request):
    ss = Q.seasons()
    show = list(ss)
    if not show:
        show = [dt.date.today().year]
    if show[-1] + 1 not in show:
        show.append(show[-1] + 1)      # 讓使用者可先設定下一個產季
    rows = []
    for s in show:
        f = Q.season_finance(s)
        rows.append(dict(
            season=s, window=f["window"],
            target=Q.sales_target_get(s), revenue=f["revenue"],
            pretax=f["pretax"], margin=f["margin"]))
    return tpl.TemplateResponse("targets.html", dict(
        request=request, active="target", rows=rows))

@app.post("/targets")
async def targets_save(request: Request):
    f = await request.form()
    try:
        season = int(f.get("season"))
    except (TypeError, ValueError):
        return RedirectResponse("/targets", status_code=303)
    try:
        amt = float(f.get("target_amount") or 0)
    except ValueError:
        amt = 0.0
    Q.sales_target_set(season, amt)
    return RedirectResponse("/targets", status_code=303)


# ---------- 進口商品:2026-10-16 改成在訂單成立時一起付給小農,/goods-purchases 頁面拿掉 ----
def _goods_purchase_delete(pid):
    """舊資料用:進口商品表留著(dormant),總帳勾選刪除時還認得。"""
    execute("DELETE FROM goods_purchase WHERE purchase_id=?", (pid,))
    ledger.delete_voucher_for("goods_purchase", pid)


# 傳票勾選刪除:連來源紀錄一起刪(重用各來源自己的刪除邏輯,不繞過既有規則)
_LEDGER_DELETERS = {
    "op_expense": _expense_delete,
    "order_sale": order_delete,
    "order_payment": order_delete,
    "goods_purchase": _goods_purchase_delete,
    "other_income": _other_income_delete,
    "equity": _equity_delete,
    "manual_adjustment": _adjustment_delete,
    "sales_return": _return_delete,
}

# 清單勾選刪除(2026-10-17,0921 修改意見第 17 條「跟總帳一樣用勾選的方式刪」):
# 訂單 / 營運支出 / 其他支出 / 其他收益 / 資本異動 / 帳務調整 / 退款清單都可以勾好幾筆一次刪,
# 一樣連總帳的傳票一起刪(重用各自的刪除邏輯)。
_BULK_DELETERS = {
    "order": order_delete, "op_expense": _expense_delete, "other_income": _other_income_delete,
    "equity": _equity_delete, "manual_adjustment": _adjustment_delete, "sales_return": _return_delete,
}

@app.post("/bulk-delete")
async def bulk_delete(request: Request):
    f = await request.form()
    kind, back = f.get("kind"), f.get("back") or "/"
    if not back.startswith("/") or back.startswith("//"):
        back = "/"
    n = 0
    if kind in _BULK_DELETERS:
        for i in dict.fromkeys(x for x in f.getlist("id") if str(x).isdigit()):
            _BULK_DELETERS[kind](int(i))
            n += 1
    sep = "&" if "?" in back else "?"
    return RedirectResponse(f"{back}{sep}bulkdel={n}", status_code=303)


@app.post("/ledger/delete")
async def ledger_delete(request: Request):
    """勾選刪除(1015 版「總帳」工作表:只能一次刪除同一筆傳票號碼的全部,不可分開刪其中某一筆)。
    收傳票號碼清單,每張找出來源、呼叫該來源自己的刪除邏輯(連來源資料一起刪)。
    設備取得 / 折舊 / 年度結轉不能從這裡刪(要到固定資產 / 財務報表處理),會略過。"""
    f = await request.form()
    done, deleted, skipped = set(), [], []
    for vno in dict.fromkeys(v.strip() for v in f.getlist("voucher_no") if v.strip()):
        r = q1("SELECT source_type, source_id FROM ledger_entry WHERE voucher_no=? LIMIT 1", (vno,))
        if not r:
            continue
        key = (r["source_type"], r["source_id"])
        if r["source_type"] not in _LEDGER_DELETERS:
            skipped.append(vno); continue
        if key not in done:
            _LEDGER_DELETERS[r["source_type"]](int(r["source_id"]))
            done.add(key)
        deleted.append(vno)
    from urllib.parse import quote
    return RedirectResponse(f"/ledger?deleted={quote(','.join(deleted))}&skipped={quote(','.join(skipped))}",
                            status_code=303)


# ---------- 出貨作業:揀貨單 / 標籤 / 食品標示 ---------------
SENDER = {"name": "郡碩農創", "place": "南投縣中寮鄉", "addr": "[寄件地址]", "phone": "[寄件電話]"}

@app.get("/shipping", response_class=HTMLResponse)
def shipping_hub(request: Request):
    pending = q("SELECT COUNT(*) n FROM \"order\" WHERE ship_status='待出貨' AND order_kind<>'內部領用'")[0]["n"]
    dates = [r["d"] for r in q("""SELECT DISTINCT order_date d FROM "order"
                                  WHERE ship_status='待出貨' ORDER BY d DESC LIMIT 20""")]
    return tpl.TemplateResponse("shipping.html", dict(
        request=request, active="ship", pending=pending, dates=dates,
        products=q("SELECT product_id,sku,name FROM product ORDER BY sku")))

def _ship_scope(request):
    d = request.query_params.get("date")
    return ("date", d) if d else ("pending", None)

@app.get("/shipping/picklist", response_class=HTMLResponse)
def shipping_picklist(request: Request):
    scope, date = _ship_scope(request)
    orders, summary = Q.shipping_orders(scope, date)
    return tpl.TemplateResponse("picklist.html", dict(
        request=request, active="ship", orders=orders, summary=summary,
        scope=scope, date=date, today=dt.date.today().isoformat()))

@app.get("/shipping/labels", response_class=HTMLResponse)
def shipping_labels(request: Request):
    scope, date = _ship_scope(request)
    orders, _ = Q.shipping_orders(scope, date)
    return tpl.TemplateResponse("shiplabels.html", dict(
        request=request, active="ship", orders=orders, scope=scope, date=date, sender=SENDER))

@app.get("/shipping/foodlabel", response_class=HTMLResponse)
def shipping_foodlabel(request: Request):
    pid = request.query_params.get("product_id")
    try:
        copies = max(1, min(60, int(request.query_params.get("copies", 8))))
    except ValueError:
        copies = 8
    data = Q.food_label(int(pid)) if pid else None
    return tpl.TemplateResponse("foodlabel.html", dict(
        request=request, active="ship", data=data, copies=copies, sender=SENDER,
        products=q("SELECT product_id,sku,name FROM product ORDER BY sku"),
        pid=pid or ""))


# ---------- 下載手冊(核心手冊即時轉成自包含 HTML)-------------
@app.get("/manuals", response_class=HTMLResponse)
def manuals_page(request: Request):
    items = [dict(key=k, title=t, desc=d) for k, (_, t, d) in ME.CORE_MANUALS.items()]
    return tpl.TemplateResponse("manuals.html", dict(request=request, active="manuals", items=items))

@app.get("/manuals/{key}/download")
def manuals_download(key: str):
    if key not in ME.CORE_MANUALS:
        return RedirectResponse("/manuals", status_code=303)
    from urllib.parse import quote
    title, content = ME.export_html(key)
    cd = f"attachment; filename=manual.html; filename*=UTF-8''{quote(title)}.html"
    return Response(content, media_type="text/html; charset=utf-8", headers={"Content-Disposition": cd})
