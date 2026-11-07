"""訂單匯入(Google 表單 / Excel)—— 解析檔案 + 比對客戶/商品 + 判斷每一列能不能匯入。
只負責「解析與比對」,實際寫入資料庫(建客戶、建訂單、過帳)留在 main.py 的
/orders/import/confirm,跟手動新增訂單共用同一套入帳邏輯,避免兩條路算出不同結果。

表單/Excel 欄位(表頭文字要完全一樣;2026-11-07 整理,加了訂單日期 / Email / 發票 / 稅額 / 運費 / 實際收費):
  時間戳記(或 Timestamp)— 選填。Google 表單自動產生,有的話拿來防止同一列重複匯入
  訂單日期 — 選填,沒填就用時間戳記的日期;兩個都讀不出日期 → 不能匯入
  客戶姓名、客戶電話 — 必填(電話是比對既有客戶的主要依據)
  Email、收件地址 — 選填(也拿來比對是不是舊客戶)
  付款方式 — 選填,要跟系統用字一樣(現金/銀行匯款/…),對不上會提醒、留空
  發票號碼 — 選填,沒填 = 還沒開,匯入後列進「待補」
  銷項稅額 — 選填,有填照填的;沒填或填 0 都在預覽紅字提醒(附 5% 估算),確認後記 0
  運費 — 選填,跟客人收的運費
  實際收費 — 選填,商品這次實際收多少(不含運費);比商品小計少的差額 = 整單折扣
  備註 — 選填
  其餘每一欄:欄名完全等於某個「在售」商品的品名或 SKU,就當成那個商品的數量欄
以前會默默帶過的(數量打成「2包」、日期讀不出來、付款方式打錯),現在都會在預覽列出來。
"""
import csv
import hashlib
import io
import re
import datetime as dt

from db import q
import queries as Q

try:
    import openpyxl
except ImportError:  # 還沒裝套件時,先讓 CSV 路徑能用,xlsx 上傳會在 parse_file 給出清楚錯誤
    openpyxl = None

META_HEADERS = {
    "timestamp": ["時間戳記", "Timestamp"],
    "order_date": ["訂單日期"],
    "customer_name": ["客戶姓名"],
    "phone": ["客戶電話"],
    "email": ["Email", "E-mail", "電子郵件"],
    "address": ["收件地址"],
    "payment_method": ["付款方式"],
    "invoice_no": ["發票號碼"],
    "sales_tax": ["銷項稅額"],
    "shipping_fee": ["運費"],
    "actual_charge": ["實際收費", "本次實際收費"],
    "note": ["備註"],
}
ALL_META_ALIASES = {alias for names in META_HEADERS.values() for alias in names}
# 範本欄位順序(下載範本用)
TEMPLATE_HEADERS = ["時間戳記", "訂單日期", "客戶姓名", "客戶電話", "Email", "收件地址", "付款方式",
                    "發票號碼", "銷項稅額", "運費", "實際收費", "備註"]
EXAMPLE_MARK = "這是範例"      # 範本範例列的備註;忘了刪也不會被匯入


def normalize_phone(s):
    return re.sub(r"\D", "", s or "")


def _phone_from_cell(v):
    """xlsx 常見問題:電話欄被 Excel 當數字存,開頭的 0 不見了,這裡補回來。"""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int):
        s = str(v)
        return ("0" + s) if len(s) == 9 else s
    return str(v).strip()


