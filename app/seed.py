"""建立 guiyuan_ledger.db:套用 schema.sql 並灌入「精簡示範資料」。

原則:每個功能給 1~2 筆有代表性的資料就好,數字都用整齊好對的整數,
方便邊測功能邊驗證。不做大量隨機訂單。

用法:  py seed.py          (存在則詢問覆蓋)
       py seed.py --force  (直接重建)
"""
import sqlite3, os, sys, datetime as dt
import ledger

try:
    import paths
    DB, SQL = paths.DB_PATH, paths.SCHEMA_PATH
except Exception:
    HERE = os.path.dirname(os.path.abspath(__file__))
    DB   = os.path.join(HERE, "guiyuan_ledger.db")
    SQL  = os.path.join(HERE, "schema.sql")

if os.path.exists(DB):
    if "--force" not in sys.argv:
        ans = input(f"{DB} 已存在,要重建嗎? (y/N) ").strip().lower()
        if ans != "y":
            print("取消。"); sys.exit(0)
    os.remove(DB)

cx = sqlite3.connect(DB)
cx.executescript(open(SQL, encoding="utf-8").read())
c = cx.cursor()

# ---- 總帳分錄小工具(方案B試點:只給有代表性的幾筆示範資料寫分錄,-----
#      不是每月 108 筆營運費用都寫,那樣 /ledger 會塞爆,失去示範的意義)----
_voucher_seq = {}
def add_voucher(prefix, date, legs, source_type, source_id, note=None):
    if not legs:
        return
    key = (prefix, date.replace("-", ""))
    _voucher_seq[key] = _voucher_seq.get(key, 0) + 1
    vno = f"{prefix}{key[1]}-{_voucher_seq[key]:03d}"
    for leg in legs:
        c.execute("""INSERT INTO ledger_entry(voucher_no,entry_date,account_code,account_name,
                     debit,credit,source_type,source_id,note) VALUES(?,?,?,?,?,?,?,?,?)""",
                  (vno, date, leg["account_code"], leg["account_name"],
                   leg["debit"], leg["credit"], source_type, source_id, note))

# ---- 產品線:事業別 → 產品群組 -----------------------------------
bu = {}
for name, srt in [("龍眼", 1), ("蜂蜜", 2)]:
    c.execute("INSERT INTO business_unit(name,sort) VALUES(?,?)", (name, srt))
    bu[name] = c.lastrowid
pg = {}
for i, (gname, buname) in enumerate(
        [("龍眼鮮果", "龍眼"), ("龍眼乾", "龍眼"), ("龍眼肉", "龍眼"), ("蜂蜜", "蜂蜜")]):
    c.execute("INSERT INTO product_group(bu_id,name,sort) VALUES(?,?,?)", (bu[buname], gname, i))
    pg[gname] = c.lastrowid

# ---- 通路(3 個:官網 / LINE 社群〔有抽成〕/ 批發〔有結算天數〕)----
channels = [
    ("WEB",  "官網",      "官網",     0.0,  0),
    ("LINE", "LINE 社群", "LINE社群", 0.05, 7),
    ("WS",   "批發",      "批發",     0.0, 30),
]
c.executemany("INSERT INTO channel(code,name,category,commission_pct,settlement_lag_days) VALUES(?,?,?,?,?)", channels)
ch = {row[1]: i + 1 for i, row in enumerate(channels)}

