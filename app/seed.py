"""建立 郡碩銷售.db:套用 schema.sql 並灌入示範假資料。
用法:  py seed.py          (存在則詢問覆蓋)
       py seed.py --force  (直接重建)
"""
import sqlite3, os, sys, random, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(HERE, "guiyuan_ledger.db")
SQL  = os.path.join(HERE, "schema.sql")
random.seed(20260904)

if os.path.exists(DB):
    if "--force" not in sys.argv:
        ans = input(f"{DB} 已存在,要重建嗎? (y/N) ").strip().lower()
        if ans != "y":
            print("取消。"); sys.exit(0)
    os.remove(DB)

cx = sqlite3.connect(DB)
cx.executescript(open(SQL, encoding="utf-8").read())
c = cx.cursor()

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

# 每個 SKU 歸到哪個產品群組(稅別預設「待確認」,等家易確認)
SKU_GROUP = {
    "GY-DRY-300": "龍眼乾", "GY-DRY-500": "龍眼乾", "GY-DRY-BULK": "龍眼乾",
    "GY-GIFT": "龍眼乾", "GX-STICK": "龍眼乾",
    "LG-MEAT-600": "龍眼肉", "LG-MEAT-1000": "龍眼肉",
    "HNY-LONGAN-420": "蜂蜜", "HNY-LYCHEE-420": "蜂蜜",
    "GY-FRESH-TCHIN": "龍眼鮮果",
}

# ---- 通路 ----------------------------------------------------------
channels = [
    ("WEB",   "官網",       "官網",       0.0,  0),
    ("LINE",  "LINE 社群",  "LINE社群",   0.05, 7),
    ("FAIR",  "市集展售",   "市集展售",   0.0,  0),
    ("WS",    "批發",       "批發",       0.0, 30),
    ("MEDIA", "媒體導流",   "媒體導流",   0.0,  0),
    ("SELF",  "自售",       "直售",       0.0,  0),
]
c.executemany("INSERT INTO channel(code,name,category,commission_pct,settlement_lag_days) VALUES(?,?,?,?,?)", channels)
ch = {row[1]: i+1 for i, row in enumerate(channels)}