_DATE_PATTERNS = [
    (re.compile(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"), lambda m: (int(m[3]), int(m[1]), int(m[2]))),
]
_TIME_RE = re.compile(r"(上午|下午|AM|PM|am|pm)?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM|am|pm)?")


def _ymd(raw):
    """回傳 (date, 字串裡日期後面剩下的部分)。"""
    if isinstance(raw, dt.datetime):
        return raw.date(), ""
    if isinstance(raw, dt.date):
        return raw, ""
    s = str(raw or "")
    for pat, build in _DATE_PATTERNS:
        m = pat.search(s)
        if m:
            try:
                return dt.date(*build(m)), s[m.end():]
            except ValueError:
                continue
    return None, ""


def parse_date(raw):
    """從時間戳記 / 訂單日期(字串或 xlsx 的 datetime 物件)抓出 YYYY-MM-DD;抓不到回傳 None。"""
    d, _ = _ymd(raw)
    return d.isoformat() if d else None


def norm_timestamp(raw):
    """時間戳記統一成 YYYY-MM-DD HH:MM:SS(防重複用)。Google 表單匯出是「2026/10/2 下午 3:00:00」,
    CSV 用 Excel 打開再存一次可能變「2026/10/2 15:00」——兩種寫法要認得是同一列。讀不出來回傳原字串。"""
    if isinstance(raw, dt.datetime):
        return raw.strftime("%Y-%m-%d %H:%M:%S")
    s = str(raw or "").strip()
    d, rest = _ymd(s)
    if not d:
        return s
    t = _TIME_RE.search(rest)
    hh = mi = ss = 0
    if t:
        hh, mi, ss = int(t[2]), int(t[3]), int(t[4] or 0)
        ap = (t[1] or t[5] or "").lower()
        if ap in ("下午", "pm") and hh < 12:
            hh += 12
        elif ap in ("上午", "am") and hh == 12:
            hh = 0
    return f"{d.isoformat()} {hh:02d}:{mi:02d}:{ss:02d}"


def parse_number(v):
    """金額 / 數量欄:空白 → (None, True);數字(可有千分位逗號)→ (float, True);其他 → (None, False)。"""
    if v is None:
        return None, True
    if isinstance(v, bool):
        return None, False
    if isinstance(v, (int, float)):
        return float(v), True
    s = str(v).strip().replace(",", "")
    if s == "":
        return None, True
    try:
        return float(s), True
    except ValueError:
        return None, False


def fmt_qty(v):
    """數量顯示:2.0 → 2、1.5 → 1.5(原本用 |int,1.5 會顯示成 1)。"""
    return f"{float(v):.4f}".rstrip("0").rstrip(".")


def _decode_csv(content: bytes) -> str:
    for enc in ("utf-8-sig", "cp950", "utf-8"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def parse_file(filename, content: bytes):
    """回傳 (headers, raw_rows):raw_rows 每一列是 {表頭: 值}(xlsx 的值保留原始型別,數字/日期
    不會先轉成字串,交給後面的欄位各自處理)。"""
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        if openpyxl is None:
            raise RuntimeError("這台機器還沒裝 openpyxl,無法讀取 .xlsx —— 改存成 .csv 再上傳,或先安裝套件。")
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        headers = [str(h).strip() if h is not None else "" for h in next(rows_iter, [])]
        raw_rows = []
        for vals in rows_iter:
            if all(v is None or str(v).strip() == "" for v in vals):
                continue
            raw_rows.append({headers[i]: vals[i] for i in range(len(headers)) if i < len(vals)})
        return headers, raw_rows
    # 預設當 CSV 讀(.csv,或副檔名怪怪的也試試看)
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [(h or "").strip() for h in (reader.fieldnames or [])]
    headers = list(reader.fieldnames)
    raw_rows = [row for row in reader
                if any(isinstance(v, str) and v.strip() for v in row.values())]
    return headers, raw_rows


def _txt(v):
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, dt.date):
        return v.isoformat()
    return "" if v is None else str(v)


def norm_invoice(no):
    """發票號碼統一寫法(跟 main._norm_invoice 同規則):去空白、去連字號、轉大寫。"""
    return re.sub(r"[\s\-]", "", no or "").upper()


_FW = str.maketrans("０１２３４５６７８９－", "0123456789-")


def _norm_name(s):
    return re.sub(r"\s", "", s or "")


def _norm_email(s):
    return (s or "").strip().lower()


def _norm_addr(s):
    """地址比對用:去空白、全形數字轉半形、「臺」當「台」。"""
    return re.sub(r"\s", "", (s or "").translate(_FW)).replace("臺", "台")


def _customer_index():
    """既有客戶的比對索引:電話 / 姓名(含別名)/ Email / 地址 → 客戶。"""
    custs = {c["customer_id"]: dict(c, names=set(), addrs=set()) for c in
             q("SELECT customer_id, display_name, phone, email FROM customer")}
    for c in custs.values():
        c["names"].add(_norm_name(c["display_name"]))
    for r in q("SELECT customer_id, alias_text FROM customer_alias"):
        if r["customer_id"] in custs:
            custs[r["customer_id"]]["names"].add(_norm_name(r["alias_text"]))
    for r in q("SELECT customer_id, address_full FROM address WHERE COALESCE(address_full,'')<>''"):
        if r["customer_id"] in custs:
            custs[r["customer_id"]]["addrs"].add(_norm_addr(r["address_full"]))
    idx = dict(phone={}, name={}, email={}, addr={})
    for cid, c in custs.items():
        p = normalize_phone(c["phone"])
        if p:
            idx["phone"].setdefault(p, cid)
        for n in c["names"]:
            if n:
                idx["name"].setdefault(n, set()).add(cid)
        if _norm_email(c["email"]):
            idx["email"].setdefault(_norm_email(c["email"]), set()).add(cid)
        for a in c["addrs"]:
            idx["addr"].setdefault(a, set()).add(cid)
    return custs, idx


def _items_key(lines):
    return tuple(sorted((l["product_id"], round(l["qty"], 4)) for l in lines))


PHONE_ALIASES = set(META_HEADERS["phone"])
# 預覽頁「修正」可以改的欄位(時間戳記不能改:它是防重複匯入的依據)
EDIT_FIELDS = [h for h in TEMPLATE_HEADERS if h != "時間戳記"]


def serialize_raw(raw):
    """每一列轉成純文字 dict(預覽頁要帶著走、改完重新檢查;第一次檢查跟重新檢查才會看到一模一樣的資料)。
    電話先補回開頭的 0、整數的 2.0 寫成 2、日期時間寫成 YYYY-MM-DD HH:MM:SS。"""
    out = {}
    for k, v in raw.items():
        if k is None:
            continue
        if k in PHONE_ALIASES:
            v = _phone_from_cell(v)
        elif isinstance(v, float) and v.is_integer():
            v = str(int(v))
        out[k] = _txt(v).strip()
    return out


def field_value(raw, field):
    """預覽頁「修正」欄位要帶的值:欄名用範本的寫法,檔案用的是別名(例 E-mail)也找得到。"""
    for aliases in META_HEADERS.values():
        if field in aliases:
            for a in aliases:
                if str(raw.get(a) or "").strip():
                    return str(raw[a])
            return ""
    return str(raw.get(field) or "")


def set_field(raw, field, value):
    """改一個欄位;同一欄的其他別名一起清掉,不然清空 Email 時還會讀到 E-mail。"""
    for aliases in META_HEADERS.values():
        if field in aliases:
            for a in aliases:
                raw.pop(a, None)
    raw[field] = value


def warn_sig(row):
    """這一列 ⚠️ 提醒 + 對到的客戶的指紋:勾「已確認」時記下來,確認匯入時內容變了就要重勾。"""
    txt = "|".join(row.get("warnings") or []) + "#" + str(row.get("matched_customer_id"))
    return hashlib.sha1(txt.encode("utf-8")).hexdigest()[:12]


def match_rows(headers, raw_rows, pay_methods, closed_years=(), invoice_taken=None, today=None, orig_rows=None):
    """逐列解析 + 檢查 + 比對客戶,標出可匯入 / 要確認 / 有問題 / 已匯入過。
    每一列帶 errors(有任何一條就不能匯入)、warnings(⚠️ 要勾「已確認」才匯入)、infos(只是告知)。
    status:ok(沒有要確認的)/ warn(有 ⚠️)/ error / duplicate。
    客戶:cust_mode = existing(電話、姓名都對上)/ check(電話對上但姓名不同,或電話沒對上但姓名 / Email / 地址
    有一樣的)/ new(都沒對上);candidates = 可能是的舊客戶(附哪幾項相同);default_customer = 預設選哪個。
    closed_years:已年度結轉的年份;invoice_taken(no) → 撞到的那筆 {label} 或 None(main._invoice_taken)。
    orig_rows:預覽頁修正過時傳原始檔案的列——沒時間戳記的列用「原始內容」判斷是否匯入過、也存原始的,
    之後又上傳同一份原始檔才認得出來。"""
    orig_refs = {}
    if orig_rows is not None:
        for r in match_rows(headers, orig_rows, pay_methods, closed_years, None, today)["rows"]:
            if r["source_ref"]:
                orig_refs[r["idx"]] = r["source_ref"]
    today = today or dt.date.today().isoformat()
    product_cols = {}       # header -> product_id
    unmatched_headers = []
    for h in headers:
        h = (h or "").strip()
        if not h or h in ALL_META_ALIASES:
            continue
        unmatched_headers.append(h)  # 之後找到比對就移除

    products = q("SELECT product_id, sku, name, product_group_id FROM product WHERE status='在售'")
    pinfo = {p["product_id"]: p for p in products}
    by_name = {p["name"]: p["product_id"] for p in products}
    by_sku = {p["sku"]: p["product_id"] for p in products}
    for h in list(unmatched_headers):
        pid = by_name.get(h) or by_sku.get(h)
        if pid:
            product_cols[h] = pid
            unmatched_headers.remove(h)
    # 欄名對到的是已停售商品,講清楚(不然只說「找不到」會以為打錯字)
    stopped = set()
    for r in q("SELECT name, sku FROM product WHERE status<>'在售'"):
        stopped |= {r["name"], r["sku"]}
    stopped_headers = [h for h in unmatched_headers if h in stopped]
    # 商品售價(客戶 2026-10-16 起不分級,一律用「零售」那一欄);成本移轉價帶上一次的(跟手動開單一樣)
    price = {r["product_id"]: r["unit_price"] for r in q(
        "SELECT product_id, unit_price FROM price_list WHERE customer_segment='零售' AND channel_id IS NULL")}
    lastcost = {pid: v["u"] for pid, v in Q.last_unit_costs().items()}

    custs, cidx = _customer_index()

    # 已匯入過的:時間戳記(舊資料存的是原字串,一併換成統一寫法)或沒時間戳記時的「日期|電話|品項」
    existing_refs = set()
    for r in q("SELECT source_ref FROM \"order\" WHERE source_ref LIKE 'gform:%' OR source_ref LIKE 'import:%'"):
        existing_refs.add(r["source_ref"])
        if r["source_ref"].startswith("gform:"):
            existing_refs.add("gform:" + norm_timestamp(r["source_ref"][6:]))
    # 疑似重複:同客戶、同一天、同品項同數量的既有訂單(例如已經手動開過)
    similar = {}
    for r in q("""SELECT o.order_no, o.customer_id, o.order_date, ol.product_id, ol.qty
                  FROM "order" o JOIN order_line ol ON ol.order_id=o.order_id"""):
        similar.setdefault((r["customer_id"], r["order_date"], r["order_no"]), []).append(
            {"product_id": r["product_id"], "qty": r["qty"]})
    similar_by_key = {}
    for (cid, d, no), ls in similar.items():
        similar_by_key.setdefault((cid, d, _items_key(ls)), []).append(no)

    seen_ref, seen_inv, seen_same, seen_newphone = {}, {}, {}, {}
    rows = []
    ok_n = warn_n = err_n = dup_n = 0
    for idx, raw in enumerate(raw_rows):
        line_no = idx + 2     # 檔案裡的第幾列(第 1 列是表頭)

        def meta(key):
            for alias in META_HEADERS[key]:
                v = raw.get(alias)
                if v is not None and str(v).strip() != "":
                    return v
            return None

        errors, warnings, infos = [], [], []
        ts_raw = meta("timestamp")
        name = str(meta("customer_name") or "").strip()
        phone_raw = _phone_from_cell(meta("phone"))
        email = str(meta("email") or "").strip()
        address = str(meta("address") or "").strip()
        pm_raw = str(meta("payment_method") or "").strip()
        payment_method = pm_raw if pm_raw in pay_methods else None
        note = str(meta("note") or "").strip()
        invoice_no = norm_invoice(_txt(meta("invoice_no")))

        if EXAMPLE_MARK in note:
            errors.append("這是範本的範例列,不會匯入(可以直接刪掉)")

        # 日期:訂單日期優先,沒填用時間戳記的日期;都讀不出來就不能匯入(以前會默默變成今天)
        od_raw = meta("order_date")
        order_date = None
        if od_raw is not None:
            order_date = parse_date(od_raw)
            if not order_date:
                errors.append(f"訂單日期「{_txt(od_raw)}」看不懂,請寫成 2026/10/2 這種格式")
        elif ts_raw is not None:
            order_date = parse_date(ts_raw)
            if not order_date:
                errors.append(f"時間戳記「{_txt(ts_raw)}」讀不出日期,請另外填「訂單日期」")
        else:
            errors.append("沒有訂單日期(「訂單日期」或「時間戳記」至少要填一個)")
        if order_date and int(order_date[:4]) in set(closed_years):
            errors.append(f"{order_date[:4]} 年已經做過年度結轉,不能再匯入那一年的訂單(要匯入請先到試算表撤銷結轉)")
        elif order_date and order_date > today:
            warnings.append(f"訂單日期 {order_date} 在未來(今天是 {today})")

        if not name:
            errors.append("沒填客戶姓名")
        if not phone_raw:
            errors.append("沒填客戶電話")
        if pm_raw and not payment_method:
            warnings.append(f"付款方式「{pm_raw}」系統不認得,會留空(可用:{'、'.join(pay_methods)})")

        # 金額欄:打錯(不是數字或負數)直接擋,不默默當 0
        money = {}
        for key, label in (("sales_tax", "銷項稅額"), ("shipping_fee", "運費"), ("actual_charge", "實際收費")):
            v, ok = parse_number(meta(key))
            if not ok:
                errors.append(f"{label}「{_txt(meta(key))}」不是數字")
            elif v is not None and v < 0:
                errors.append(f"{label}不能是負數")
                v = None
            money[key] = v

        # 商品
        lines, qty_bad = [], False
        for h, pid in product_cols.items():
            v, ok = parse_number(raw.get(h))
            if not ok:
                errors.append(f"「{h}」的數量「{_txt(raw.get(h))}」不是數字(只能填數字,例如 2 或 1.5)")
                qty_bad = True
                continue
            if v is None or v == 0:
                continue
            if v < 0:
                errors.append(f"「{h}」的數量不能是負數")
                qty_bad = True
                continue
            up = price.get(pid)
            uc = lastcost.get(pid) or 0
            lines.append({"product_id": pid, "name": pinfo[pid]["name"], "qty": v, "qty_txt": fmt_qty(v),
                          "unit_price": up or 0, "subtotal": round(v * (up or 0), 2),
                          "unit_cost": uc, "line_cost": round(v * uc, 2)})
        if not lines and not qty_bad:
            errors.append("沒有任何商品數量(每個商品欄填數量,0 或空白 = 沒買)")
        nopg = [l["name"] for l in lines if not pinfo[l["product_id"]]["product_group_id"]]
        if nopg:   # 2026-10-24:沒有產品線不知道收入 / 成本記哪個科目
            errors.append("商品還沒設定產品線(先到商品目錄設定):" + "、".join(nopg))
        noprice = [l["name"] for l in lines if not price.get(l["product_id"])]
        if noprice:   # 以前會開出 0 元的訂單
            errors.append("商品還沒設定售價(先到商品目錄設定):" + "、".join(noprice))
        nocost = [l["name"] for l in lines if not lastcost.get(l["product_id"])]
        if nocost:
            warnings.append("沒有上一次的成本移轉價,成本先記 0、匯入後要到訂單補:" + "、".join(nocost))

        # 金額:小計 → 實際收費(折扣)→ + 運費 = 應收合計
        subtotal = round(sum(l["subtotal"] for l in lines), 2)
        charge = money["actual_charge"] if money["actual_charge"] is not None else subtotal
        discount = round(subtotal - charge, 2)
        if abs(discount) < 0.01:
            discount = 0.0
        shipping = money["shipping_fee"] or 0
        total = round(charge + shipping, 2)
        if discount < 0 and subtotal:
            warnings.append(f"實際收費 {charge:,.0f} 比商品小計 {subtotal:,.0f} 高 +{-discount:,.0f}")
        tax = money["sales_tax"]
        if tax and total and tax >= total:
            errors.append(f"銷項稅額 {tax:,.0f} 比應收合計 {total:,.0f} 還大,應該是打錯了")
        # 銷項稅額沒填或填 0 都要提醒(2026-11-07 使用者:填 0 也要);附 5% 估算,免稅商品確認後照樣記 0
        elif not tax and lines:
            est = f",依 5% 估算約 {round(total * 5 / 105):,} 元(應收 {total:,.0f} ÷ 1.05 × 5%)" if total > 0 else ""
            what = "沒填" if tax is None else "填 0"
            warnings.append(f"銷項稅額{what}{est};免稅商品(例如未加工鮮果)確認後照樣記 0")

        # 發票號碼:有填要不重複(檔案裡、系統裡都比);沒填 = 還沒開,匯入後列進待補
        if invoice_no:
            if invoice_no in seen_inv:
                errors.append(f"發票號碼 {invoice_no} 跟第 {seen_inv[invoice_no]} 列重複")
            else:
                hit = invoice_taken(invoice_no) if invoice_taken else None
                if hit:
                    errors.append(f"發票號碼 {invoice_no} 已經用在「{hit['label']}」")
            seen_inv.setdefault(invoice_no, line_no)
        elif lines:
            warnings.append("沒有發票號碼(還沒開?),匯入後列進待補事項")

        # 防重複:有時間戳記用時間戳記;沒有就用「日期|電話|品項」
        if ts_raw is not None:
            source_ref = f"gform:{norm_timestamp(ts_raw)}"
        elif order_date and phone_raw and lines:
            source_ref = "import:" + "|".join([order_date, normalize_phone(phone_raw),
                                               ",".join(f"{p}x{fmt_qty(qv)}" for p, qv in _items_key(lines))])
        else:
            source_ref = None
        file_ref = source_ref                       # 檔案內比對用(改過就用改過的)
        if idx in orig_refs and orig_refs[idx] != source_ref:
            source_ref = orig_refs[idx]             # 跟系統比對、存檔用原始的
        dup_reason = None
        if source_ref and source_ref in existing_refs:
            dup_reason = "之前已經匯入過(同一個時間戳記)" if ts_raw is not None else \
                         "之前已經匯入過(同一天、同電話、同品項,而且沒有時間戳記)"
        elif file_ref and file_ref in seen_ref:
            errors.append(f"跟第 {seen_ref[file_ref]} 列重複(" +
                          ("時間戳記一樣)" if ts_raw is not None else "同一天、同電話、同品項)"))
        if file_ref:
            seen_ref.setdefault(file_ref, line_no)

        # 客戶比對:電話優先;電話沒對上,再看姓名 / Email / 地址有沒有跟舊客戶一樣的
        pkey = normalize_phone(phone_raw)
        cust_mode, candidates, default_customer, same_as_line = "new", [], "new", None
        cid = cidx["phone"].get(pkey) if pkey else None
        if cid:
            c = custs[cid]
            if _norm_name(name) in c["names"]:
                cust_mode = "existing"
            else:
                cust_mode = "check"
                warnings.append(f"電話跟舊客戶「{c['display_name']}」一樣,但姓名是「{name}」,請確認是不是同一位")
            candidates = [dict(customer_id=cid, display_name=c["display_name"], phone=c["phone"] or "",
                               reasons=["電話"] + (["姓名"] if cust_mode == "existing" else []))]
            default_customer = cid
            if email and _norm_email(email) != _norm_email(c["email"]) or \
                    address and c["addrs"] and _norm_addr(address) not in c["addrs"]:
                infos.append("檔案裡的 Email / 地址跟客戶資料不一樣,不會自動更新客戶資料(要改請到客戶頁)")
        elif name or email or address:
            hits = {}
            for key, val, label in (("name", _norm_name(name), "姓名"), ("email", _norm_email(email), "Email"),
                                    ("addr", _norm_addr(address), "地址")):
                for hcid in (cidx[key].get(val, ()) if val else ()):
                    hits.setdefault(hcid, []).append(label)
            if hits:
                cust_mode = "check"
                candidates = [dict(customer_id=hcid, display_name=custs[hcid]["display_name"],
                                   phone=custs[hcid]["phone"] or "", reasons=rs)
                              for hcid, rs in sorted(hits.items(), key=lambda kv: -len(kv[1]))]
                top = candidates[0]
                warnings.append(f"電話沒對上,但{'、'.join(top['reasons'])}跟舊客戶「{top['display_name']}」"
                                f"({top['phone'] or '沒留電話'})一樣,請確認是不是舊客戶"
                                + (f"(另有 {len(candidates) - 1} 位也相似)" if len(candidates) > 1 else ""))
            else:
                warnings.append("新客戶,確認匯入時建立")
        else:
            warnings.append("新客戶,確認匯入時建立")
        # 同一個新電話在檔案裡出現好幾列:只建一位客戶,後面的列跟著第一列
        if cust_mode != "existing" and pkey and not cid:
            if pkey in seen_newphone:
                same_as_line = seen_newphone[pkey]
                infos.append(f"跟第 {same_as_line} 列是同一個電話,如果都建新客戶只會建一位")
            else:
                seen_newphone[pkey] = line_no

        # 疑似重複:同客戶同一天同品項(系統裡已有的 / 檔案裡前面的列)
        ikey = _items_key(lines)
        if cid and order_date and lines:
            nos = similar_by_key.get((cid, order_date, ikey))
            if nos:
                warnings.append(f"疑似重複:已經有訂單 {'、'.join(nos)}(同客戶、同一天、同品項同數量)")
        if pkey and order_date and lines:
            sk = (pkey, order_date, ikey)
            if sk in seen_same and not any(f"第 {seen_same[sk]} 列重複" in e for e in errors):
                warnings.append(f"疑似重複:跟第 {seen_same[sk]} 列一樣(同電話、同一天、同品項同數量)")
            seen_same.setdefault(sk, line_no)

        row = dict(idx=idx, line_no=line_no, raw_timestamp=_txt(ts_raw), customer_name=name,
                   phone_raw=_txt(meta("phone")), phone=phone_raw, email=email, address=address,
                   payment_method=payment_method, note=note, invoice_no=invoice_no,
                   sales_tax=money["sales_tax"], shipping_fee=money["shipping_fee"],
                   actual_charge=money["actual_charge"], lines=lines,
                   subtotal=subtotal, discount=discount, total=total, tax_est=round(total * 5 / 105) if total > 0 else 0,
                   cost_total=round(sum(l["line_cost"] for l in lines), 2),
                   errors=errors, warnings=warnings, infos=infos,
                   cust_mode=cust_mode, candidates=candidates, default_customer=default_customer,
                   same_as_line=same_as_line,
                   status=None, error_reason=None, dup_reason=dup_reason,
                   matched_customer_id=cid, matched_customer_name=custs[cid]["display_name"] if cid else None,
                   source_ref=source_ref, order_date=order_date)

        if dup_reason:
            row["status"] = "duplicate"
            dup_n += 1
        elif errors:
            row["status"], row["error_reason"] = "error", errors[0]
            err_n += 1
        elif warnings:
            row["status"] = "warn"
            warn_n += 1
        else:
            row["status"] = "ok"
            ok_n += 1
        rows.append(row)

    return dict(rows=rows, unmatched_headers=unmatched_headers, stopped_headers=stopped_headers,
                product_headers=list(product_cols),
                summary=dict(ok=ok_n, warn=warn_n, err=err_n, dup=dup_n, total=len(rows)))
