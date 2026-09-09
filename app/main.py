"""桂圓帳房 · 本機銷售平台(FastAPI 骨架)
啟動:  py -m uvicorn main:app --port 8000    或雙擊 啟動.bat
"""
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os, io, csv, json, glob, shutil, datetime as dt

import queries as Q
import db as _db
from db import q, execute

HERE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="桂圓帳房")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
tpl = Jinja2Templates(directory=os.path.join(HERE, "templates"))

def money(v):
    try: return f"{v:,.0f}"
    except Exception: return v
tpl.env.filters["money"] = money
tpl.env.filters["pct"] = lambda v: f"{v*100:.0f}%"


# ---------- 啟動時自動備份資料庫(保留最近 30 份) --------------
def backup_db():
    try:
        src = _db.DB
        if not os.path.exists(src):
            return
        bdir = os.path.join(HERE, "備份")
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

@app.get("/export/order_lines.csv")
def export_order_lines(request: Request):
    season = _season_arg(request)
    sc, sp = Q._S(season)
    rows = q(f"""SELECT o.order_no, o.order_date, o.season, o.order_kind,
                        cu.display_name, ch.name, p.sku, p.name,
                        ol.qty, ol.unit_price, ol.list_price, ol.line_subtotal,
                        CASE ol.is_gift WHEN 1 THEN '是' ELSE '' END, b.batch_code
                 FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id
                 JOIN product p ON p.product_id=ol.product_id
                 LEFT JOIN customer cu ON cu.customer_id=o.customer_id
                 LEFT JOIN channel ch ON ch.channel_id=o.channel_id
                 LEFT JOIN batch b ON b.batch_id=ol.batch_id
                 WHERE {sc} ORDER BY o.order_date, o.order_no, ol.line_id""", sp)
    return csv_response("訂單明細.csv",
        ["單號","日期","產季","種類","客戶","管道","商品編號","品名",
         "數量","成交單價","定價","小計","贈品","批次"],
        [list(r.values()) for r in rows])

