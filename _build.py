# -*- coding: utf-8 -*-
import json, re, datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

RAW = json.load(open('_all.json', encoding='utf-8'))

def parse_date(v):
    if v is None: return None, ''
    if isinstance(v, str):
        if v.strip() in ('-', ''): return None, '原值:-'
        m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', v)
        if m:
            return datetime.date(int(m[1]), int(m[2]), int(m[3])), ''
        return None, f'原值:{v}'
    if isinstance(v, (int, float)):
        s = str(int(v))
        if len(s) == 8:
            try:
                return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8])), ''
            except ValueError:
                return None, f'原值:{s}'
        return None, f'原值:{s}'
    return None, ''

def split_customer(name):
    if name is None: return '', ''
    n = name.replace('\t', '').strip()
    m = re.match(r'^(.*?)[（(](.+?)[）)]\s*$', n)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    if '/' in n:
        a, b = n.split('/', 1)
        return a.strip(), b.strip().rstrip('/')
    return n, ''

def parse_pay(v):
    """回傳 (訂單類別, 付款方式, 匯款銀行)"""
    if v is None:
        return '銷售', '', ''
    v = v.strip()
    if v == '公關':
        return '公關', '', ''
    if v in ('公關(捐獻)', '捐獻', '捐贈'):
        return '捐贈', '', ''
    if v == '補償':
        return '補償', '', ''
    bank = ''
    if '郵局' in v: bank = '郵局'
    elif '合庫' in v: bank = '合庫'
    methods = []
    if '匯款' in v: methods.append('匯款')
    if '現金' in v: methods.append('現金')
    if '貨到付款' in v: methods.append('貨到付款')
    if '藍星金流' in v: methods.append('藍星金流')
    return '銷售', '+'.join(methods) if methods else v, bank

PROMO_KEYS = ['圓緣不絕', '批發優惠', '團媽優惠', '團購優惠', '老客戶優惠',
              '擴散教育', '活動消費', '批發']

def parse_remark(v):
    """回傳 (優惠方案, 折讓金額, 贈品, 其他備註)"""
    if v is None:
        return '', None, '', ''
    parts = re.split(r'[、,，/]\s*', v.strip())
    promos, gifts, others = [], [], []
    discount = None
    for p in parts:
        p = p.strip()
        if not p:
            continue
        md = re.search(r'折讓\s*(\d+)', p)
        if md:
            discount = int(md.group(1))
            continue
        if p in PROMO_KEYS:
            promos.append(p)
            continue
        if p.startswith('贈') or '蜂蜜' in p or '桂圓棒' in p:
            gifts.append(p)
            continue
        others.append(p)
    return '/'.join(promos), discount, '/'.join(gifts), '/'.join(others)

def parse_logi(v):
    """回傳 (物流方式, 物流商, 自送地點)"""
    if v is None:
        return '', '', ''
    v = v.replace(' ', '').strip()
    if v in ('樣品', '禮盒樣品'):
        return '', '', ''          # 這其實是備註，非物流；併入其他備註於外層處理
    m = re.match(r'^自送[（(](.+?)[）)]$', v)
    if m:
        return '自送', '', m.group(1)
    if v == '自送':
        return '自送', '', ''
    if v == '自取':
        return '自取', '', ''
    if v.startswith('中華郵政'):
        return '宅配', v, ''
    if v.startswith('7-11') or v.startswith('7-ELEVEN'):
        return '超商取貨', '7-11', ''
    if v.startswith('全家'):
        return '超商取貨', '全家', ''
    return v, '', ''

HEADERS = ['產季', '年月', '原項次', '日期', '客戶', '客戶來源/單位', '品項', '規格',
           '保存期限', '保存期限原註', '數量', '單價', '合計', '數量×單價',
           '訂單類別', '付款方式', '匯款銀行', '方案', '折讓金額', '贈品',
           '包裝/其他備註', '物流方式', '物流商', '自送地點',
           '運費_向客收取(推測)', '運費_成本(推測)', '資料檢核']

def clean(v):
    return v.strip() if isinstance(v, str) else v