# ---- 商品(每條產品線 1 個代表 + 1 個禮盒 + 1 個停售 + 1 個贈品)----
# sku, name, type, code_raw, net_g, gross_g, form, uom, g/uom, shelf, gift_only, retail, wholesale, group, unit_cost_hint
products = [
    ("GY-DRY-300",     "桂圓乾 300g",        "單品",     "#26", 300,  360, "夾鏈袋", "包", 300, 180, 0, 175, 140,  "龍眼乾",   100),
    ("GY-GIFT",        "桂圓乾 禮盒(300g×2)", "禮盒",     "#27", 600,  900, "禮盒",   "盒", 600, 180, 0, 350, 300,  "龍眼乾",   210),
    ("GY-DRY-500",     "桂圓乾 500g(停售)",  "單品",     "#28", 500,  580, "夾鏈袋", "包", 500, 180, 0, 300, 250,  "龍眼乾",   160),
    ("LG-MEAT-600",    "龍眼肉 600g 罐",     "單品",     None,  600,  700, "罐",     "罐", 600, 365, 0, 600, 500,  "龍眼肉",   340),
    ("HNY-LONGAN-420", "龍眼蜂蜜 420g",      "單品",     None,  420,  620, "玻璃罐", "罐", 420, 730, 0, 420, 360,  "蜂蜜",     150),
    ("GY-FRESH-TCHIN", "龍眼鮮果 台斤",      "裸裝",     None,  None, None, "裸裝",   "斤", 600,  10, 0,  80,  60,  "龍眼鮮果",  35),
    ("GX-STICK",       "桂圓棒",             "加購贈品", None,   40,   50, "夾鏈袋", "支",  40, 150, 1,   0,   0,  "龍眼乾",    18),
]
prod = {}
for p in products:
    sku = p[0]
    low = {"GY-DRY-300": 60, "GY-GIFT": 20}.get(sku)
    ingredients = "蜂蜜(南投中寮)" if sku.startswith("HNY") else "龍眼(南投中寮)"
    status = "停售" if sku == "GY-DRY-500" else "在售"
    c.execute("""INSERT INTO product(sku,name,product_type,type_code_raw,net_weight_g,gross_weight_g,
                 package_form,uom,grams_per_uom,shelf_life_days,gift_only,ingredients,origin,status,low_stock,
                 product_group_id,unit_cost)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9], p[10],
               ingredients, "南投中寮", status, low, pg[p[13]], p[14]))
    pid = c.lastrowid
    prod[sku] = dict(id=pid, retail=p[11], wholesale=p[12], unit_cost=p[14], uom=p[7], pg_name=p[13])
    for seg, price in (("零售", p[11]), ("批發", p[12]), ("團購", round(p[11] * 0.95)),
                       ("機構", p[11]), ("內部", 0)):
        c.execute("INSERT INTO price_list(product_id,customer_segment,unit_price) VALUES(?,?,?)",
                  (pid, seg, price))

# ---- 批次(2 個:上一季 2025-A〔已過期〕/ 本季 2026-A〔快到期〕)----
batches = [
    ("2025-A", 2025, "2025-09-10", "2025-09-24", 500, "2025-09-24"),
    ("2026-A", 2026, "2026-04-20", "2026-05-02", 500, "2026-05-02"),
]
bat = {}
for code, bseason, rs, re_, out, mfg in batches:
    c.execute("""INSERT INTO batch(batch_code,season,roast_start,roast_end,raw_source,
                 raw_input_kg,output_qty,output_uom,mfg_date,unit_cost)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (code, bseason, rs, re_, "自園 + 收購", out * 3.2, out, "份", mfg, 110))
    bat[code] = c.lastrowid

# ---- 客戶(4 個:批發通路商 / 團購主 / 零售 / 公關對象)----
customers = [
    ("一品園有機商店", "通路商",  "批發",   "批發",     ["一品園", "游念慈(一品園)"]),
    ("張宜彤",         "個人",    "團購主", "官網",     ["張宜彤/育成村客人"]),
    ("莊麗芳",         "個人",    "零售",   "LINE 社群", ["莊麗芳/台灣亮起來客人"]),
    ("今周刊",         "公司",    "公關對象", "官網",   ["今周刊拍攝"]),
]
cust = {}
for name, ctype, seg, chan, aliases in customers:
    c.execute("""INSERT INTO customer(display_name,customer_type,segment,primary_channel_id,tax_doc_pref)
                 VALUES(?,?,?,?,?)""",
              (name, ctype, seg, ch[chan], "農民收據" if seg in ("批發", "機構") else "免開立"))
    cid = c.lastrowid
    cust[name] = dict(id=cid, seg=seg, chan=chan)
    c.execute("INSERT INTO customer_alias(customer_id,alias_text) VALUES(?,?)", (cid, name))
    for a in aliases:
        c.execute("INSERT INTO customer_alias(customer_id,alias_text) VALUES(?,?)", (cid, a))
    c.execute("""INSERT INTO address(customer_id,label,recipient_name,is_default,address_full)
                 VALUES(?,?,?,1,?)""", (cid, "預設", name, "(示範地址)"))