# ---- 商品 --------------------------------------------------------
products = [
    # sku, name, type, code_raw, net_g, gross_g, form, uom, g/uom, shelf, gift_only, retail, wholesale, group, unit_cost_hint
    ("GY-DRY-300",  "桂圓乾 300g",        "單品", "#26",       300,  360, "夾鏈袋", "包", 300, 180, 0, 175, 140,  96),
    ("GY-DRY-500",  "桂圓乾 500g",        "單品", "#28",       500,  580, "夾鏈袋", "包", 500, 180, 0, 300, 250, 150),
    ("GY-DRY-BULK", "桂圓乾 裸裝",        "裸裝", "#24",       None, None, "裸裝",   "斤", 600, 180, 0, 220, 140, 130),
    ("GY-GIFT",     "桂圓乾 禮盒(300g×2)","禮盒", "#27",       600,  900, "禮盒",   "盒", 600, 180, 0, 350, 300, 210),
    ("LG-MEAT-600", "龍眼肉 600g 罐",     "單品", None,        600,  700, "罐",     "罐", 600, 365, 0, 600, 500, 340),
    ("LG-MEAT-1000","龍眼肉 1000g 裸裝",  "單品", None,       1000, 1100, "真空袋", "罐",1000, 365, 0,1000, 900, 560),
    ("GX-STICK",    "桂圓棒",             "加購贈品", None,     40,   50, "夾鏈袋", "支",  40, 150, 1,   0,   0,  18),
    ("HNY-LONGAN-420","龍眼蜂蜜 420g",    "單品", None,        420,  620, "玻璃罐", "罐", 420, 730, 0, 420, 360, 150),
    ("HNY-LYCHEE-420","荔枝蜂蜜 420g",    "單品", None,        420,  620, "玻璃罐", "罐", 420, 730, 0, 420, 360, 160),
    ("GY-FRESH-TCHIN","龍眼鮮果 台斤",    "裸裝", None,       None, None, "裸裝",   "斤", 600,  10, 0,  80,  60,  35),
]
prod = {}
for p in products:
    low = {"GY-DRY-300": 60, "GY-GIFT": 60, "LG-MEAT-600": 60}.get(p[0])
    ingredients = "蜂蜜(南投中寮)" if p[0].startswith("HNY") else "龍眼(南投中寮)"
    pgid = pg.get(SKU_GROUP.get(p[0]))
    c.execute("""INSERT INTO product(sku,name,product_type,type_code_raw,net_weight_g,gross_weight_g,
                 package_form,uom,grams_per_uom,shelf_life_days,gift_only,ingredients,origin,status,low_stock,
                 product_group_id)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (p[0],p[1],p[2],p[3],p[4],p[5],p[6],p[7],p[8],p[9],p[10],
               ingredients,"南投中寮","在售" if p[0]!="GY-DRY-500" else "停售", low, pgid))
    pid = c.lastrowid
    prod[p[0]] = dict(id=pid, retail=p[11], wholesale=p[12], unit_cost=p[13], uom=p[7])
    # 定價表:零售 / 批發 / 團購(零售95折) / 機構(零售) / 內部(0)
    for seg, price in (("零售",p[11]),("批發",p[12]),("團購",round(p[11]*0.95)),
                       ("機構",p[11]),("內部",0)):
        c.execute("INSERT INTO price_list(product_id,customer_segment,unit_price) VALUES(?,?,?)",
                  (pid, seg, price))

# ---- 批次 ------------------------------------------------------
batches = [
    ("2024-A", 2024, "2024-09-10", "2024-09-24", 6400, "2024-09-24"),
    ("2024-B", 2024, "2024-10-20", "2024-11-02", 2600, "2024-11-02"),
    ("2025-A", 2025, "2025-09-10", "2025-09-24", 7200, "2025-09-24"),
    ("2025-B", 2025, "2025-10-25", "2025-11-06", 2800, "2025-11-06"),
]
bat = {}
for code, bseason, rs, re_, out, mfg in batches:
    c.execute("""INSERT INTO batch(batch_code,season,roast_start,roast_end,raw_source,
                 raw_input_kg,output_qty,output_uom,mfg_date,unit_cost)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (code, bseason, rs, re_, "自園 + 收購", out*3.2, out, "份", mfg, 110))
    bat[code] = c.lastrowid

# ---- 客戶 ----------------------------------------------------
customers = [
    # name, type, segment, channel, aliases
    ("一品園有機商店", "通路商", "批發",  "批發",   ["游念慈(一品園)","一品園"]),
    ("永豐米糧行",     "通路商", "批發",  "批發",   []),
    ("主婦聯盟",       "機構團體","機構", "批發",   ["主婦聯盟/柴烘桂圓"]),
    ("張玉玲",         "個人",   "團購主","市集展售",["張玉玲(中興高中老師/育成市集)"]),
    ("張宜彤",         "個人",   "團購主","市集展售",["張宜彤/育成村客人"]),
    ("莊麗芳",         "個人",   "零售",  "媒體導流",["莊麗芳/台灣亮起來客人"]),
    ("黃雯菁",         "個人",   "零售",  "媒體導流",["黃雯菁/台灣亮起來客人"]),
    ("林春男",         "個人",   "零售",  "官網",   ["林春男(心戰大隊)"]),
    ("李國賓",         "個人",   "團購主","LINE 社群",["李國賓老師"]),
    ("劉麗華(基隆)",   "個人",   "零售",  "官網",   []),
    ("劉麗華(宜蘭)",   "個人",   "零售",  "官網",   []),
    ("賴碧秋",         "個人",   "零售",  "自售",   []),
    ("阿琴豬腳飯",     "公司",   "零售",  "自售",   []),
    ("今周刊",         "公司",   "公關對象","媒體導流",["今周刊拍攝"]),
    ("御鼎興",         "公司",   "公關對象","自售",   []),
    ("彭寓達",         "個人",   "公關對象","自售",   []),
]
cust = {}
for name, ctype, seg, chan, aliases in customers:
    c.execute("""INSERT INTO customer(display_name,customer_type,segment,primary_channel_id,tax_doc_pref)
                 VALUES(?,?,?,?,?)""",
              (name, ctype, seg, ch[chan], "農民收據" if seg in ("批發","機構") else "免開立"))
    cid = c.lastrowid
    cust[name] = dict(id=cid, seg=seg, chan=chan)
    c.execute("INSERT INTO customer_alias(customer_id,alias_text) VALUES(?,?)", (cid, name))
    for a in aliases:
        c.execute("INSERT INTO customer_alias(customer_id,alias_text) VALUES(?,?)", (cid, a))
    c.execute("""INSERT INTO address(customer_id,label,recipient_name,is_default,address_full)
                 VALUES(?,?,?,1,?)""", (cid, "預設", name, "(示範地址)"))