rows_out = []
dup_index = {}
for season, rows in RAW.items():
    for r in rows:
        (idx, dt, cust, item, spec, exp, qty, price, total, pay, remark, logi,
         m_col, n_col) = (r + [None] * 14)[:14]
        if item is None:            # 沒有品項 = 未成交，跳過（依老闆指示）
            continue
        d, dnote = parse_date(dt)
        e, enote = parse_date(exp)
        cname, csrc = split_customer(cust)
        cat, method, bank = parse_pay(pay)
        promo, disc, gift, other = parse_remark(remark)
        lmethod, lprov, lplace = parse_logi(logi)
        if logi and logi.replace(' ', '') in ('樣品', '禮盒樣品'):
            other = (other + '/' if other else '') + logi.replace(' ', '')
        spec_clean = clean(spec)
        calc = qty * price if (qty is not None and price is not None) else None

        # 年月：由「日期」推出（YYYY-MM），供日曆時間彙總／畫圖
        ym = d.strftime('%Y-%m') if d else ''

        flags = []
        if calc is not None and total is not None and round(calc, 1) != round(total, 1):
            flags.append('金額不符(數量×單價≠合計)')
        if d and e and e < d:
            flags.append('保存期限早於銷售日')
        key = (season, str(dt), cname, csrc, item, spec_clean, qty, price)
        if key in dup_index:
            flags.append(f'疑似重複(對照原項次{dup_index[key]})')
        else:
            dup_index[key] = idx

        rows_out.append([
            season, ym, idx, d, clean(cname), clean(csrc), clean(item), spec_clean,
            e, (dnote or enote), qty, price, total, calc,
            cat, method, bank, promo, disc, gift,
            clean(other), lmethod, lprov, clean(lplace),
            m_col, n_col, ' / '.join(flags)
        ])

# 依 產季 → 日期 → 原項次 排序，順一下原檔的亂序
rows_out.sort(key=lambda r: (r[0], r[3] or datetime.date.max, r[2]))

# 項次重號偵測
from collections import Counter
seen_idx = {}
for row in rows_out:
    seen_idx.setdefault((row[0], row[2]), []).append(row)
for (season, idx), grp in seen_idx.items():
    if len(grp) > 1:
        for row in grp:
            row[-1] = (row[-1] + ' / ' if row[-1] else '') + '項次重號'

wb = openpyxl.Workbook()
ws = wb.active
ws.title = '銷售明細'
ws.append(HEADERS)
for row in rows_out:
    ws.append(row)

# 樣式
bold = Font(bold=True, color='FFFFFF')
fill = PatternFill('solid', fgColor='4F6228')
for c in ws[1]:
    c.font = bold; c.fill = fill; c.alignment = Alignment(horizontal='center')
ws.freeze_panes = 'A2'
widths = [8,7,7,11,22,20,7,12,12,14,6,7,9,9,9,11,9,11,8,14,24,10,16,22,14,14,40]
for i, w in enumerate(widths, 1):
    ws.column_dimensions[get_column_letter(i)].width = w
for rr in ws.iter_rows(min_row=2):
    for c in rr:
        h = HEADERS[c.column - 1]
        if h in ('日期', '保存期限') and isinstance(c.value, datetime.date):
            c.number_format = 'yyyy-mm-dd'
warn = PatternFill('solid', fgColor='FCE4D6')
col_chk = len(HEADERS)
for rr in ws.iter_rows(min_row=2):
    if rr[col_chk - 1].value:
        for c in rr:
            c.fill = warn

# 待確認分頁
ws2 = wb.create_sheet('待確認')
ws2.append(['產季', '原項次', '日期', '客戶', '品項', '規格',
            '數量', '單價', '合計', '問題說明'])
for c in ws2[1]:
    c.font = bold; c.fill = fill
for row in rows_out:
    if row[-1]:
        ws2.append([row[0], row[2], row[3], row[4], row[6], row[7],
                    row[10], row[11], row[12], row[-1]])
for i, w in enumerate([7,7,11,22,7,12,6,7,9,44], 1):
    ws2.column_dimensions[get_column_letter(i)].width = w
ws2.freeze_panes = 'A2'