seg_to_priceseg = {"批發": "批發", "機構": "機構", "團購主": "團購", "零售": "零售", "公關對象": "內部"}

def price_of(sku, seg):
    if seg == "批發":
        return prod[sku]["wholesale"]
    if seg == "團購":
        return round(prod[sku]["retail"] * 0.95)
    if seg == "內部":
        return 0
    return prod[sku]["retail"]

# ---- 訂單(手工 10 筆,涵蓋各種狀況)----------------------------
order_no = 0

def make_order(d, cname, kind, lines, ship_method, pay_status, ship_status=None, season=2026):
    global order_no
    order_no += 1
    ci = cust[cname]
    cid, chan = ci["id"], ci["chan"]
    seg = seg_to_priceseg.get(ci["seg"], "零售")
    subtotal = sum(qty * (0 if (gift or kind != "銷售") else price_of(sku, seg))
                   for sku, qty, gift in lines)
    ship_cost = 0
    carrier = None
    if ship_method in ("宅配", "超商店到店", "冷藏宅配"):
        carrier = "黑貓"
        ship_cost = 70
    total = subtotal
    pm = {"批發": "銀行匯款", "機構": "銀行匯款"}.get(ci["seg"], "現金")
    if kind != "銷售":
        pm, pay_status, total = "未收款", "免收款", 0
    if ship_status is None:
        ship_status = "已送達" if pay_status == "已收款" else "已出貨"
    invoiced = 1 if ci["seg"] in ("批發", "機構") else 0
    paid_amount = total if pay_status == "已收款" else (round(total * 0.5) if pay_status == "部分收款" else None)
    c.execute("""INSERT INTO "order"(order_no,order_date,season,customer_id,channel_id,order_kind,
                 source_ref,discount_total,order_total,
                 payment_method,payment_account,payment_status,paid_date,paid_amount,
                 invoiced,ship_method,carrier,shipping_cost_actual,ship_status)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (f"S{order_no:04d}", d.isoformat(), season, cid, ch[chan], kind,
               None, 0, total,
               pm, "郵局" if pm == "銀行匯款" else None, pay_status,
               d.isoformat() if pay_status == "已收款" else None, paid_amount,
               invoiced, ship_method, carrier, ship_cost, ship_status))
    oid = c.lastrowid
    bcode = f"{season}-A"
    for sku, qty, gift in lines:
        tier = 0 if (gift or kind != "銷售") else price_of(sku, seg)
        sub = 0 if gift else round(qty * tier)
        bid = None if sku.startswith(("HNY", "GY-FRESH")) else bat[bcode]
        c.execute("""INSERT INTO order_line(order_id,product_id,batch_id,qty,unit_price,list_price,
                     line_discount,line_subtotal,is_gift) VALUES(?,?,?,?,?,?,?,?,?)""",
                  (oid, prod[sku]["id"], bid, qty, tier, tier, 0, sub, 1 if gift else 0))

    # 記帳:訂單成立分錄 + 收款分錄(邏輯跟 main.py 的 _post_order_ledger 一致)
    sale_lines = [(prod[sku]["pg_name"], 0 if gift else round(qty * price_of(sku, seg)),
                   qty * prod[sku]["unit_cost"])
                  for sku, qty, gift in lines if not gift]
    sale_legs = ledger.compose_order_sale_entries(sale_lines, kind)
    add_voucher("S", d.isoformat(), sale_legs, "order_sale", oid, f"訂單 S{order_no:04d}")
    pay_legs = ledger.compose_order_payment_entries(paid_amount, "郵局" if pm == "銀行匯款" else None)
    add_voucher("R", d.isoformat(), pay_legs, "order_payment", oid, f"訂單 S{order_no:04d} 收款")
    return oid

# d, 客戶, 種類, 明細[(sku,數量,贈品)], 出貨方式, 收款狀態, 出貨狀態, 產季
ORDERS = [
    (dt.date(2026, 5, 6),  "張宜彤",         "銷售",     [("GY-DRY-300", 4, False)],                       "宅配",     "已收款", None,   2026),
    (dt.date(2026, 6, 10), "一品園有機商店", "銷售",     [("GY-DRY-300", 20, False), ("GY-GIFT", 10, False)], "自行配送", "已收款", None,   2026),
    (dt.date(2026, 6, 15), "莊麗芳",         "銷售",     [("HNY-LONGAN-420", 3, False)],                   "超商店到店", "已收款", None,   2026),
    (dt.date(2026, 6, 20), "莊麗芳",         "銷售",     [("LG-MEAT-600", 2, False)],                      "宅配",     "待收款", "已送達", 2026),  # → 貨款逾期未收
    (dt.date(2026, 9, 2),  "張宜彤",         "銷售",     [("GY-GIFT", 2, False)],                          "宅配",     "待收款", "待出貨", 2026),  # → 超過 3 天沒出貨
    (dt.date(2026, 7, 1),  "今周刊",         "贈送-公關", [("GY-DRY-300", 2, False)],                      "自行配送", "免收款", None,   2026),
    (dt.date(2026, 5, 20), "莊麗芳",         "銷售",     [("GY-DRY-300", 3, False)],                       "宅配",     "已收款", None,   2026),  # → 掛破損異常
    (dt.date(2026, 8, 10), "一品園有機商店", "銷售",     [("LG-MEAT-600", 6, False)],                      "自行配送", "已收款", None,   2026),
    (dt.date(2025, 11, 15), "張宜彤",        "銷售",     [("GY-DRY-300", 5, False), ("GX-STICK", 1, True)], "宅配",     "已收款", None,   2025),
    (dt.date(2025, 12, 20), "一品園有機商店", "銷售",    [("GY-GIFT", 8, False)],                          "自行配送", "已收款", None,   2025),
]
oids = [make_order(*row) for row in ORDERS]

# ---- 物流異常(1 筆未處理的破損)-------------------------------
issue_oid = oids[6]      # 2026-05-20 莊麗芳那筆
c.execute("""INSERT INTO shipment_issue(order_id,issue_type,issue_date,qty_affected,
             resolution,cost_impact,reason_note) VALUES(?,?,?,?,?,?,?)""",
          (issue_oid, "破損", "2026-05-22", 1, None, 350, "黑貓 · 示範資料"))
c.execute("UPDATE \"order\" SET ship_status='破損' WHERE order_id=?", (issue_oid,))

# ---- 銷貨退回 / 折讓(v2 回合 8 示範:一筆折讓、一筆退貨進庫)----
#      技術債 #2(2026-09-21):兩筆也補上總帳分錄,跟其他示範資料一樣的做法。
gift_pid = prod["GY-GIFT"]["id"]
b2025 = bat["2025-A"]

def _return_ledger(rid, oid, date, kind, amount, pid=None, qty=None, restock=0):
    o = c.execute('SELECT payment_status, payment_account FROM "order" WHERE order_id=?', (oid,)).fetchone()
    already_paid = o[0] == "已收款"
    cogs_amount, pgn = 0, None
    if restock and pid and qty:
        pr = c.execute("""SELECT p.unit_cost, pg.name pg_name FROM product p
                           LEFT JOIN product_group pg ON pg.pg_id=p.product_group_id
                           WHERE p.product_id=?""", (pid,)).fetchone()
        cogs_amount = abs(qty) * (pr[0] or 0)
        pgn = pr[1]
    legs = ledger.compose_sales_return_entries(kind, amount, already_paid, o[1],
                                                cogs_amount, pgn)
    add_voucher("T", date, legs, "sales_return", rid, f"{kind}:訂單#{oid}")

c.execute("""INSERT INTO sales_return(order_id,return_date,season,kind,amount,restock,reason)
             VALUES(?,?,?,?,?,?,?)""",
          (issue_oid, "2026-05-25", 2026, "折讓", 150, 0, "破損客訴,折讓不退貨"))
_sr1 = c.lastrowid
_return_ledger(_sr1, issue_oid, "2026-05-25", "折讓", 150)

c.execute("""INSERT INTO sales_return(order_id,return_date,season,kind,amount,product_id,qty,batch_id,restock,reason)
             VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (oids[9], "2026-01-05", 2025, "退貨", 600, gift_pid, 2, b2025, 1, "客戶多訂,退 2 盒"))
