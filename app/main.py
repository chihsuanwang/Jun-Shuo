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
    try: return f"{v:,.0f}"
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
        execute("""CREATE TABLE IF NOT EXISTS sales_return (
          return_id    INTEGER PRIMARY KEY,
          order_id     INTEGER NOT NULL REFERENCES "order"(order_id) ON DELETE CASCADE,
          return_date  TEXT NOT NULL,
          season       INTEGER NOT NULL,
          kind         TEXT NOT NULL DEFAULT '退貨' CHECK (kind IN ('退貨','折讓')),
          amount       REAL NOT NULL DEFAULT 0,
          product_id   INTEGER REFERENCES product(product_id),
          qty          REAL,
          batch_id     INTEGER REFERENCES batch(batch_id),
          restock      INTEGER NOT NULL DEFAULT 0 CHECK (restock IN (0,1)),
          reason       TEXT,
          created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )""")
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

def _season_ctx(request: Request):
    sv = request.query_params.get("season", "")
    season = None if sv in ("", "all") else int(sv)
    asof = request.query_params.get("asof", "latest") or "latest"
    return season, asof, dict(
        seasons=Q.seasons(), season_sel=sv or "all", asof_sel=asof, asof_opts=ASOF_OPTS)


def _seasons_ctx(request: Request):
    """產季可複選(?season=2025&season=2026)—— 儀表板 / 各產品線損益共用。
    沒帶 season → 預設只看最新產季;明確帶 season=all → 全部。"""
    all_seasons = Q.seasons()
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
    asof = request.query_params.get("asof", "latest") or "latest"
    season_qs = "&".join(f"season={s}" for s in sel) if sel else "season=all"
    return (sel or None), asof, dict(
        seasons=all_seasons, seasons_sel=sel, season_qs=season_qs,
        asof_sel=asof, asof_opts=ASOF_OPTS)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    season, asof, ctx = _seasons_ctx(request)
    k = Q.kpi(season, asof)
    mon = Q.monthly(season)
    monmax = max([m["rev"] for m in mon] + [1])
    fin = Q.finance_summary(season)
    prog = Q.season_progress(season)
    # 各產品線賺不賺(pl)2026-09 暫時隱藏,先不算 —— 要恢復時把這行打開,
    # 連同 dashboard.html 裡對應那段一起解除註解。
    # pl = Q.product_line_pnl(season, "rev")
    return tpl.TemplateResponse("dashboard.html", dict(
        request=request, active="dash", k=k, fin=fin, prog=prog,
        mon_json=json.dumps(mon), monmax=monmax,
        alerts=Q.alerts(season, asof), **ctx,
    ))


# ---------- 報表 -----------------------------------------------
REPORT_TABS = {"profit": "獲利分析", "aging": "收款帳齡", "compare": "跨產季比較"}

@app.get("/reports", response_class=HTMLResponse)
def reports(request: Request):
    season, asof, ctx = _season_ctx(request)
    tab = request.query_params.get("tab", "profit")
    if tab not in REPORT_TABS:
        tab = "profit"
    data = dict(request=request, active="report", tab=tab, tabs=REPORT_TABS, **ctx)
    if tab == "profit":
        pm = Q.product_mix(season)
        pmtot = sum(p["rev"] for p in pm) or 1
        for p in pm:
            p["share"] = p["rev"] / pmtot
        nr = Q.new_vs_repeat(season)
        nrtot = (nr["repeat_rev"] + nr["new_rev"]) or 1
        data.update(prod=Q.profit_by_product(season), ch=Q.by_channel(season),
                    pm=pm, nr=nr, nr_rep_pct=nr["repeat_rev"] / nrtot,
                    top=Q.top_customers(season))
    elif tab == "aging":
        buckets, rows = Q.ar_aging(season, asof)
        data.update(buckets=buckets, rows=rows,
                    agmax=max(list(buckets.values()) + [1]),
                    paid_recent=Q.ar_recently_paid(season))
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


# ---------- 供應商 + 進貨單(v2 回合 2) ---------------------
SUP_CATS  = ['原料', '包材', '委外加工', '設備', '服務', '其他']
DOC_TYPES = ['三聯式發票', '二聯式發票', '收據', '農民收據', '無憑證']

@app.get("/suppliers", response_class=HTMLResponse)
def suppliers_page(request: Request):
    rows = q("""SELECT s.*,
                  (SELECT COUNT(*) FROM purchase p WHERE p.supplier_id=s.supplier_id) n_buy,
                  (SELECT COALESCE(SUM(p.amount + p.tax_amount),0) FROM purchase p
                     WHERE p.supplier_id=s.supplier_id) total
                FROM supplier s ORDER BY s.supplier_id""")
    return tpl.TemplateResponse("suppliers.html", dict(request=request, active="supplier", rows=rows))

@app.get("/suppliers/new", response_class=HTMLResponse)
def supplier_new(request: Request):
    return tpl.TemplateResponse("supplier_form.html", dict(request=request, active="supplier", s=None, cats=SUP_CATS))

@app.get("/suppliers/{sid}/edit", response_class=HTMLResponse)
def supplier_edit(request: Request, sid: int):
    s = q("SELECT * FROM supplier WHERE supplier_id=?", (sid,))
    if not s:
        return RedirectResponse("/suppliers", status_code=303)
    return tpl.TemplateResponse("supplier_form.html", dict(request=request, active="supplier", s=s[0], cats=SUP_CATS))