@app.get("/export/orders.csv")
def export_orders(request: Request):
    season = _season_arg(request)
    sc, sp = Q._S(season)
    rows = q(f"""SELECT o.order_no, o.order_date, o.season, o.order_kind,
                        cu.display_name, ch.name,
                        o.discount_total, o.shipping_fee_charged, o.platform_fee, o.order_total,
                        o.payment_method, o.payment_status, o.paid_date,
                        o.ship_method, o.carrier, o.tracking_no, o.shipped_date, o.ship_status,
                        o.shipping_cost_actual, o.tax_doc_type
                 FROM "order" o
                 LEFT JOIN customer cu ON cu.customer_id=o.customer_id
                 LEFT JOIN channel ch ON ch.channel_id=o.channel_id
                 WHERE {sc} ORDER BY o.order_date, o.order_no""", sp)
    return csv_response("訂單.csv",
        ["單號","日期","產季","種類","客戶","管道","折扣","向客收運費","通路抽成","應收合計",
         "付款方式","收款狀態","收款日","出貨方式","物流商","物流單號","出貨日","出貨狀態",
         "我方運費","單據類型"],
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


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    season, asof, ctx = _season_ctx(request)
    k = Q.kpi(season, asof)
    mon = Q.monthly(season)
    monmax = max([m["rev"] for m in mon] + [1])
    fin = Q.finance_summary(season)
    prog = Q.season_progress(season)
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
                    agmax=max(list(buckets.values()) + [1]))
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
    groups = q("""SELECT g.pg_id, g.bu_id, g.name, g.tax_class, g.sort,
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
        bus=bus, groups=groups, skus=skus, unassigned=unassigned, tax_classes=TAX_CLASSES))

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
    tc = (f.get("tax_class") or "待確認").strip()
    if tc not in TAX_CLASSES:
        tc = "待確認"
    if gid:
        row = q("SELECT bu_id FROM product_group WHERE pg_id=?", (int(gid),))
        if not row:
            return RedirectResponse("/product-lines", status_code=303)
        dup = q("SELECT pg_id FROM product_group WHERE bu_id=? AND name=?", (row[0]["bu_id"], name))
        if name and (not dup or dup[0]["pg_id"] == int(gid)):
            execute("UPDATE product_group SET name=?, tax_class=? WHERE pg_id=?", (name, tc, int(gid)))
        else:
            execute("UPDATE product_group SET tax_class=? WHERE pg_id=?", (tc, int(gid)))
    else:
        bu_id = f.get("bu_id")
        if not (name and bu_id):
            return RedirectResponse("/product-lines", status_code=303)
        if not q("SELECT 1 FROM product_group WHERE bu_id=? AND name=?", (int(bu_id), name)):
            n = q("SELECT COALESCE(MAX(sort),0)+1 s FROM product_group")[0]["s"]
            execute("INSERT INTO product_group(bu_id,name,tax_class,sort) VALUES(?,?,?,?)",
                    (int(bu_id), name, tc, n))
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
               groups=q("SELECT pg_id, name FROM product_group ORDER BY sort, pg_id"),
               cats=SUP_CATS, docs=DOC_TYPES, today=dt.date.today().isoformat())

@app.get("/purchases", response_class=HTMLResponse)
def purchases_page(request: Request):
    pgv = request.query_params.get("pg") or ""
    ym = request.query_params.get("ym") or ""
    where, args = ["1=1"], []
    if pgv == "common":
        where.append("p.product_group_id IS NULL")
    elif pgv.isdigit():
        where.append("p.product_group_id=?"); args.append(int(pgv))
    if ym:
        where.append("substr(p.purchase_date,1,7)=?"); args.append(ym)
    rows = q(f"""SELECT p.*, s.name sup_name, g.name pg_name
                 FROM purchase p LEFT JOIN supplier s ON s.supplier_id=p.supplier_id
                 LEFT JOIN product_group g ON g.pg_id=p.product_group_id
                 WHERE {' AND '.join(where)}
                 ORDER BY p.purchase_date DESC, p.purchase_id DESC""", args)
    total = sum(r["amount"] for r in rows)
    tax_total = sum(r["tax_amount"] for r in rows if r["tax_deductible"])
    return tpl.TemplateResponse("purchases.html", dict(
        request=request, active="purchase", rows=rows, total=total, tax_total=tax_total,
        groups=q("SELECT pg_id, name FROM product_group ORDER BY sort, pg_id"),
        yms=[r["ym"] for r in q("SELECT DISTINCT substr(purchase_date,1,7) ym FROM purchase ORDER BY ym DESC")],
        pg_sel=pgv, ym_sel=ym))

@app.get("/purchases/new", response_class=HTMLResponse)
def purchase_new(request: Request):
    return tpl.TemplateResponse("purchase_form.html", _purchase_form_ctx(request, None))

@app.get("/purchases/{pid}/edit", response_class=HTMLResponse)
def purchase_edit(request: Request, pid: int):
    p = q("SELECT * FROM purchase WHERE purchase_id=?", (pid,))
    if not p:
        return RedirectResponse("/purchases", status_code=303)
    return tpl.TemplateResponse("purchase_form.html", _purchase_form_ctx(request, p[0]))

@app.post("/purchases")
async def purchase_save(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    fl = lambda k: float(f.get(k)) if (f.get(k) or "").strip() else 0.0
    pid = f.get("purchase_id")
    if f.get("_delete") and pid:
        execute("DELETE FROM purchase WHERE purchase_id=?", (int(pid),))
        return RedirectResponse("/purchases", status_code=303)
    date = (f.get("purchase_date") or "").strip()
    if not date:
        return RedirectResponse("/purchases", status_code=303)
    pgv = f.get("product_group_id") or ""
    cols = dict(
        purchase_date=date,
        supplier_id=(int(f.get("supplier_id")) if (f.get("supplier_id") or "").isdigit() else None),
        category=g("category"),
        product_group_id=(int(pgv) if pgv.isdigit() else None),
        amount=fl("amount"), tax_amount=fl("tax_amount"),
        tax_deductible=(1 if f.get("tax_deductible") else 0),
        doc_type=g("doc_type"),
        is_fixed_asset=(1 if f.get("is_fixed_asset") else 0),
        note=g("note"))
    if pid:
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE purchase SET {sets} WHERE purchase_id=:id", {**cols, "id": int(pid)})
    else:
        keys = ",".join(cols)
        execute(f"INSERT INTO purchase({keys}) VALUES({','.join(':' + k for k in cols)})", cols)
    return RedirectResponse("/purchases", status_code=303)


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
        rows=q("SELECT * FROM product ORDER BY status, sku")))

def _price_map(pid):
    rows = q("SELECT customer_segment, unit_price FROM price_list WHERE product_id=? AND channel_id IS NULL", (pid,))
    return {r["customer_segment"]: r["unit_price"] for r in rows}

@app.get("/products/new", response_class=HTMLResponse)
def product_new(request: Request):
    return tpl.TemplateResponse("product_form.html", dict(
        request=request, active="prod", p=None, prices={},
        segs=PRICE_SEGS, types=PROD_TYPES, forms=PKG_FORMS, uoms=UOMS, storage=STORAGE))

@app.get("/products/{pid}/edit", response_class=HTMLResponse)
def product_edit(request: Request, pid: int):
    p = q("SELECT * FROM product WHERE product_id=?", (pid,))
    if not p:
        return RedirectResponse("/products", status_code=303)
    return tpl.TemplateResponse("product_form.html", dict(
        request=request, active="prod", p=p[0], prices=_price_map(pid),
        segs=PRICE_SEGS, types=PROD_TYPES, forms=PKG_FORMS, uoms=UOMS, storage=STORAGE))

@app.post("/products")
async def product_save(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    ig = lambda k: int(f.get(k)) if (f.get(k) or "").strip() else None
    cols = dict(sku=(f.get("sku") or "").strip(), name=(f.get("name") or "").strip(),
                product_type=g("product_type"), type_code_raw=g("type_code_raw"),
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


# ---------- 批次 -------------------------------------------
@app.get("/batches", response_class=HTMLResponse)
def batches_page(request: Request):
    rows = Q.batch_report(None, "latest")
    rows.sort(key=lambda r: r["batch_code"], reverse=True)
    return tpl.TemplateResponse("batches.html", dict(request=request, active="batch", rows=rows))

@app.get("/batches/new", response_class=HTMLResponse)
def batch_new(request: Request):
    return tpl.TemplateResponse("batch_form.html", dict(
        request=request, active="batch", b=None,
        default_season=Q.season_of(dt.date.today().isoformat()),
        products=q("SELECT product_id,name FROM product ORDER BY name")))

@app.get("/batches/{bid}/edit", response_class=HTMLResponse)
def batch_edit(request: Request, bid: int):
    b = q("SELECT * FROM batch WHERE batch_id=?", (bid,))
    if not b:
        return RedirectResponse("/batches", status_code=303)
    return tpl.TemplateResponse("batch_form.html", dict(
        request=request, active="batch", b=b[0],
        products=q("SELECT product_id,name FROM product ORDER BY name")))

@app.post("/batches")
async def batch_save(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    fl = lambda k: float(f.get(k)) if (f.get(k) or "").strip() else None
    cols = dict(batch_code=(f.get("batch_code") or "").strip(),
                season=int(f.get("season") or Q.season_of(dt.date.today().isoformat())),
                product_id=(int(f.get("product_id")) if f.get("product_id") else None),
                roast_start=g("roast_start"), roast_end=g("roast_end"),
                raw_source=g("raw_source"), raw_input_kg=fl("raw_input_kg"),
                output_qty=fl("output_qty"), output_uom=(f.get("output_uom") or "份").strip(),
                mfg_date=g("mfg_date"), unit_cost=fl("unit_cost"), note=g("note"))
    bid = f.get("batch_id")
    if bid:
        sets = ",".join(f"{k}=:{k}" for k in cols)
        execute(f"UPDATE batch SET {sets} WHERE batch_id=:id", {**cols, "id": int(bid)})
    else:
        keys = ",".join(cols)
        execute(f"INSERT INTO batch({keys}) VALUES({','.join(':'+k for k in cols)})", cols)
    return RedirectResponse("/batches", status_code=303)


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

@app.get("/channels/{chid}/edit", response_class=HTMLResponse)
def channel_edit(request: Request, chid: int):
    c = q("SELECT * FROM channel WHERE channel_id=?", (chid,))
    if not c:
        return RedirectResponse("/channels", status_code=303)
    return tpl.TemplateResponse("channel_form.html", dict(request=request, active="chan", c=c[0], cats=CH_CATS))

@app.post("/channels")
async def channel_save(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    cols = dict(code=(f.get("code") or "").strip(), name=(f.get("name") or "").strip(),
                category=g("category"),
                commission_pct=float(f.get("commission_pct") or 0),
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
    return tpl.TemplateResponse("customer_form.html", dict(
        request=request, active="cust", cust=cust[0], addr=(addr[0] if addr else None),
        next=next or f"/customers/{cid}", dups=None,
        aliases=q("SELECT alias_text FROM customer_alias WHERE customer_id=? ORDER BY alias_id", (cid,)),
        channels=q("SELECT channel_id,name FROM channel ORDER BY channel_id"),
        types=CUST_TYPES, segs=CUST_SEGS, docs=DOC_PREFS))

@app.post("/customers")
async def customer_save(request: Request):
    f = await request.form()
    g = lambda k: (f.get(k) or "").strip() or None
    ch = int(f.get("primary_channel_id")) if f.get("primary_channel_id") else None
    name = (f.get("display_name") or "").strip()
    cid = f.get("customer_id")
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
        batches=q("SELECT batch_id,batch_code FROM batch ORDER BY batch_code"),
        today=dt.date.today().isoformat(),
        prices_json=json.dumps(prices), cust_seg_json=json.dumps(cust_seg),
        stock_json=json.dumps(Q.stock_on_hand_map()),
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
    ups   = f.getlist("unit_price"); bats = f.getlist("batch_id")
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
        b = bats[i] if i < len(bats) and bats[i] else None
        lines.append((pid, qv, up, int(b) if b else None, lp))

    discount_total = flt("discount_total")
    shipping_fee_charged = flt("shipping_fee_charged")
    subtotal = sum(qv * up for _, qv, up, _, _ in lines)
    # 每列的成交價已是實收價;discount_total 只放使用者另外填的整單折讓。
    # 「賣得比定價低」的差額改由 list_price 於報表即時計算,不重複扣。
    total = subtotal - discount_total + shipping_fee_charged
    n = q("SELECT COUNT(*) c FROM \"order\"")[0]["c"] + 1
    oid = execute("""INSERT INTO "order"(order_no,order_date,season,customer_id,channel_id,order_kind,
                     discount_total,shipping_fee_charged,order_total,payment_method,payment_status,
                     shipping_cost_actual,ship_method,ship_status)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, '待出貨')""",
                  (f"S{n:04d}", order_date, Q.season_of(order_date), customer_id, channel_id, order_kind,
                   discount_total, shipping_fee_charged, total,
                   one("payment_method") or None, one("payment_status", "待收款"),
                   flt("shipping_cost_actual"), one("ship_method") or None))
    for p, qv, up, b, lp in lines:
        execute("""INSERT INTO order_line(order_id,product_id,batch_id,qty,unit_price,list_price,line_subtotal)
                   VALUES(?,?,?,?,?,?,?)""", (oid, p, b, qv, up, lp, qv * up))
    sync_order_stock(oid)
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


# ---------- 訂單管理:清單 / 明細 / 更新 -----------------------
PAY_METHODS  = ['現金', '銀行匯款', '貨到付款', '行動支付', '信用卡', '平台代收', '未收款']
PAY_STATUS   = ['待收款', '部分收款', '已收款', '免收款']
SHIP_METHODS = ['自行配送', '客戶自取', '宅配', '超商店到店', '超商賣貨便', '冷藏宅配']
SHIP_STATUS  = ['待出貨', '已出貨', '已送達', '退回', '遺失', '破損']
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
        batches=q("SELECT batch_id,batch_code FROM batch ORDER BY batch_code"),
        pay_methods=PAY_METHODS, pay_status=PAY_STATUS,
        ship_methods=SHIP_METHODS, ship_status=SHIP_STATUS, kinds=ORDER_KINDS))

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
    ups, bats, gl = f.getlist("unit_price"), f.getlist("batch_id"), f.getlist("is_gift")
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
        b = bats[i] if i < len(bats) and bats[i] else None
        is_gift = 1 if (i < len(gl) and gl[i] == "是") else 0
        new_lines.append((int(p), qv, up if not is_gift else 0,
                          int(b) if b else None, is_gift))
    execute("DELETE FROM order_line WHERE order_id=?", (oid,))
    for p, qv, up, b, gf in new_lines:
        execute("""INSERT INTO order_line(order_id,product_id,batch_id,qty,unit_price,line_subtotal,is_gift)
                   VALUES(?,?,?,?,?,?,?)""", (oid, p, b, qv, up, qv * up, gf))

    subtotal = sum(qv * up for _, qv, up, _, gf in new_lines if not gf)
    disc = flt("discount_total"); fee = flt("shipping_fee_charged")
    total = subtotal - disc + fee

    cust_id = f.get("customer_id")
    cols = dict(order_date=g("order_date"), order_kind=g("order_kind"),
                payment_method=g("payment_method"), payment_status=g("payment_status") or "待收款",
                paid_date=g("paid_date"), paid_amount=(flt("paid_amount") or None),
                discount_total=disc, shipping_fee_charged=fee, order_total=total,
                ship_method=g("ship_method"), carrier=g("carrier"), tracking_no=g("tracking_no"),
                shipped_date=g("shipped_date"), delivered_date=g("delivered_date"),
                ship_status=g("ship_status") or "待出貨",
                shipping_cost_actual=flt("shipping_cost_actual"), note=g("note"))
    if cust_id and cust_id.isdigit():
        cols["customer_id"] = int(cust_id)
    if cols.get("order_date"):
        cols["season"] = Q.season_of(cols["order_date"])
    sets = ",".join(f'{k}=:{k}' for k in cols)
    execute(f'UPDATE "order" SET {sets} WHERE order_id=:id', {**cols, "id": oid})
    sync_order_stock(oid)
    return RedirectResponse(f"/orders/{oid}?ok=1", status_code=303)

@app.post("/orders/{oid}/copy")
def order_copy(oid: int):
    o, lines = Q.order_get(oid)
    if not o:
        return RedirectResponse("/orders", status_code=303)
    n = q("SELECT COUNT(*) c FROM \"order\"")[0]["c"] + 1
    noid = execute("""INSERT INTO "order"(order_no,order_date,season,customer_id,channel_id,order_kind,
                      order_total,payment_method,payment_status,ship_method,ship_status,note)
                      VALUES(?,?,?,?,?,?,?,?, '待收款', ?, '待出貨', ?)""",
                   (f"S{n:04d}", dt.date.today().isoformat(), o["season"], o["customer_id"],
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
    return RedirectResponse(f"/orders/{noid}?ok=1", status_code=303)

@app.post("/orders/{oid}/paid")
def order_mark_paid(oid: int):
    o, _ = Q.order_get(oid)
    if o:
        execute("""UPDATE "order" SET payment_status='已收款', paid_date=?, paid_amount=?
                   WHERE order_id=?""", (dt.date.today().isoformat(), o["order_total"], oid))
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
        n = q("SELECT COUNT(*) c FROM \"order\"")[0]["c"] + 1
        noid = execute("""INSERT INTO "order"(order_no,order_date,season,customer_id,channel_id,
                          order_kind,order_total,payment_method,payment_status,
                          ship_method,ship_status,note)
                          VALUES(?,?,?,?,?, '理賠重寄', 0, '未收款', '免收款',
                                 '自行配送', '待出貨', ?)""",
                       (f"S{n:04d}", dt.date.today().isoformat(), o["season"], o["customer_id"],
                        o["channel_id"], f"由 {o['order_no']} 的{si['issue_type']}理賠重寄"))
        for l in lines:
            execute("""INSERT INTO order_line(order_id,product_id,batch_id,qty,unit_price,line_subtotal,is_gift)
                       VALUES(?,?,?,?,0,0,1)""", (noid, l["product_id"], l["batch_id"], l["qty"]))
        execute("UPDATE shipment_issue SET linked_reship_order_id=? WHERE issue_id=?", (noid, iid))
        sync_order_stock(noid)
    return RedirectResponse("/issues", status_code=303)


# ---------- 財務健康:月損益 ----------------------------------
@app.get("/finance", response_class=HTMLResponse)
def finance_page(request: Request):
    fin = []
    for ym in Q.months_with_data():
        d = Q.finance_month(ym)
        fin.append(dict(ym=ym, revenue=d["revenue"], cogs=d["cogs"],
                        gross_profit=d["gross_profit"], platform_fee=d["platform_fee"],
                        ship_pnl=d["ship_pnl"], opex_total=d["opex_total"], pretax=d["pretax"],
                        opex={r["category"]: r["amt"] for r in d["opex"]}))
    return tpl.TemplateResponse("finance.html", dict(
        request=request, active="finance",
        fin_json=json.dumps(fin), cats=Q.OPEX_CATS,
        seasons_fin=[Q.season_finance(s) for s in Q.seasons()]))

@app.get("/finance/expenses", response_class=HTMLResponse)
def finance_expenses(request: Request):
    months = Q.months_with_data()
    return tpl.TemplateResponse("finance_expenses.html", dict(
        request=request, active="finance", cats=Q.OPEX_CATS,
        ym_default=request.query_params.get("ym") or (months[-1] if months else dt.date.today().strftime("%Y-%m")),
        rows=q("SELECT * FROM op_expense ORDER BY ym DESC, category")))

@app.post("/finance/expenses")
async def finance_expense_save(request: Request):
    f = await request.form()
    eid = f.get("expense_id")
    if f.get("_delete") and eid:
        execute("DELETE FROM op_expense WHERE expense_id=?", (int(eid),))
        return RedirectResponse("/finance/expenses", status_code=303)
    ym = (f.get("ym") or "").strip()
    cat = (f.get("category") or "").strip()
    try:
        amt = float(f.get("amount") or 0)
    except ValueError:
        amt = 0.0
    note = (f.get("note") or "").strip() or None
    if not (ym and cat):
        return RedirectResponse("/finance/expenses", status_code=303)
    if eid:
        execute("UPDATE op_expense SET ym=?,category=?,amount=?,note=? WHERE expense_id=?",
                (ym, cat, amt, note, int(eid)))
    else:
        execute("INSERT INTO op_expense(ym,category,amount,note) VALUES(?,?,?,?)", (ym, cat, amt, note))
    return RedirectResponse("/finance/expenses", status_code=303)


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
        batches=q("SELECT batch_id,batch_code FROM batch ORDER BY batch_code DESC"),
        in_types=STOCK_IN_TYPES, adj_types=STOCK_ADJ_TYPES))

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
    bid = f.get("batch_id")
    bid = int(bid) if bid and bid.isdigit() else None
    if qty == 0:
        return RedirectResponse("/stock", status_code=303)
    # 入庫類一律記正,調整類依使用者填的正負,損耗報廢一律記負
    if mtype in ("分裝入庫", "退貨入庫", "期初庫存"):
        qty = abs(qty)
    elif mtype == "損耗報廢":
        qty = -abs(qty)
    execute("""INSERT INTO stock_move(move_date,product_id,batch_id,qty,move_type,note)
               VALUES(?,?,?,?,?,?)""",
            (f.get("move_date") or dt.date.today().isoformat(), pid, bid, qty, mtype,
             (f.get("note") or "").strip() or None))
    return RedirectResponse("/stock", status_code=303)


# ---------- 出貨作業:揀貨單 / 標籤 / 食品標示 ---------------
SENDER = {"name": "郡碩農創", "place": "南投縣中寮鄉", "addr": "[寄件地址]", "phone": "[寄件電話]"}

@app.get("/shipping", response_class=HTMLResponse)
def shipping_hub(request: Request):
    pending = q("SELECT COUNT(*) n FROM \"order\" WHERE ship_status='待出貨' AND order_kind<>'內部領用'")[0]["n"]
    dates = [r["d"] for r in q("""SELECT DISTINCT order_date d FROM "order"
                                  WHERE ship_status='待出貨' ORDER BY d DESC LIMIT 20""")]
    return tpl.TemplateResponse("shipping.html", dict(
        request=request, active="ship", pending=pending, dates=dates,
        products=q("SELECT product_id,sku,name FROM product ORDER BY sku"),
        batches=q("SELECT batch_id,batch_code FROM batch ORDER BY batch_code DESC")))

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
    bid = request.query_params.get("batch_id")
    try:
        copies = max(1, min(60, int(request.query_params.get("copies", 8))))
    except ValueError:
        copies = 8
    data = Q.food_label(int(pid), int(bid) if bid else None) if pid else None
    return tpl.TemplateResponse("foodlabel.html", dict(
        request=request, active="ship", data=data, copies=copies, sender=SENDER,
        products=q("SELECT product_id,sku,name FROM product ORDER BY sku"),
        batches=q("SELECT batch_id,batch_code FROM batch ORDER BY batch_code DESC"),
        pid=pid or "", bid=bid or ""))