_sr2 = c.lastrowid
c.execute("""INSERT INTO stock_move(move_date,product_id,batch_id,qty,move_type,ref_order_id,note)
             VALUES(?,?,?,?,?,?,?)""",
          ("2026-01-05", gift_pid, b2025, 2, "退貨入庫", oids[9], f"退貨單#{_sr2}"))
_return_ledger(_sr2, oids[9], "2026-01-05", "退貨", 600, gift_pid, 2, 1)

# ---- 產季目標 --------------------------------------------------
c.execute("INSERT INTO sales_target(season,product_id,target_qty,target_amount) VALUES(2025,NULL,NULL,300000)")
c.execute("INSERT INTO sales_target(season,product_id,target_qty,target_amount) VALUES(2026,NULL,NULL,400000)")

# ---- 待確認佇列(1 筆)---------------------------------------
c.execute("""INSERT INTO review_queue(source,issue_type,note)
             VALUES('form','客戶未分級','新客戶「王小明」尚未指定分級')""")

# ---- 營運費用:2025-04 ~ 2026-09,每月固定整數(好對帳)-------
opex_months = []
_y, _m = 2025, 4
while (_y, _m) <= (2026, 9):
    opex_months.append(f"{_y}-{_m:02d}")
    _m += 1
    if _m > 12:
        _m = 1; _y += 1