seg_to_priceseg = {"批發":"批發","機構":"機構","團購主":"團購","零售":"零售","公關對象":"內部"}

def price_of(sku, seg):
    return prod[sku][{"批發":"wholesale"}.get(seg,"retail")] if seg=="批發" else \
           {"團購":round(prod[sku]["retail"]*0.95),"內部":0}.get(seg, prod[sku]["retail"])

# ---- 訂單 --------------------------------------------------
CARRIERS = ["中華郵政","黑貓","7-11","全家"]
months = [(2025,9),(2025,10),(2025,11),(2025,12),(2026,1),(2026,2),(2026,3)]
order_no = 0
sale_names = [n for n,i in cust.items() if i["seg"] != "公關對象"]
pr_names   = [n for n,i in cust.items() if i["seg"] == "公關對象"]

def make_order(d, cname, kind, lines, ship_method, pay_status, season=2025):
    global order_no
    order_no += 1
    ci = cust[cname]
    cid = ci["id"]
    chan = ci["chan"]
    seg = seg_to_priceseg.get(ci["seg"], "零售")
    subtotal = 0.0
    discount = 0.0
    for sku, qty, gift in lines:
        std = prod[sku]["retail"]
        up  = 0 if (gift or kind != "銷售") else price_of(sku, seg)
        ld  = 0
        if not gift and kind == "銷售" and up < std and random.random() < .35:
            ld = round((std-up)*qty*0.1)   # 偶發折讓
        sub = qty*up - ld
        subtotal += sub
        discount += ld
    ship_charged = 0
    ship_cost = 0
    carrier = None
    if ship_method in ("宅配","超商店到店","超商賣貨便","冷藏宅配"):
        carrier = random.choice(CARRIERS)
        ship_cost = random.choice([60,65,70,75,80,120])
        ship_charged = random.choice([0,0,0,60,80])
    plat = round(subtotal*(0.05 if chan=="LINE 社群" else 0))
    total = subtotal - discount + ship_charged
    pm = {"批發":"銀行匯款","機構":"銀行匯款"}.get(ci["seg"], random.choice(["現金","銀行匯款","現金"]))
    if kind != "銷售":
        pm, pay_status, total = "未收款", "免收款", 0
    c.execute("""INSERT INTO "order"(order_no,order_date,season,customer_id,channel_id,order_kind,
                 source_ref,discount_total,shipping_fee_charged,platform_fee,order_total,
                 payment_method,payment_account,payment_status,paid_date,
                 tax_doc_type,ship_method,carrier,shipping_cost_actual,ship_status)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (f"S{order_no:04d}", d.isoformat(), season, cid, ch[chan], kind,
               None, discount, ship_charged, plat, total,
               pm, "郵局" if pm=="銀行匯款" else None, pay_status,
               d.isoformat() if pay_status=="已收款" else None,
               "農民收據" if ci["seg"] in ("批發","機構") else "免開立",
               ship_method, carrier, ship_cost,
               "已送達" if pay_status=="已收款" else random.choice(["已出貨","待出貨","已送達"])))
    oid = c.lastrowid
    bcode = f"{season}-A"   # 示範:整季都出自 A 批,庫存與批次帳才一致
    for sku, qty, gift in lines:
        tier = 0 if (gift or kind != "銷售") else price_of(sku, seg)   # 該分級標準價
        up = tier
        if not gift and kind == "銷售" and random.random() < .15:      # 偶發:賣得比標準價低
            up = round(tier * 0.9)
        sub = 0 if gift else round(qty * up)
        c.execute("""INSERT INTO order_line(order_id,product_id,batch_id,qty,unit_price,list_price,
                     line_discount,line_subtotal,is_gift) VALUES(?,?,?,?,?,?,?,?,?)""",
                  (oid, prod[sku]["id"], bat[bcode], qty, up, tier, 0, sub, 1 if gift else 0))
    return oid

SKUS_SELL = ["GY-DRY-300","GY-DRY-BULK","GY-GIFT","LG-MEAT-600","LG-MEAT-1000"]
SEASON_MONTHS = {
    2024: [(2024,9),(2024,10),(2024,11),(2024,12),(2025,1),(2025,2),(2025,3)],
    2025: [(2025,9),(2025,10),(2025,11),(2025,12),(2026,1),(2026,2),(2026,3)],
}
for season, months in SEASON_MONTHS.items():
    for (yy,mm) in months:
        # 旺月(10–12 月)訂單多、淡月少 —— 讓月損益有明顯起伏
        n = random.randint(11, 17) if mm in (10, 11, 12) else random.randint(5, 9)
        for _ in range(n):
            d = dt.date(yy, mm, random.randint(1, 27))
            cname = random.choice(sale_names)
            lines = []
            for _ in range(random.randint(1, 3)):
                sku = random.choice(SKUS_SELL)
                qty = random.choice([2,3,4,5,6,8,10,12,20]) if "BULK" not in sku else random.choice([15,20,30,40,50])
                lines.append((sku, qty, False))
            if random.random() < .3:
                lines.append(("GX-STICK", random.choice([1,2]), True))
            sm = random.choice(["宅配","宅配","自行配送","客戶自取","超商店到店"])
            ps = random.choice(["已收款","已收款","已收款","待收款","部分收款"])
            make_order(d, cname, "銷售", lines, sm, ps, season=season)
        for _ in range(random.randint(1,2)):
            d = dt.date(yy, mm, random.randint(1,27))
            make_order(d, random.choice(pr_names), "贈送-公關",
                       [(random.choice(["GY-DRY-300","GY-GIFT"]), random.choice([1,2,4,6]), False)],
                       "自行配送", "免收款", season=season)

# 幾筆逾期未收(把日期往前挪 + 狀態待收)
c.execute("""UPDATE "order" SET payment_status='待收款', paid_date=NULL
             WHERE order_id IN (SELECT order_id FROM "order" WHERE order_kind='銷售'
                                ORDER BY order_date LIMIT 3)""")

# ---- 物流異常 ----------------------------------------------
rows = c.execute("""SELECT order_id, carrier, order_date FROM "order"
                    WHERE carrier IS NOT NULL ORDER BY RANDOM() LIMIT 5""").fetchall()
for i,(oid, carrier, od) in enumerate(rows):
    itype = ["破損","破損","遺失","客訴","破損"][i]
    # 前 2 筆留「未處理」給使用者練習,其餘已處理
    reso = None if i < 2 else ("重寄" if itype != "客訴" else "折讓")
    c.execute("""INSERT INTO shipment_issue(order_id,issue_type,issue_date,qty_affected,
                 resolution,cost_impact,reason_note) VALUES(?,?,?,?,?,?,?)""",
              (oid, itype, od, 1, reso,
               random.choice([350,420,600,2100]), f"{carrier} · 示範資料"))
    if carrier == "黑貓":
        c.execute("UPDATE \"order\" SET ship_status='破損' WHERE order_id=?", (oid,))

# ---- 產季目標 --------------------------------------------
c.execute("INSERT INTO sales_target(season,product_id,target_qty,target_amount) VALUES(2024,NULL,NULL,420000)")
c.execute("INSERT INTO sales_target(season,product_id,target_qty,target_amount) VALUES(2025,NULL,NULL,500000)")

# ---- 待確認佇列(示範 2 筆) -----------------------------
c.execute("""INSERT INTO review_queue(source,issue_type,note) VALUES
             ('form','客戶未分級','新客戶「王小明」尚未指定分級'),
             ('form','金額待確認','S0007 折讓金額與備註不符')""")

# ---- 營運費用(示範:只灌「已發生」的月份,不預灌未來)---
# 2024、2025 兩個產季各 12 個月(4 月初~隔年 3 月底);
# 2026 產季目前只到 8 月(前期投入期,4–8 月已發生,尚未開賣,9 月之後還沒到不預灌)。
opex_months = []
_y, _m = 2024, 4
while (_y, _m) <= (2026, 8):
    opex_months.append(f"{_y}-{_m:02d}")
    _m += 1
    if _m > 12:
        _m = 1; _y += 1
for ym in opex_months:
    peak = int(ym[5:7]) in (10, 11, 12)        # 旺月行銷 / 人力多一點
    for cat, amt in [("人事", 32000 if not peak else 40000),
                     ("場地・倉儲", 5000),
                     ("行銷", random.choice([5000, 7000, 9000]) + (5000 if peak else 0)),
                     ("金流手續費", random.randint(2000, 4500)),
                     ("其他", random.randint(1500, 3500))]:
        c.execute("INSERT INTO op_expense(ym,category,amount) VALUES(?,?,?)", (ym, cat, amt))

# ---- 庫存異動:每產季各分裝一批(A 批),再依訂單出庫 ----
import math
sku_by_id = {v["id"]: k for k, v in prod.items()}
sold_by_season = {}   # (pid, season) -> 該季賣出總量
for pid, season, qty in c.execute("""
        SELECT ol.product_id, o.season, ol.qty
        FROM order_line ol JOIN "order" o ON o.order_id=ol.order_id""").fetchall():
    sold_by_season[(pid, season)] = sold_by_season.get((pid, season), 0) + (qty or 0)
for (pid, season), q_sold in sold_by_season.items():
    a_batch = c.execute("SELECT batch_id, mfg_date FROM batch WHERE batch_code=?",
                        (f"{season}-A",)).fetchone()
    if not a_batch:
        continue
    # GY-DRY-300 故意備得不夠 → 觸發「超賣」警示;其餘備約 12% 餘量
    factor = 0.9 if sku_by_id.get(pid) == "GY-DRY-300" else 1.12
    c.execute("""INSERT INTO stock_move(move_date,product_id,batch_id,qty,move_type,note)
                 VALUES(?,?,?,?,?,?)""",
              (a_batch[1], pid, a_batch[0], math.ceil(q_sold * factor),
               "分裝入庫", f"示範:{season} 產季分裝"))
for oid, kind, odate in c.execute(
        'SELECT order_id, order_kind, order_date FROM "order"').fetchall():
    mtype = "銷售出庫" if kind == "銷售" else "贈送出庫"
    for pid, bid, qty in c.execute(
            "SELECT product_id, batch_id, qty FROM order_line WHERE order_id=?", (oid,)).fetchall():
        c.execute("""INSERT INTO stock_move(move_date,product_id,batch_id,qty,move_type,ref_order_id)
                     VALUES(?,?,?,?,?,?)""", (odate, pid, bid, -abs(qty or 0), mtype, oid))

cx.commit()
n_ord = c.execute('SELECT COUNT(*) FROM "order"').fetchone()[0]
rev = c.execute("SELECT SUM(order_total) FROM \"order\" WHERE order_kind='銷售'").fetchone()[0]
print(f"OK  →  {DB}")
print(f"    訂單 {n_ord} 張 · 銷售額 NT$ {rev:,.0f} · 商品 {len(prod)} · 客戶 {len(cust)} · 批次 {len(bat)}")
cx.close()