@app.post("/suppliers")
async def supplier_save(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    sid = f.get("supplier_id")
    if f.get("_delete") and sid:
        if not q("SELECT 1 FROM purchase WHERE supplier_id=?", (int(sid),)):
            execute("DELETE FROM supplier WHERE supplier_id=?", (int(sid),))
        return RedirectResponse("/suppliers", status_code=303)
    name = (f.get("name") or "").strip()
    if not name:
        return RedirectResponse("/suppliers", status_code=303)
    cols = dict(name=name, tax_id=g("tax_id"), category=g("category"),
                phone=g("phone"), note=g("note"))
    if sid:
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE supplier SET {sets} WHERE supplier_id=:id", {**cols, "id": int(sid)})
    else:
        keys = ",".join(cols)
        execute(f"INSERT INTO supplier({keys}) VALUES({','.join(':' + k for k in cols)})", cols)
    return RedirectResponse("/suppliers", status_code=303)


def _purchase_form_ctx(request, p):
    return dict(request=request, active="purchase", p=p,
               suppliers=q("SELECT supplier_id, name FROM supplier ORDER BY name"),
               cats=SUP_CATS, docs=DOC_TYPES,
               today=dt.date.today().isoformat())

@app.get("/purchases", response_class=HTMLResponse)
def purchases_page(request: Request):
    ym = request.query_params.get("ym") or ""
    where, args = ["1=1"], []
    if ym:
        where.append("substr(p.purchase_date,1,7)=?"); args.append(ym)
    rows = q(f"""SELECT p.*, s.name sup_name,
                        (SELECT COUNT(*) FROM fixed_asset fa WHERE fa.source_purchase_id=p.purchase_id) has_card
                 FROM purchase p LEFT JOIN supplier s ON s.supplier_id=p.supplier_id
                 WHERE {' AND '.join(where)}
                 ORDER BY p.purchase_date DESC, p.purchase_id DESC""", args)
    total = sum(r["amount"] for r in rows)
    return tpl.TemplateResponse("purchases.html", dict(
        request=request, active="purchase", rows=rows, total=total,
        yms=[r["ym"] for r in q("SELECT DISTINCT substr(purchase_date,1,7) ym FROM purchase ORDER BY ym DESC")],
        ym_sel=ym))

@app.get("/purchases/new", response_class=HTMLResponse)
def purchase_new(request: Request):
    return tpl.TemplateResponse("purchase_form.html", _purchase_form_ctx(request, None))

@app.get("/purchases/{pid}/edit", response_class=HTMLResponse)
def purchase_edit(request: Request, pid: int):
    p = q("SELECT * FROM purchase WHERE purchase_id=?", (pid,))
    if not p:
        return RedirectResponse("/purchases", status_code=303)
    return tpl.TemplateResponse("purchase_form.html", _purchase_form_ctx(request, p[0]))

def _purchase_upsert(cols, pid):
    """寫入 purchase 主資料(不含分錄),回傳 purchase_id。"""
    if pid:
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE purchase SET {sets} WHERE purchase_id=:id", {**cols, "id": int(pid)})
        return int(pid)
    keys = ",".join(cols)
    return execute(f"INSERT INTO purchase({keys}) VALUES({','.join(':' + k for k in cols)})", cols)


def _purchase_delete(pid):
    execute("DELETE FROM purchase WHERE purchase_id=?", (int(pid),))
    ledger.delete_voucher_for("purchase", int(pid))

@app.post("/purchases")
async def purchase_save(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    fl = lambda k: float(f.get(k)) if (f.get(k) or "").strip() else 0.0
    pid = f.get("purchase_id")
    if f.get("_delete") and pid:
        _purchase_delete(pid)
        return RedirectResponse("/purchases", status_code=303)
    date = (f.get("purchase_date") or "").strip()
    if not date:
        return RedirectResponse("/purchases", status_code=303)
    is_fa = 1 if f.get("is_fixed_asset") else 0
    cols = dict(
        purchase_date=date,
        supplier_id=(int(f.get("supplier_id")) if (f.get("supplier_id") or "").isdigit() else None),
        category=g("category"),
        amount=fl("amount"),
        is_fixed_asset=is_fa,
        note=g("note"))
    if is_fa:
        # 設備採購不記分錄(交給固定資產那條路),照舊直接存檔
        _purchase_upsert(cols, pid)
        return RedirectResponse("/purchases", status_code=303)

    # 原料/包材/委外/服務/其他 → 先看確認畫面,存檔動作交給 /purchases/confirm
    existing = q1("SELECT tax_amount, payment_account, doc_type, invoice_no FROM purchase WHERE purchase_id=?",
                  (int(pid),)) if pid else {}
    sup_name = None
    if cols["supplier_id"]:
        r = q1("SELECT name FROM supplier WHERE supplier_id=?", (cols["supplier_id"],))
        sup_name = r.get("name")
    acct_code, acct_name = ledger.PURCHASE_ACCOUNTS.get(cols["category"], ledger.PURCHASE_ACCOUNTS["其他"])
    hidden = dict(cols)
    if pid:
        hidden["purchase_id"] = pid
    back = f"/purchases/{pid}/edit" if pid else "/purchases/new"
    return tpl.TemplateResponse("ledger_confirm.html", dict(
        request=request, active="purchase",
        source_label="進貨", back_url=back, commit_url="/purchases/confirm",
        summary=[
            dict(label="日期", value=date),
            dict(label="供應商", value=sup_name or "—"),
            dict(label="類別", value=cols["category"] or "—"),
            dict(label="金額", value=f"{cols['amount']:,.0f}"),
        ],
        hidden=hidden,
        amount=cols["amount"], primary_account_name=acct_name,
        tax_amount=existing.get("tax_amount"), payment_account=existing.get("payment_account"),
        doc_type=existing.get("doc_type"), doc_types=DOC_TYPES, bank_names=Q.payment_account_names(),
        invoice_no=existing.get("invoice_no"),
    ))


@app.post("/purchases/confirm")
async def purchase_confirm(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    fl = lambda k: float(f.get(k)) if (f.get(k) or "").strip() else 0.0
    pid = f.get("purchase_id")
    tax_amount = fl("tax_amount")
    payment_account = g("payment_account")
    doc_type = g("doc_type")
    invoice_no = g("invoice_no")
    if doc_type and "發票" in doc_type and not invoice_no:
        back = f"/purchases/{pid}/edit" if pid else "/purchases/new"
        return RedirectResponse(f"{back}?ierr=1", status_code=303)
    cols = dict(
        purchase_date=(f.get("purchase_date") or "").strip(),
        supplier_id=(int(f.get("supplier_id")) if (f.get("supplier_id") or "").isdigit() else None),
        category=g("category"),
        amount=fl("amount"),
        is_fixed_asset=0,
        tax_amount=tax_amount,
        payment_account=payment_account,
        doc_type=doc_type,
        invoice_no=invoice_no,
        note=g("note"))
    new_id = _purchase_upsert(cols, pid)
    legs = ledger.compose_purchase_entries(cols["category"], cols["amount"], tax_amount,
                                            payment_account, is_fixed_asset=False)
    ledger.save_voucher("purchase", new_id, cols["purchase_date"], legs, "P",
                         note=f"進貨:{cols['category'] or ''}")
    return RedirectResponse("/purchases", status_code=303)


# ---------- 固定資產卡 + 折舊(v2 回合 3) -------------------
ASSET_CATS = {"機器設備": 5, "生財器具": 5, "運輸設備": 5,
              "電腦設備": 3, "房屋建築": 30, "其他": 5}

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
               cats=ASSET_CATS, today=dt.date.today().isoformat(),
               bank_names=Q.payment_account_names())

@app.get("/assets/new", response_class=HTMLResponse)
def asset_new(request: Request):
    prefill = None
    pv = request.query_params.get("purchase")
    if pv and pv.isdigit():
        r = q("SELECT * FROM purchase WHERE purchase_id=?", (int(pv),))
        if r:
            p = r[0]
            prefill = dict(source_purchase_id=p["purchase_id"], name=(p["note"] or "設備"),
                           acquire_date=p["purchase_date"], cost=p["amount"])
    return tpl.TemplateResponse("asset_form.html", _asset_form_ctx(request, None, prefill))

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
    life = int(f.get("life_years") or 5) or 5
    cost = fl("cost")
    grant = fl("grant_amount")
    sv = (f.get("salvage") or "").strip()
    salvage = float(sv) if sv else round(max(0.0, cost - grant) / (life + 1))
    spv = f.get("source_purchase_id") or ""
    category = g("category")
    payment_account = g("payment_account")
    cols = dict(
        name=name, category=category,
        acquire_date=date, cost=cost, grant_amount=grant, salvage=salvage,
        life_years=life, method="平均法",
        source_purchase_id=(int(spv) if spv.isdigit() else None),
        disposed_date=g("disposed_date"), payment_account=payment_account, note=g("note"))
    if aid:
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE fixed_asset SET {sets} WHERE asset_id=:id", {**cols, "id": int(aid)})
        new_id = int(aid)
    else:
        keys = ",".join(cols)
        new_id = execute(f"INSERT INTO fixed_asset({keys}) VALUES({','.join(':' + k for k in cols)})", cols)
    legs = ledger.compose_asset_acquire_entries(category, cost, grant, payment_account)
    ledger.save_voucher("asset_acquire", new_id, date, legs, "K", note=f"設備取得:{name}")
    return RedirectResponse("/assets", status_code=303)


# ---------- 商品目錄 + 定價 -----------------------------------
PRICE_SEGS = ['零售', '批發', '團購', '機構', '內部']   # price_list.customer_segment
PROD_TYPES = ['單品', '組合', '禮盒', '裸裝', '試吃包', '加購贈品']
PKG_FORMS  = ['夾鏈袋', '罐', '禮盒', '真空袋', '裸裝']
UOMS       = ['包', '斤', '盒', '罐', '支']
STORAGE    = ['常溫', '冷藏', '冷凍']
# customer.segment -> price_list.customer_segment
SEG_MAP = {'零售': '零售', '批發': '批發', '團購主': '團購', '機構': '機構', '公關對象': '內部'}

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
    on_hand = q("SELECT COALESCE(SUM(qty),0) v FROM stock_move WHERE product_id=?", (pid,))[0]["v"]
    n_returns = q("SELECT COUNT(*) n FROM sales_return WHERE product_id=?", (pid,))[0]["n"]
    n_batch = q("SELECT COUNT(*) n FROM batch WHERE product_id=?", (pid,))[0]["n"]
    n_target = q("SELECT COUNT(*) n FROM sales_target WHERE product_id=?", (pid,))[0]["n"]
    uom = (q("SELECT uom FROM product WHERE product_id=?", (pid,)) or [{}])[0].get("uom", "")
    reasons = []
    if n_orders:
        reasons.append(f"有 {n_orders} 張訂單用過這個商品")
    if on_hand:
        reasons.append(f"現有庫存還有 {round(on_hand)} {uom}")
    if n_returns:
        reasons.append(f"有 {n_returns} 筆退貨紀錄")
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
    cols = dict(sku=(f.get("sku") or "").strip(), name=(f.get("name") or "").strip(),
                product_type=g("product_type"), type_code_raw=g("type_code_raw"),
                product_group_id=ig("product_group_id"),
                net_weight_g=ig("net_weight_g"), gross_weight_g=ig("gross_weight_g"),
                package_form=g("package_form"), uom=(f.get("uom") or "包").strip(),
                grams_per_uom=ig("grams_per_uom"), shelf_life_days=ig("shelf_life_days"),
                storage_condition=g("storage_condition"), ingredients=g("ingredients"),
                origin=g("origin"), barcode=g("barcode"),
                gift_only=1 if f.get("gift_only") else 0,
                unit_cost=(float(f.get("unit_cost")) if (f.get("unit_cost") or "").strip() else 0),
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
CUST_TYPES = ['個人', '公司', '機構團體', '通路商', '內部']
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
        rows = q("""SELECT cu.customer_id id, cu.display_name name, cu.segment, cu.phone,
                      (SELECT MAX(order_date) FROM "order" o WHERE o.customer_id=cu.customer_id) last_order,
                      (SELECT COUNT(*) FROM "order" o WHERE o.customer_id=cu.customer_id AND o.order_kind='銷售') n_orders
                    FROM customer cu
                    WHERE cu.customer_id IN (
                       SELECT customer_id FROM customer WHERE display_name LIKE :k OR phone LIKE :k
                       UNION SELECT customer_id FROM customer_alias WHERE alias_text LIKE :k)
                    ORDER BY n_orders DESC, cu.display_name LIMIT 25""", {"k": f"%{kw}%"})
    else:
        rows = q("""SELECT cu.customer_id id, cu.display_name name, cu.segment, cu.phone,
                      (SELECT MAX(order_date) FROM "order" o WHERE o.customer_id=cu.customer_id) last_order,
                      (SELECT COUNT(*) FROM "order" o WHERE o.customer_id=cu.customer_id AND o.order_kind='銷售') n_orders
                    FROM customer cu ORDER BY last_order DESC NULLS LAST, cu.display_name LIMIT 8""")
    return JSONResponse(rows)

@app.get("/api/customers/{cid}")
def api_customer(cid: int):
    c = q("""SELECT customer_id id, display_name name, segment, phone, contact_person
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
    cols = dict(display_name=name, customer_type=g("customer_type"), segment=g("segment"),
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


def next_order_no():
    """依現有單號最大編號 +1(不是用筆數算,避免刪過訂單後編號撞號)。"""
    row = q("SELECT MAX(CAST(SUBSTR(order_no,2) AS INTEGER)) m FROM \"order\" WHERE order_no LIKE 'S%'")
    m = (row[0]["m"] or 0) + 1
    return f"S{m:04d}"


# ---------- 新增訂單 -------------------------------------------
@app.get("/orders/new", response_class=HTMLResponse)
def order_new(request: Request):
    customers = q("SELECT customer_id,display_name,segment FROM customer ORDER BY display_name")
    prices = {}
    for r in q("SELECT product_id,customer_segment,unit_price FROM price_list WHERE channel_id IS NULL"):
        prices.setdefault(r["product_id"], {})[r["customer_segment"]] = r["unit_price"]
    cust_seg = {c["customer_id"]: SEG_MAP.get(c["segment"], "零售") for c in customers}
    return tpl.TemplateResponse("orders_new.html", dict(
        request=request, active="order", customers=customers,
        channels=q("SELECT channel_id,name FROM channel ORDER BY channel_id"),
        products=q("SELECT product_id,sku,name,uom FROM product WHERE status='在售' ORDER BY sku"),
        today=dt.date.today().isoformat(),
        prices_json=json.dumps(prices), cust_seg_json=json.dumps(cust_seg),
        stock_json=json.dumps(Q.stock_on_hand_map()), ship_payers=SHIP_PAYERS,
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
    lines = []
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

    discount_total = flt("discount_total")
    subtotal = sum(qv * up for _, qv, up, _, _ in lines)
    # 每列的成交價已是實收價;discount_total 只放使用者另外填的整單折讓。
    # 「賣得比定價低」的差額改由 list_price 於報表即時計算,不重複扣。
    # 應收金額不含運費(運費只是家易花多少錢的紀錄,不跟客人收的部分另外拆帳)。
    total = subtotal - discount_total
    invoiced = 1 if one("invoiced") else 0
    oid = execute("""INSERT INTO "order"(order_no,order_date,season,customer_id,channel_id,order_kind,
                     discount_total,order_total,payment_method,payment_status,
                     shipping_cost_actual,ship_payer,ship_method,ship_status,invoiced,tax_doc_no)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, '待出貨',?,?)""",
                  (next_order_no(), order_date, Q.season_of(order_date), customer_id, channel_id, order_kind,
                   discount_total, total,
                   one("payment_method") or None, one("payment_status", "待收款"),
                   flt("shipping_cost_actual"), one("ship_payer", "店家吸收"), one("ship_method") or None,
                   invoiced, one("tax_doc_no") or None))
    for p, qv, up, b, lp in lines:
        execute("""INSERT INTO order_line(order_id,product_id,batch_id,qty,unit_price,list_price,line_subtotal)
                   VALUES(?,?,?,?,?,?,?)""", (oid, p, b, qv, up, lp, qv * up))
    sync_order_stock(oid)
    _post_order_ledger(oid)
    return RedirectResponse(f"/orders/{oid}?ok=1", status_code=303)


def sync_order_stock(oid):
    """依訂單目前的明細,重建該訂單造成的出庫紀錄。"""
    o = q('SELECT order_kind FROM "order" WHERE order_id=?', (oid,))
    if not o:
        return
    mtype = "銷售出庫" if o[0]["order_kind"] == "銷售" else "贈送出庫"
    execute("DELETE FROM stock_move WHERE ref_order_id=? AND move_type IN ('銷售出庫','贈送出庫')", (oid,))
    d = q('SELECT order_date FROM "order" WHERE order_id=?', (oid,))[0]["order_date"]
    for l in q("SELECT product_id, batch_id, qty FROM order_line WHERE order_id=?", (oid,)):
        execute("""INSERT INTO stock_move(move_date,product_id,batch_id,qty,move_type,ref_order_id)
                   VALUES(?,?,?,?,?,?)""",
                (d, l["product_id"], l["batch_id"], -abs(l["qty"] or 0), mtype, oid))


def _post_order_ledger(oid):
    """訂單存檔/標記已收款後呼叫:重開 order_sale(成立分錄)+ order_payment(收款分錄)。
    贈送/樣品等非「銷售」訂單、或還沒收到錢,對應那張就不會產生(ledger.save_voucher
    在 legs 為空時只清空、不新增)。"""
    o, lines = Q.order_get(oid)
    if not o:
        return
    sale_lines = [(l["pg_name"], l["line_subtotal"] or 0, (l["qty"] or 0) * (l["unit_cost"] or 0))
                  for l in lines if not l["is_gift"]]
    sale_legs = ledger.compose_order_sale_entries(sale_lines, o["order_kind"])
    ledger.save_voucher("order_sale", oid, o["order_date"], sale_legs, "S", note=f"訂單 {o['order_no']}")

    pay_legs = ledger.compose_order_payment_entries(o["paid_amount"], o["payment_account"])
    pay_date = o["paid_date"] or o["order_date"]
    ledger.save_voucher("order_payment", oid, pay_date, pay_legs, "R", note=f"訂單 {o['order_no']} 收款")


# ---------- 訂單管理:清單 / 明細 / 更新 -----------------------
PAY_METHODS  = ['現金', '銀行匯款', '貨到付款', '行動支付', '信用卡', '平台代收', '未收款']
PAY_STATUS   = ['待收款', '部分收款', '已收款', '免收款']
SHIP_METHODS = ['自行配送', '客戶自取', '宅配', '超商店到店', '超商賣貨便', '冷藏宅配']
SHIP_STATUS  = ['待出貨', '已出貨', '已送達', '退回', '遺失', '破損']
SHIP_PAYERS  = ['店家吸收', '客戶付']
ORDER_KINDS  = ['銷售', '贈送-公關', '贈送-捐贈', '樣品', '理賠重寄', '換貨補出', '內部領用']
FILTERS = {"all": "全部", "overdue": "貨款逾期", "unpaid": "未收款", "unshipped": "超過 3 天未出貨"}

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
        ship_methods=SHIP_METHODS, ship_status=SHIP_STATUS, ship_payers=SHIP_PAYERS, kinds=ORDER_KINDS,
        returns=Q.returns_list(oid), return_kinds=RETURN_KINDS,
        stock_json=json.dumps(Q.stock_on_hand_map()),
        today=dt.date.today().isoformat()))


# ---------- 銷貨退回 / 折讓(v2 回合 8) ----------------------
RETURN_KINDS = ['退貨', '折讓']

def _post_return_ledger(rid, oid, date, kind, amount, product_id, qty, restock):
    o = q1('SELECT payment_status, payment_account FROM "order" WHERE order_id=?', (oid,))
    already_paid = o.get("payment_status") == "已收款"
    cogs_amount, pg_name = 0, None
    if restock and product_id and qty:
        p = q1("""SELECT p.unit_cost, pg.name pg_name FROM product p
                   LEFT JOIN product_group pg ON pg.pg_id=p.product_group_id
                   WHERE p.product_id=?""", (product_id,))
        cogs_amount = abs(qty) * (p.get("unit_cost") or 0)
        pg_name = p.get("pg_name")
    legs = ledger.compose_sales_return_entries(kind, amount, already_paid, o.get("payment_account"),
                                                cogs_amount, pg_name)
    ledger.save_voucher("sales_return", rid, date, legs, "T", note=f"{kind}:訂單#{oid}")

@app.get("/returns", response_class=HTMLResponse)
def returns_page(request: Request):
    rows = Q.returns_list()
    total = sum(r["amount"] for r in rows)
    return tpl.TemplateResponse("returns.html", dict(
        request=request, active="returns", rows=rows, total=total))

@app.post("/orders/{oid}/return")
async def order_return_create(request: Request, oid: int):
    f = await request.form()
    o, _ = Q.order_get(oid)
    if not o:
        return RedirectResponse("/orders", status_code=303)
    g = lambda k: (f.get(k) or "").strip() or None
    fl = lambda k: float(f.get(k)) if (f.get(k) or "").strip() else 0.0
    date = (f.get("return_date") or "").strip() or dt.date.today().isoformat()
    kind = (f.get("kind") or "退貨").strip()
    if kind not in RETURN_KINDS:
        kind = "退貨"
    amount = fl("amount")
    if amount <= 0:
        return RedirectResponse(f"/orders/{oid}?rerr=1", status_code=303)
    pid = f.get("product_id") or ""
    bid = f.get("batch_id") or ""
    qty = fl("qty")
    restock = 1 if f.get("restock") else 0
    cols = dict(
        order_id=oid, return_date=date, season=Q.season_of(date), kind=kind, amount=amount,
        product_id=(int(pid) if pid.isdigit() else None),
        qty=(qty or None),
        batch_id=(int(bid) if bid.isdigit() else None),
        restock=restock, reason=g("reason"))
    keys = ",".join(cols)
    rid = execute(f"INSERT INTO sales_return({keys}) VALUES({','.join(':' + k for k in cols)})", cols)
    # 好貨退回可再賣 → 產生一筆「退貨入庫」
    if restock and cols["product_id"] and qty > 0:
        execute("""INSERT INTO stock_move(move_date,product_id,batch_id,qty,move_type,ref_order_id,note)
                   VALUES(?,?,?,?,?,?,?)""",
                (date, cols["product_id"], cols["batch_id"], abs(qty), "退貨入庫", oid,
                 f"退貨單#{rid}"))
    _post_return_ledger(rid, oid, date, kind, amount, cols["product_id"], qty, restock)
    return RedirectResponse(f"/orders/{oid}?rok=1", status_code=303)

@app.post("/orders/{oid}/return_all")
async def order_return_all(request: Request, oid: int):
    o, lines = Q.order_get(oid)
    if not o:
        return RedirectResponse("/orders", status_code=303)
    date = dt.date.today().isoformat()
    season = Q.season_of(date)
    for ln in lines:
        if ln["is_gift"] or not ln["line_subtotal"]:
            continue
        rid = execute("""INSERT INTO sales_return(order_id,return_date,season,kind,amount,
                     product_id,qty,batch_id,restock,reason)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (oid, date, season, "退貨", ln["line_subtotal"],
                 ln["product_id"], ln["qty"], ln["batch_id"], 1, "整筆退單"))
        execute("""INSERT INTO stock_move(move_date,product_id,batch_id,qty,move_type,ref_order_id,note)
                   VALUES(?,?,?,?,?,?,?)""",
                (date, ln["product_id"], ln["batch_id"], abs(ln["qty"]), "退貨入庫", oid,
                 f"退貨單#{rid}"))
        _post_return_ledger(rid, oid, date, "退貨", ln["line_subtotal"], ln["product_id"], ln["qty"], 1)
    return RedirectResponse(f"/orders/{oid}?rok=1", status_code=303)


def _return_delete(rid):
    """回傳被刪那筆退貨所屬的 order_id(找不到就 None)。"""
    r = q("SELECT order_id FROM sales_return WHERE return_id=?", (rid,))
    execute("DELETE FROM stock_move WHERE move_type='退貨入庫' AND note=?", (f"退貨單#{rid}",))
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
    new_lines = []
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
    execute("DELETE FROM order_line WHERE order_id=?", (oid,))
    for p, qv, up, b, gf in new_lines:
        execute("""INSERT INTO order_line(order_id,product_id,batch_id,qty,unit_price,line_subtotal,is_gift)
                   VALUES(?,?,?,?,?,?,?)""", (oid, p, b, qv, up, qv * up, gf))

    subtotal = sum(qv * up for _, qv, up, _, gf in new_lines if not gf)
    disc = o["discount_total"] or 0  # 折扣欄位已不開放編輯,沿用原值(通常是 0)
    total = subtotal - disc

    cust_id = f.get("customer_id")
    cols = dict(order_date=g("order_date"), order_kind=g("order_kind"),
                payment_method=g("payment_method"), payment_status=g("payment_status") or "待收款",
                paid_date=g("paid_date"), paid_amount=(flt("paid_amount") or None),
                order_total=total,
                ship_method=g("ship_method"), carrier=g("carrier"), tracking_no=g("tracking_no"),
                shipped_date=g("shipped_date"), delivered_date=g("delivered_date"),
                ship_status=g("ship_status") or "待出貨",
                shipping_cost_actual=flt("shipping_cost_actual"),
                ship_payer=g("ship_payer") or "店家吸收", note=g("note"),
                invoiced=(1 if f.get("invoiced") else 0), tax_doc_no=g("tax_doc_no"))
    if cust_id and cust_id.isdigit():
        cols["customer_id"] = int(cust_id)
    if cols.get("order_date"):
        cols["season"] = Q.season_of(cols["order_date"])
    sets = ",".join(f'{k}=:{k}' for k in cols)
    execute(f'UPDATE "order" SET {sets} WHERE order_id=:id', {**cols, "id": oid})
    sync_order_stock(oid)
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
        execute("""INSERT INTO order_line(order_id,product_id,batch_id,qty,unit_price,list_price,line_subtotal,is_gift)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (noid, l["product_id"], l["batch_id"], l["qty"], l["unit_price"],
                 l["list_price"], 0 if l["is_gift"] else l["qty"] * l["unit_price"], l["is_gift"]))
    execute('UPDATE "order" SET order_total=? WHERE order_id=?', (subtotal, noid))
    sync_order_stock(noid)
    _post_order_ledger(noid)
    return RedirectResponse(f"/orders/{noid}?ok=1", status_code=303)

@app.post("/orders/{oid}/delete")
def order_delete(oid: int):
    o = q('SELECT order_id FROM "order" WHERE order_id=?', (oid,))
    if not o:
        return RedirectResponse("/orders", status_code=303)
    execute("DELETE FROM stock_move WHERE ref_order_id=?", (oid,))
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
    try:
        amt = float(f.get("paid_amount") or 0)
    except ValueError:
        amt = 0.0
    if amt <= 0:
        return RedirectResponse(f"/orders/{oid}?perr=1", status_code=303)
    pdate = (f.get("paid_date") or "").strip() or dt.date.today().isoformat()
    # 實收 >= 應收(差一點四捨五入誤差也算)→ 已收款;不足 → 部分收款
    status = "已收款" if amt >= (o["order_total"] or 0) - 0.5 else "部分收款"
    execute("""UPDATE "order" SET payment_status=?, paid_date=?, paid_amount=?
               WHERE order_id=?""", (status, pdate, amt, oid))
    _post_order_ledger(oid)
    return RedirectResponse(f"/orders/{oid}?done=paid", status_code=303)

@app.post("/orders/{oid}/shipped")
def order_mark_shipped(oid: int):
    execute("""UPDATE "order" SET ship_status='已出貨',
               shipped_date=COALESCE(shipped_date, ?) WHERE order_id=?""",
            (dt.date.today().isoformat(), oid))
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
            execute("""INSERT INTO order_line(order_id,product_id,batch_id,qty,unit_price,line_subtotal,is_gift)
                       VALUES(?,?,?,?,0,0,1)""", (noid, l["product_id"], l["batch_id"], l["qty"]))
        execute("UPDATE shipment_issue SET linked_reship_order_id=? WHERE issue_id=?", (noid, iid))
        sync_order_stock(noid)
    return RedirectResponse("/issues", status_code=303)


# ---------- 各產品線損益(v2 回合 6) ------------------------
PNL_BASIS = {"rev": "依營收", "qty": "依銷量", "dm": "依直接成本"}

@app.get("/lines", response_class=HTMLResponse)
def product_lines_pnl(request: Request):
    season, asof, ctx = _seasons_ctx(request)
    basis = request.query_params.get("basis", "rev")
    if basis not in PNL_BASIS:
        basis = "rev"
    data = Q.product_line_pnl(season, basis)
    return tpl.TemplateResponse("lines.html", dict(
        request=request, active="lines", basis=basis, basis_opts=PNL_BASIS,
        d=data, **ctx))


# ---------- 財務健康:月損益 ----------------------------------
@app.get("/finance", response_class=HTMLResponse)
def finance_page(request: Request):
    season, asof, ctx = _seasons_ctx(request)
    fin = []
    for ym in Q.finance_months(season):
        d = Q.finance_month(ym)
        fin.append(dict(ym=ym, revenue=d["revenue"], returns=d["returns"], cogs=d["cogs"],
                        gross_profit=d["gross_profit"], ship_cost=d["ship_cost"],
                        opex_total=d["opex_total"], pretax=d["pretax"],
                        opex={r["category"]: r["amt"] for r in d["opex"]}))
    return tpl.TemplateResponse("finance.html", dict(
        request=request, active="finance",
        fin_json=json.dumps(fin), cats=Q.OPEX_CATS,
        seasons_fin=[Q.season_finance(s) for s in Q.seasons()], **ctx))

@app.get("/finance/expenses", response_class=HTMLResponse)
def finance_expenses(request: Request):
    rows = q("SELECT * FROM op_expense ORDER BY ym DESC, category")
    return tpl.TemplateResponse("finance_expenses.html", dict(
        request=request, active="expenses", rows=rows))

def _expense_form_ctx(request, e):
    months = Q.months_with_data()
    return dict(request=request, active="expenses", e=e, cats=Q.OPEX_CATS,
               docs=DOC_TYPES,
               ym_default=(months[-1] if months else dt.date.today().strftime("%Y-%m")))

@app.get("/finance/expenses/new", response_class=HTMLResponse)
def finance_expense_new(request: Request):
    return tpl.TemplateResponse("finance_expense_form.html", _expense_form_ctx(request, None))

@app.get("/finance/expenses/{eid}/edit", response_class=HTMLResponse)
def finance_expense_edit(request: Request, eid: int):
    e = q("SELECT * FROM op_expense WHERE expense_id=?", (eid,))
    if not e:
        return RedirectResponse("/finance/expenses", status_code=303)
    return tpl.TemplateResponse("finance_expense_form.html", _expense_form_ctx(request, e[0]))

def _expense_upsert(cols, eid):
    """寫入 op_expense 主資料(不含分錄),回傳 expense_id。"""
    if eid:
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE op_expense SET {sets}, updated_at=datetime('now','localtime') WHERE expense_id=:id",
                {**cols, "id": int(eid)})
        return int(eid)
    keys = ",".join(cols)
    return execute(f"INSERT INTO op_expense({keys}, updated_at) "
                   f"VALUES({','.join(':' + k for k in cols)}, datetime('now','localtime'))", cols)


def _expense_delete(eid):
    execute("DELETE FROM op_expense WHERE expense_id=?", (int(eid),))
    ledger.delete_voucher_for("op_expense", int(eid))

@app.post("/finance/expenses")
async def finance_expense_save(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    fl = lambda k: float(f.get(k)) if (f.get(k) or "").strip() else 0.0
    eid = f.get("expense_id")
    if f.get("_delete") and eid:
        _expense_delete(eid)
        return RedirectResponse("/finance/expenses", status_code=303)
    ym = (f.get("ym") or "").strip()
    cat = (f.get("category") or "").strip()
    if not (ym and cat):
        return RedirectResponse("/finance/expenses", status_code=303)
    am = f.get("amortize_months") or ""
    cols = dict(
        ym=ym, category=cat, amount=fl("amount"),
        amortize_months=(int(am) if am.isdigit() and int(am) > 1 else None),
        note=g("note"))

    # 走確認畫面,存檔動作交給 /finance/expenses/confirm
    existing = q1("SELECT tax_amount, payment_account, doc_type, invoice_no FROM op_expense WHERE expense_id=?",
                  (int(eid),)) if eid else {}
    acct_code, acct_name = ledger.EXPENSE_ACCOUNTS.get(cat, ledger.EXPENSE_ACCOUNTS["其他"])
    hidden = dict(cols)
    if eid:
        hidden["expense_id"] = eid
    back = f"/finance/expenses/{eid}/edit" if eid else "/finance/expenses/new"
    return tpl.TemplateResponse("ledger_confirm.html", dict(
        request=request, active="expenses",
        source_label="營運費用", back_url=back, commit_url="/finance/expenses/confirm",
        summary=[
            dict(label="月份", value=ym),
            dict(label="項目", value=cat),
            dict(label="金額", value=f"{cols['amount']:,.0f}"),
        ],
        hidden=hidden,
        amount=cols["amount"], primary_account_name=acct_name,
        tax_amount=existing.get("tax_amount"), payment_account=existing.get("payment_account"),
        doc_type=existing.get("doc_type"), doc_types=DOC_TYPES, bank_names=Q.payment_account_names(),
        invoice_no=existing.get("invoice_no"),
    ))


@app.post("/finance/expenses/confirm")
async def finance_expense_confirm(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    fl = lambda k: float(f.get(k)) if (f.get(k) or "").strip() else 0.0
    eid = f.get("expense_id")
    tax_amount = fl("tax_amount")
    payment_account = g("payment_account")
    am = f.get("amortize_months") or ""
    ym = (f.get("ym") or "").strip()
    cat = (f.get("category") or "").strip()
    doc_type = g("doc_type")
    invoice_no = g("invoice_no")
    if doc_type and "發票" in doc_type and not invoice_no:
        back = f"/finance/expenses/{eid}/edit" if eid else "/finance/expenses/new"
        return RedirectResponse(f"{back}?ierr=1", status_code=303)
    cols = dict(
        ym=ym, category=cat, amount=fl("amount"),
        amortize_months=(int(am) if am.isdigit() and int(am) > 1 else None),
        tax_amount=tax_amount, payment_account=payment_account, doc_type=doc_type,
        invoice_no=invoice_no,
        note=g("note"))
    new_id = _expense_upsert(cols, eid)
    legs = ledger.compose_expense_entries(cat, cols["amount"], tax_amount, payment_account)
    ledger.save_voucher("op_expense", new_id, f"{ym}-01", legs, "E", note=f"營運費用:{cat}")
    return RedirectResponse("/finance/expenses", status_code=303)


# ---------- 9 宮格 B 類:首頁入口 ----------------------------
ENTRY_CARDS = [
    dict(title="銷售 / 出貨", href="/orders/new", desc="開一張訂單,存檔就自動記應收帳款/銷貨收入。"),
    dict(title="採購物料", href="/purchases/new", desc="原料/包材/委外/服務進貨,存檔前會給你看一次分錄。"),
    dict(title="營運支出", href="/finance/expenses/new", desc="逐月費用登記,存檔前會給你看一次分錄。"),
    dict(title="設備相關", href="/assets/new", desc="機器/器具採購,建固定資產卡,系統會自動記分錄、每月提折舊。"),
    dict(title="生產入庫", href="/stock#produce", desc="農產品/蜂蜜做好入庫,登記數量+價值。"),
    dict(title="其他收益", href="/other-income/new", desc="利息收入、政府補助等非銷售的進帳。"),
    dict(title="其他費用", href="/finance/expenses/new", desc="勞務費/檢驗費/規費等雜項費用,在「營運支出」的類別選單裡就找得到。"),
    dict(title="資本異動", href="/equity/new", desc="現金增資、盈餘轉列公積。"),
    dict(title="帳務調整", href="/adjustments/new", desc="上面都套不上時,手動指定一組借/貸科目記一筆。"),
]

@app.get("/entry", response_class=HTMLResponse)
def entry_page(request: Request):
    return tpl.TemplateResponse("entry.html", dict(request=request, active="entry", cards=ENTRY_CARDS))


# ---------- 9 宮格 B 類:其他收益 ----------------------------
OTHER_INCOME_CATS = list(ledger.OTHER_INCOME_ACCOUNTS.keys())

@app.get("/other-income", response_class=HTMLResponse)
def other_income_list(request: Request):
    rows = q("SELECT * FROM other_income ORDER BY income_date DESC, income_id DESC")
    return tpl.TemplateResponse("other_income_list.html", dict(
        request=request, active="other_income", rows=rows))

def _other_income_form_ctx(request, r):
    return dict(request=request, active="other_income", r=r, cats=OTHER_INCOME_CATS,
                bank_names=Q.payment_account_names())

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
    cat = g("category")
    if not (date and cat):
        return RedirectResponse("/other-income", status_code=303)
    cols = dict(income_date=date, category=cat, amount=fl("amount"), tax_amount=fl("tax_amount"),
                payment_account=g("payment_account"), note=g("note"))
    if iid:
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE other_income SET {sets} WHERE income_id=:id", {**cols, "id": int(iid)})
        new_id = int(iid)
    else:
        keys = ",".join(cols)
        new_id = execute(f"INSERT INTO other_income({keys}) VALUES({','.join(':' + k for k in cols)})", cols)
    legs = ledger.compose_other_income_entries(cat, cols["amount"], cols["tax_amount"], cols["payment_account"])
    ledger.save_voucher("other_income", new_id, date, legs, "I", note=f"其他收益:{cat}")
    return RedirectResponse("/other-income", status_code=303)


# ---------- 9 宮格 B 類:資本異動 ----------------------------
@app.get("/equity", response_class=HTMLResponse)
def equity_list(request: Request):
    rows = q("SELECT * FROM equity_txn ORDER BY txn_date DESC, txn_id DESC")
    return tpl.TemplateResponse("equity_list.html", dict(request=request, active="equity", rows=rows))

@app.get("/equity/new", response_class=HTMLResponse)
def equity_new(request: Request):
    return tpl.TemplateResponse("equity_form.html", dict(
        request=request, active="equity", r=None, bank_names=Q.payment_account_names()))

@app.get("/equity/{tid}/edit", response_class=HTMLResponse)
def equity_edit(request: Request, tid: int):
    r = q("SELECT * FROM equity_txn WHERE txn_id=?", (tid,))
    if not r:
        return RedirectResponse("/equity", status_code=303)
    return tpl.TemplateResponse("equity_form.html", dict(
        request=request, active="equity", r=r[0], bank_names=Q.payment_account_names()))

def _equity_delete(tid):
    execute("DELETE FROM equity_txn WHERE txn_id=?", (int(tid),))
    ledger.delete_voucher_for("equity", int(tid))

@app.post("/equity")
async def equity_save(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    fl = lambda k: float(f.get(k)) if (f.get(k) or "").strip() else 0.0
    tid = f.get("txn_id")
    if f.get("_delete") and tid:
        _equity_delete(tid)
        return RedirectResponse("/equity", status_code=303)
    date = (f.get("txn_date") or "").strip()
    ttype = g("txn_type")
    if not (date and ttype):
        return RedirectResponse("/equity", status_code=303)
    cols = dict(txn_date=date, txn_type=ttype, amount=fl("amount"), payment_account=g("payment_account"),
                note=g("note"))
    if tid:
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE equity_txn SET {sets} WHERE txn_id=:id", {**cols, "id": int(tid)})
        new_id = int(tid)
    else:
        keys = ",".join(cols)
        new_id = execute(f"INSERT INTO equity_txn({keys}) VALUES({','.join(':' + k for k in cols)})", cols)
    legs = ledger.compose_equity_entries(ttype, cols["amount"], cols["payment_account"])
    ledger.save_voucher("equity", new_id, date, legs, "Q", note=f"資本異動:{ttype}")
    return RedirectResponse("/equity", status_code=303)


# ---------- 9 宮格 B 類:帳務調整(手動指定借/貸科目)---------
@app.get("/adjustments", response_class=HTMLResponse)
def adjustment_list(request: Request):
    rows = q("SELECT * FROM manual_entry ORDER BY entry_date DESC, entry_id DESC")
    return tpl.TemplateResponse("adjustment_list.html", dict(request=request, active="adjust", rows=rows))

@app.get("/adjustments/new", response_class=HTMLResponse)
def adjustment_new(request: Request):
    return tpl.TemplateResponse("adjustment_form.html", dict(
        request=request, active="adjust", r=None, accounts=ledger.ALL_ACCOUNTS))

@app.get("/adjustments/{mid}/edit", response_class=HTMLResponse)
def adjustment_edit(request: Request, mid: int):
    r = q("SELECT * FROM manual_entry WHERE entry_id=?", (mid,))
    if not r:
        return RedirectResponse("/adjustments", status_code=303)
    return tpl.TemplateResponse("adjustment_form.html", dict(
        request=request, active="adjust", r=r[0], accounts=ledger.ALL_ACCOUNTS))

def _adjustment_delete(mid):
    execute("DELETE FROM manual_entry WHERE entry_id=?", (int(mid),))
    ledger.delete_voucher_for("manual_adjustment", int(mid))

@app.post("/adjustments")
async def adjustment_save(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    fl = lambda k: float(f.get(k)) if (f.get(k) or "").strip() else 0.0
    mid = f.get("entry_id")
    if f.get("_delete") and mid:
        _adjustment_delete(mid)
        return RedirectResponse("/adjustments", status_code=303)
    date = (f.get("entry_date") or "").strip()
    debit_code, _, debit_name = (f.get("debit_account") or "").partition("|")
    credit_code, _, credit_name = (f.get("credit_account") or "").partition("|")
    if not (date and debit_code and credit_code):
        return RedirectResponse("/adjustments", status_code=303)
    cols = dict(entry_date=date, debit_code=debit_code, debit_name=debit_name,
                credit_code=credit_code, credit_name=credit_name, amount=fl("amount"), note=g("note"))
    if mid:
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE manual_entry SET {sets} WHERE entry_id=:id", {**cols, "id": int(mid)})
        new_id = int(mid)
    else:
        keys = ",".join(cols)
        new_id = execute(f"INSERT INTO manual_entry({keys}) VALUES({','.join(':' + k for k in cols)})", cols)
    legs = ledger.compose_manual_entries(debit_code, debit_name, credit_code, credit_name, cols["amount"])
    ledger.save_voucher("manual_adjustment", new_id, date, legs, "M", note=g("note"))
    return RedirectResponse("/adjustments", status_code=303)


# ---------- 總帳(進貨/營運費用/訂單/9宮格B類 自動過帳)-------
@app.get("/ledger", response_class=HTMLResponse)
def ledger_page(request: Request):
    src = request.query_params.get("source") or ""
    where, args = ["1=1"], []
    if src in ("purchase", "op_expense", "order_sale", "order_payment",
               "production_in", "other_income", "equity", "manual_adjustment",
               "asset_acquire", "asset_depreciation", "sales_return"):
        where.append("source_type=?"); args.append(src)
    rows = q(f"""SELECT * FROM ledger_entry WHERE {' AND '.join(where)}
                 ORDER BY voucher_no DESC, entry_id""", args)
    vouchers = []
    seen = {}
    for r in rows:
        if r["voucher_no"] not in seen:
            seen[r["voucher_no"]] = dict(voucher_no=r["voucher_no"], entry_date=r["entry_date"],
                                          source_type=r["source_type"], source_id=r["source_id"],
                                          note=r["note"], lines=[], total_debit=0, total_credit=0)
            vouchers.append(seen[r["voucher_no"]])
        v = seen[r["voucher_no"]]
        v["lines"].append(r)
        v["total_debit"] += r["debit"] or 0
        v["total_credit"] += r["credit"] or 0
    return tpl.TemplateResponse("ledger.html", dict(
        request=request, active="ledger", vouchers=vouchers, src=src))


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


# ---------- 庫存 --------------------------------------------
STOCK_IN_TYPES  = ["分裝入庫", "退貨入庫", "期初庫存"]
STOCK_ADJ_TYPES = ["盤點調整", "損耗報廢"]

@app.get("/stock", response_class=HTMLResponse)
def stock_page(request: Request):
    return tpl.TemplateResponse("stock.html", dict(
        request=request, active="stock",
        rows=Q.stock_on_hand(),
        products=q("SELECT product_id,sku,name FROM product WHERE status='在售' ORDER BY sku"),
        in_types=STOCK_IN_TYPES, adj_types=STOCK_ADJ_TYPES))

@app.get("/stock/value", response_class=HTMLResponse)
def stock_value_page(request: Request):
    today = dt.date.today()
    lo = request.query_params.get("from") or today.replace(day=1).isoformat()
    hi = request.query_params.get("to") or today.isoformat()
    return tpl.TemplateResponse("stock_value.html", dict(
        request=request, active="stock", lo=lo, hi=hi, rows=Q.stock_value_rows(lo, hi)))

@app.get("/stock/moves", response_class=HTMLResponse)
def stock_moves_page(request: Request):
    pid = request.query_params.get("product_id")
    return tpl.TemplateResponse("stock_moves.html", dict(
        request=request, active="stock",
        rows=Q.stock_moves(pid or None, request.query_params.get("type") or None),
        products=q("SELECT product_id,sku,name FROM product ORDER BY sku"),
        pid=pid or ""))

@app.post("/stock/move")
async def stock_move_add(request: Request):
    f = await request.form()
    try:
        pid = int(f.get("product_id"))
        qty = float(f.get("qty") or 0)
    except (TypeError, ValueError):
        return RedirectResponse("/stock", status_code=303)
    mtype = (f.get("move_type") or "分裝入庫").strip()
    if qty == 0:
        return RedirectResponse("/stock", status_code=303)
    # 入庫類一律記正,調整類依使用者填的正負,損耗報廢一律記負
    if mtype in ("分裝入庫", "退貨入庫", "期初庫存"):
        qty = abs(qty)
    elif mtype == "損耗報廢":
        qty = -abs(qty)
    execute("""INSERT INTO stock_move(move_date,product_id,qty,move_type,note)
               VALUES(?,?,?,?,?)""",
            (f.get("move_date") or dt.date.today().isoformat(), pid, qty, mtype,
             (f.get("note") or "").strip() or None))
    return RedirectResponse("/stock", status_code=303)

def _stock_move_delete(mid):
    """回傳 True=刪成功,False=被擋(這筆是訂單自動記的出貨/贈送出庫,不能單獨刪)。"""
    r = q("SELECT ref_order_id FROM stock_move WHERE move_id=?", (mid,))
    if not r or r[0]["ref_order_id"]:
        return False
    execute("DELETE FROM stock_move WHERE move_id=?", (mid,))
    ledger.delete_voucher_for("production_in", mid)
    return True

@app.post("/stock/moves/{mid}/delete")
async def stock_move_delete(request: Request, mid: int):
    ok = _stock_move_delete(mid)
    f = await request.form()
    if not ok:
        return RedirectResponse("/stock/moves?perr=1", status_code=303)
    return RedirectResponse(f.get("next") or "/stock/moves", status_code=303)


# ---------- 9 宮格 B 類:生產入庫(有價值,會記分錄)---------
@app.post("/stock/produce")
async def stock_produce(request: Request):
    f = await request.form()
    try:
        pid = int(f.get("product_id"))
        qty = abs(float(f.get("qty") or 0))
        amount = float(f.get("amount") or 0)
    except (TypeError, ValueError):
        return RedirectResponse("/stock", status_code=303)
    if qty <= 0 or amount <= 0:
        return RedirectResponse("/stock", status_code=303)
    date = (f.get("move_date") or dt.date.today().isoformat()).strip()
    note = (f.get("note") or "").strip() or None
    move_id = execute("""INSERT INTO stock_move(move_date,product_id,qty,move_type,note)
               VALUES(?,?,?,'生產入庫',?)""", (date, pid, qty, note))
    pg = q1("""SELECT pg.name pg_name FROM product p
               LEFT JOIN product_group pg ON pg.pg_id = p.product_group_id
               WHERE p.product_id=?""", (pid,))
    legs = ledger.compose_production_in_entries(pg.get("pg_name") if pg else None, amount)
    ledger.save_voucher("production_in", move_id, date, legs, "F", note=note)
    return RedirectResponse("/stock", status_code=303)


# 傳票勾選刪除:連來源紀錄一起刪(重用各來源自己的刪除邏輯,不繞過既有規則)
_LEDGER_DELETERS = {
    "purchase": _purchase_delete,
    "op_expense": _expense_delete,
    "order_sale": order_delete,
    "order_payment": order_delete,
    "production_in": _stock_move_delete,
    "other_income": _other_income_delete,
    "equity": _equity_delete,
    "manual_adjustment": _adjustment_delete,
    "sales_return": _return_delete,
}

@app.post("/ledger/delete")
async def ledger_delete(request: Request):
    f = await request.form()
    done = set()
    for v in f.getlist("voucher"):
        source_type, _, source_id = v.partition(":")
        key = (source_type, source_id)
        if key in done or source_type not in _LEDGER_DELETERS:
            continue
        done.add(key)
        _LEDGER_DELETERS[source_type](int(source_id))
    return RedirectResponse("/ledger", status_code=303)


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