def opx(ym, cat, amt, pgname=None, amort=None):
    c.execute("""INSERT INTO op_expense(ym,category,amount,product_group_id,amortize_months,payment_account)
                 VALUES(?,?,?,?,?,?)""", (ym, cat, amt, pg.get(pgname), amort, "現金"))
    return c.lastrowid

for ym in opex_months:
    mm = int(ym[5:7])
    opx(ym, "人事", 10000)
    opx(ym, "場地・倉儲", 2000)
    opx(ym, "行銷", 3000)
    opx(ym, "金流手續費", 1000)
    opx(ym, "其他", 1000)
    opx(ym, "田間管理", 5000, "龍眼乾")
    if mm in (7, 8, 9):                     # 採收焙製剝肉,直接人工多
        opx(ym, "直接人工", 15000, "龍眼乾")
        opx(ym, "直接人工", 8000, "龍眼肉")
        opx(ym, "直接人工", 3000, "蜂蜜")
rnd_id = opx("2025-07", "研發", 120000, None, 24)     # 研發費 12 萬,分 24 個月攤
add_voucher("E", "2025-07-01", ledger.compose_expense_entries("研發", 120000, 0, "現金"),
            "op_expense", rnd_id, "營運費用:研發")
cert_id = opx("2026-06", "驗證費", 15000, "龍眼乾")     # 年度產銷履歷驗證費
add_voucher("E", "2026-06-01", ledger.compose_expense_entries("驗證費", 15000, 0, "現金"),
            "op_expense", cert_id, "營運費用:驗證費")