# 欄位說明分頁
ws3 = wb.create_sheet('欄位說明')
notes = [
 ['欄位', '說明 / 拆解來源'],
 ['產季', '桂圓採收／焙製的批次年，值為原檔工作表名稱（2024 / 2025）。台灣中寮龍眼 3–4 月開花、7–9 月採收、9–10 月焙成桂圓，故一個產季的貨從當年 9 月一路賣到隔年春夏；2025 產季即含 2025-09 至 2026-03 的銷售。'],
 ['年月', '由「日期」推出的 YYYY-MM，供依日曆月份／年份彙總、畫趨勢圖（產季做不到這件事）。'],
 ['原項次', '對應原檔「項次」，保留以利對回原檔。原檔有重號（2025 產季兩個 102）已於資料檢核標記。原檔亂序之列已重新依日期排好。'],
 ['日期', '原「日期」，統一為日期格式。'],
 ['客戶', '由原「客戶」拆出：括號 / 斜線前的主體名稱。已移除跳位字元與多餘空白。'],
 ['客戶來源/單位', '由原「客戶」拆出：括號內或斜線後的來源、單位、通路（如 官網、台灣亮起來客人、育成村客人）。'],
 ['品項', '原「品項」。原檔空白（未成交）之列已整列排除。'],
 ['規格', '原「規格」原樣保留（僅去空白），定義由老闆維護。'],
 ['保存期限', '原「保存期限」統一為日期。原為文字 YYYYMMDD 者已轉換。'],
 ['保存期限原註', '原值為「-」「散裝」或無法解析者，保留原字串於此欄。'],
 ['數量 / 單價 / 合計', '原欄位。'],
 ['數量×單價', '系統重算，供比對「合計」是否正確。'],
 ['訂單類別', '由原「交易方式」拆出：銷售 / 公關 / 捐贈 / 補償。'],
 ['付款方式', '由原「交易方式」拆出：現金 / 匯款 / 貨到付款 / 藍星金流（可複選以 + 連接）。'],
 ['匯款銀行', '由原「交易方式」拆出：郵局 / 合庫。'],
 ['方案', '由原「備考」拆出：圓緣不絕 / 批發優惠 / 團媽優惠 / 團購優惠 / 老客戶優惠 / 擴散教育 / 活動消費 / 批發。'],
 ['折讓金額', '由原「備考」中「折讓NN」擷取為數字。'],
 ['贈品', '由原「備考」拆出：桂圓棒、蜂蜜等贈品字樣。'],
 ['包裝/其他備註', '原「備考」其餘內容（500g、新包裝、300g包裝*8、新包裝貼紙、提袋、農民收據、裸裝、官網、樣品…）。'],
 ['物流方式', '由原「物流」歸類：自送 / 自取 / 宅配 / 超商取貨。'],
 ['物流商', '由原「物流」拆出：中華郵政 / 中華郵政(I郵箱) / 7-11 / 全家。'],
 ['自送地點', '原「物流」為「自送(XXX)」時，括號內地點。'],
 ['運費_向客收取(推測)', '原檔無標題 M 欄。老闆推測為「向客戶收取之運費」，尚待確認。'],
 ['運費_成本(推測)', '原檔無標題 N 欄。老闆推測為「實際運費成本」，尚待確認。'],
 ['資料檢核', '系統標記：金額不符 / 疑似重複 / 項次重號 / 保存期限早於銷售日。詳見「待確認」分頁。'],
]
for row in notes:
    ws3.append(row)
for c in ws3[1]:
    c.font = bold; c.fill = fill
ws3.column_dimensions['A'].width = 22
ws3.column_dimensions['B'].width = 95
for rr in ws3.iter_rows(min_row=2):
    rr[1].alignment = Alignment(wrap_text=True, vertical='top')

wb.save('2025桂圓銷售_整理版.xlsx')

# 摘要
sale = [r for r in rows_out if r[14] == '銷售' and (r[12] or 0) > 0]
print('明細列數(有品項):', len(rows_out))
print('  其中 訂單類別=銷售 且 合計>0 :', len(sale))
from collections import Counter as _C
print('  依產季:', dict(sorted(_C(r[0] for r in rows_out).items())))
print('  依年月:', dict(sorted(_C(r[1] for r in rows_out).items())))
print('待確認列數:', len([r for r in rows_out if r[-1]]))
print('銷售金額合計:', sum(r[12] for r in sale))
