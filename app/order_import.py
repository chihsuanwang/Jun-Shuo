"""訂單匯入(Google 表單 / Excel)—— 解析檔案 + 比對客戶/商品 + 判斷每一列能不能匯入。
只負責「解析與比對」,實際寫入資料庫(建客戶、建訂單、過帳)留在 main.py 的
/orders/import/confirm,跟手動新增訂單共用同一套入帳邏輯,避免兩條路算出不同結果。

表單/Excel 固定欄位(表頭文字要完全一樣,大小寫/全半形不轉換):
  時間戳記(或 Timestamp)— 必填,Google 表單自動產生,用來防止同一列重複匯入
  客戶姓名
  客戶電話 — 必填,比對既有客戶的依據
  收件地址 — 選填
  付款方式 — 選填,要跟系統裡的付款方式用字一樣(現金/銀行匯款/貨到付款/行動支付/信用卡/平台代收/未收款)才會被帶入,對不上就留空
  備註 — 選填
  其餘每一欄:欄名完全等於某個「在售」商品的品名或 SKU,就當成那個商品的數量欄
  (表單端建議用下拉/核取方塊列出商品,這樣欄名就是家易自己設定好的,不會有打字誤植的問題)
"""
import csv
import io
import re
import datetime as dt

from db import q

try:
    import openpyxl
except ImportError:  # 還沒裝套件時,先讓 CSV 路徑能用,xlsx 上傳會在 parse_file 給出清楚錯誤
    openpyxl = None

META_HEADERS = {
    "timestamp": ["時間戳記", "Timestamp"],
    "customer_name": ["客戶姓名"],
    "phone": ["客戶電話"],
    "address": ["收件地址"],
    "payment_method": ["付款方式"],
    "note": ["備註"],
}
ALL_META_ALIASES = {alias for names in META_HEADERS.values() for alias in names}


def _meta_key_for(header):
    for key, aliases in META_HEADERS.items():
        if header in aliases:
            return key
    return None


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
    (re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"), lambda m: (int(m[3]), int(m[1]), int(m[2]))),
]


def parse_date(raw):
    """從時間戳記字串(或 xlsx 的 datetime 物件)盡量抓出 YYYY-MM-DD;抓不到回傳 None。"""
    if isinstance(raw, (dt.datetime, dt.date)):
        return raw.strftime("%Y-%m-%d")
    s = str(raw or "")
    for pat, build in _DATE_PATTERNS:
        m = pat.search(s)
        if m:
            try:
                y, mo, d = build(m)
                return dt.date(y, mo, d).isoformat()
            except ValueError:
                continue
    return None


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
    headers = [h.strip() for h in (reader.fieldnames or [])]
    raw_rows = [row for row in reader if any((v or "").strip() for v in row.values())]
    return headers, raw_rows


def match_rows(headers, raw_rows, pay_methods):
    """逐列比對客戶(電話)跟商品(欄名對品名/SKU),標出可匯入/待確認(新客戶)/有問題/已匯入過。"""
    product_cols = {}       # header -> product_id
    unmatched_headers = []
    for h in headers:
        h = (h or "").strip()
        if not h or h in ALL_META_ALIASES:
            continue
        unmatched_headers.append(h)  # 之後找到比對就移除

    products = q("SELECT product_id, sku, name FROM product WHERE status='在售'")
    by_name = {p["name"]: p["product_id"] for p in products}
    by_sku = {p["sku"]: p["product_id"] for p in products}
    for h in list(unmatched_headers):
        pid = by_name.get(h) or by_sku.get(h)
        if pid:
            product_cols[h] = pid
            unmatched_headers.remove(h)

    customers = q("SELECT customer_id, display_name, phone FROM customer WHERE phone IS NOT NULL AND phone<>''")
    phone_map = {}
    for c in customers:
        phone_map.setdefault(normalize_phone(c["phone"]), c)

    existing_refs = {r["source_ref"] for r in q(
        "SELECT source_ref FROM \"order\" WHERE source_ref LIKE 'gform:%'")}

    rows = []
    ok_n = warn_n = err_n = dup_n = 0
    for raw in raw_rows:
        def meta(key):
            for alias in META_HEADERS[key]:
                if alias in raw and raw[alias] not in (None, ""):
                    return raw[alias]
            return None

        ts_raw = meta("timestamp")
        name = str(meta("customer_name") or "").strip()
        phone_raw = _phone_from_cell(meta("phone"))
        address = str(meta("address") or "").strip()
        pm_raw = str(meta("payment_method") or "").strip()
        payment_method = pm_raw if pm_raw in pay_methods else None
        note = str(meta("note") or "").strip()

        lines = []
        for h, pid in product_cols.items():
            v = raw.get(h)
            if v is None or v == "":
                continue
            try:
                qty = float(v)
            except (TypeError, ValueError):
                continue
            if qty > 0:
                lines.append({"product_id": pid, "name": next(p["name"] for p in products if p["product_id"] == pid), "qty": qty})

        row = dict(raw_timestamp=str(ts_raw or ""), customer_name=name, phone_raw=str(meta("phone") or ""),
                   phone=phone_raw, address=address, payment_method=payment_method, note=note, lines=lines,
                   status=None, error_reason=None, matched_customer_id=None, matched_customer_name=None,
                   source_ref=None, order_date=None)

        if not ts_raw:
            row["status"], row["error_reason"] = "error", "缺少時間戳記,沒辦法防止重複匯入"
            err_n += 1
            rows.append(row); continue
        source_ref = f"gform:{ts_raw}"
        row["source_ref"] = source_ref
        row["order_date"] = parse_date(ts_raw) or dt.date.today().isoformat()
        if source_ref in existing_refs:
            row["status"] = "duplicate"
            dup_n += 1
            rows.append(row); continue
        if not phone_raw:
            row["status"], row["error_reason"] = "error", "缺少電話,無法比對客戶"
            err_n += 1
            rows.append(row); continue
        if not lines:
            row["status"], row["error_reason"] = "error", "沒有勾選任何商品,或商品數量都是 0"
            err_n += 1
            rows.append(row); continue

        match = phone_map.get(normalize_phone(phone_raw))
        if match:
            row["status"] = "ok"
            row["matched_customer_id"] = match["customer_id"]
            row["matched_customer_name"] = match["display_name"]
            ok_n += 1
        else:
            row["status"] = "new_customer"
            warn_n += 1
        rows.append(row)

    return dict(rows=rows, unmatched_headers=unmatched_headers,
                summary=dict(ok=ok_n, warn=warn_n, err=err_n, dup=dup_n, total=len(rows)))