# ---- 供應商(2)+ 進貨單(3,其中 1 筆設備待建卡)------------
suppliers = [
    ("中寮果農合作社", "53912888", "原料", "049-2601234"),
    ("永信包裝材料行", "27889011", "包材", "04-23015678"),
]
sup = {}
for name, tid, cat, phone in suppliers:
    c.execute("INSERT INTO supplier(name,tax_id,category,phone) VALUES(?,?,?,?)", (name, tid, cat, phone))
    sup[name] = c.lastrowid

# 日期, 供應商, 類別, 產品線, 未稅額, 稅額, 可扣抵, 憑證, 固定資產, 備註
purchases = [
    ("2026-05-10", "中寮果農合作社", "原料", "龍眼乾", 60000,    0, 0, "農民收據",   0, "鮮果收購 · 2026 產季"),
    ("2026-05-15", "永信包裝材料行", "包材", "龍眼乾",  8000,  400, 1, "三聯式發票", 0, "夾鏈袋 + 禮盒盒一批"),
    ("2026-04-20", "永信包裝材料行", "設備", "蜂蜜",   20000, 1000, 1, "三聯式發票", 1, "搖蜜機(固定資產,待建卡)"),
]
buy_id = {}
for d, sname, cat, pgname, amt, tax, ded, doc, fa, note in purchases:
    c.execute("""INSERT INTO purchase(purchase_date,supplier_id,category,product_group_id,
                 amount,tax_amount,tax_deductible,doc_type,is_fixed_asset,payment_account,note)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (d, sup[sname], cat, pg.get(pgname), amt, tax, ded, doc, fa, "現金", note))
    pur_id = c.lastrowid
    buy_id[note] = pur_id
    legs = ledger.compose_purchase_entries(cat, amt, tax, "現金", is_fixed_asset=bool(fa))
    add_voucher("P", d, legs, "purchase", pur_id, f"進貨:{cat}")

# ---- 固定資產卡(2:柴焙灶〔有政府補助〕/ 搖蜜機〔由進貨帶入〕)----
# 名稱, 類別, 產品線, 取得日, 成本, 補助, 殘值 None=自動, 年數, 來源進貨 note
assets = [
    ("智慧柴焙灶(含溫控)", "機器設備", "龍眼乾", "2025-08-01", 200000, 80000, None, 5, None),
    ("搖蜜機",             "機器設備", "蜂蜜",   "2026-04-20",  20000,     0, None, 5, "搖蜜機(固定資產,待建卡)"),
]
for name, cat, pgname, adate, cost, grant, sv, life, src_note in assets:
    salvage = sv if sv is not None else round(max(0, cost - grant) / (life + 1))
    c.execute("""INSERT INTO fixed_asset(name,category,product_group_id,acquire_date,cost,
                 grant_amount,salvage,life_years,method,source_purchase_id,payment_account,note)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (name, cat, pg.get(pgname), adate, cost, grant, salvage, life, "平均法",
               buy_id.get(src_note), "現金", None))
    asset_id = c.lastrowid
    legs = ledger.compose_asset_acquire_entries(cat, cost, grant, "現金")
    add_voucher("K", adate, legs, "asset_acquire", asset_id, f"設備取得:{name}")
    # 折舊分錄留給 /assets 頁第一次打開時自動補上(sync_depreciation_vouchers),
    # 這裡不預先寫,才是走跟真實使用一樣的路徑

# ---- 庫存異動:每產季分裝一批(A 批),再依訂單出庫 ----------
import math
sku_by_id = {v["id"]: k for k, v in prod.items()}
sold_by_season = {}
for pid, season, qty in c.execute("""
        SELECT ol.product_id, o.season, ol.qty
        FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id""").fetchall():
    sold_by_season[(pid, season)] = sold_by_season.get((pid, season), 0) + (qty or 0)
for (pid, season), q_sold in sold_by_season.items():
    a_batch = c.execute("SELECT batch_id, mfg_date FROM batch WHERE batch_code=?",
                        (f"{season}-A",)).fetchone()
    if not a_batch:
        continue
    sk = sku_by_id.get(pid, "")
    is_nonbatch = sk.startswith(("HNY", "GY-FRESH"))
    # 每項都備 20% 餘量(不做超賣);GY-DRY-300 / GY-GIFT 的 low_stock 門檻較高,
    # 餘量會落在門檻下 → 觸發「庫存偏低」警示,但不會是負的。
    factor = 1.2
    c.execute("""INSERT INTO stock_move(move_date,product_id,batch_id,qty,move_type,note)
                 VALUES(?,?,?,?,?,?)""",
              (a_batch[1], pid, (None if is_nonbatch else a_batch[0]), math.ceil(q_sold * factor),
               ("分裝入庫" if not is_nonbatch else "期初庫存"),
               f"示範:{season} 產季{'分裝' if not is_nonbatch else '進貨入庫'}"))
for oid, kind, odate in c.execute(
        'SELECT order_id, order_kind, order_date FROM "order"').fetchall():
    mtype = "銷售出庫" if kind == "銷售" else "贈送出庫"
    for pid, bid, qty in c.execute(
            "SELECT product_id, batch_id, qty FROM order_line WHERE order_id=?", (oid,)).fetchall():
        c.execute("""INSERT INTO stock_move(move_date,product_id,batch_id,qty,move_type,ref_order_id)
                     VALUES(?,?,?,?,?,?)""", (odate, pid, bid, -abs(qty or 0), mtype, oid))

# ---- 9 宮格 B 類示範(各給 1 筆,讓同事一打開就看得到長相)--------
prod_id = prod["GY-DRY-300"]["id"]
c.execute("""INSERT INTO stock_move(move_date,product_id,qty,move_type,note)
             VALUES(?,?,?,'生產入庫',?)""", ("2026-09-05", prod_id, 30, "示範:9 月第一批龍眼乾入庫"))
move_id = c.lastrowid
legs = ledger.compose_production_in_entries("龍眼乾", 30 * prod["GY-DRY-300"]["unit_cost"])
add_voucher("F", "2026-09-05", legs, "production_in", move_id, "生產入庫示範")

c.execute("""INSERT INTO other_income(income_date,category,amount,tax_amount,payment_account,note)
             VALUES(?,?,?,?,?,?)""", ("2026-08-31", "利息收入", 85, 0, "郵局", "示範:8 月存款利息"))
inc_id = c.lastrowid
legs = ledger.compose_other_income_entries("利息收入", 85, 0, "郵局")
add_voucher("I", "2026-08-31", legs, "other_income", inc_id, "其他收益示範")

c.execute("""INSERT INTO equity_txn(txn_date,txn_type,amount,payment_account,note)
             VALUES(?,?,?,?,?)""", ("2025-04-01", "現金增資", 50000, "現金", "示範:開業資本額出資"))
eq_id = c.lastrowid
legs = ledger.compose_equity_entries("現金增資", 50000, "現金")
add_voucher("Q", "2025-04-01", legs, "equity", eq_id, "資本異動示範")

c.execute("""INSERT INTO manual_entry(entry_date,debit_code,debit_name,credit_code,credit_name,amount,note)
             VALUES(?,?,?,?,?,?,?)""",
          ("2026-09-10", "1102", "現金", "1103", "銀行存款-郵局", 500, "示範:提領零用金(帳戶間轉帳,套不進其他 8 類)"))
adj_id = c.lastrowid
legs = ledger.compose_manual_entries("1102", "現金", "1103", "銀行存款-郵局", 500)
add_voucher("M", "2026-09-10", legs, "manual_adjustment", adj_id, "帳務調整示範")

cx.commit()
n_ord = c.execute('SELECT COUNT(*) FROM "order"').fetchone()[0]
rev = c.execute("SELECT COALESCE(SUM(order_total),0) FROM \"order\" WHERE order_kind='銷售'").fetchone()[0]
print(f"OK  →  {DB}")
print(f"    訂單 {n_ord} 張 · 銷售額 NT$ {rev:,.0f} · 商品 {len(prod)} · 客戶 {len(cust)} · 批次 {len(bat)}")
cx.close()
