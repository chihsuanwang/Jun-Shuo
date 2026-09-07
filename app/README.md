# 桂圓帳房 · 本機營運管理平台

南投中寮 · 郡碩農創。FastAPI + SQLite,資料全部留在本機。
專案總說明 + 新電腦安裝步驟見 [上一層 README.md](../README.md)。

**要繼續施工**:先看 [docs/開發路線.md](docs/開發路線.md)(進度 / 決策 / 下一步)。

**其他文件**:
[docs/報稅參考.md](docs/報稅參考.md)(郡碩報稅現況)·
[docs/平台概觀.md](docs/平台概觀.md)(路演)·
[docs/使用手冊.md](docs/使用手冊.md)(操作)·
[docs/儀表板解讀.md](docs/儀表板解讀.md)(怎麼看數字)·
[docs/系統文件.md](docs/系統文件.md)(開發 / 維護 / 系統圖)

## 啟動

雙擊 **`啟動.bat`**。它會在 `app\.venv\` 建一個獨立虛擬環境、裝套件、
建資料庫與示範假資料,然後開瀏覽器。（電腦裝了多套 Python 也不會混到。）

手動(自行處理虛擬環境):

```
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python seed.py --force      # 建立 guiyuan_ledger.db + 假資料
python -m uvicorn main:app --port 8000
```

瀏覽器開 http://127.0.0.1:8000

## 檔案

| 檔 | 用途 |
|---|---|
| `schema.sql` | 資料庫結構(v2;產品線三層 + 既有 14 表) |
| `seed.py` | 建庫 + 灌示範假資料 |
| `guiyuan_ledger.db` | SQLite 資料庫(用 DB Browser for SQLite 可手動檢視) |
| `備份/` | 每次啟動自動複製一份當日的 `.db`,保留最近 30 份 |
| `db.py` / `queries.py` | 資料存取與儀表板 / 報表查詢 |
| `main.py` | FastAPI 路由 |
| `templates/` `static/` | 方向 A(帳房・宣紙)版型 |

## 目前有的畫面

- `/`　　　　儀表板;右上可切**產季**(2024/2025/全部)與**結算日**;「需要注意」每項可點
- `/reports`　六個頁籤:獲利分析 / 客戶分析(RFM) / 物流與運費 / 收款帳齡 / 批次與效期 / 跨產季比較
- `/orders`　訂單清單(可篩:貨款逾期 / 未收款 / 超過3天未出貨、搜尋)
- `/orders/{id}` 訂單明細:改收款/出貨資訊、「標記已收款」「標記已出貨」快捷鈕
- `/orders/new` 新增訂單(客戶必選;找不到→去新增客戶再回來;明細可加列/刪列)
- `/product-lines` 產品線:事業別 → 產品群組 → SKU 三層;改名、改稅別、SKU 歸類(v2 回合 1)
- `/customers`　客戶資料庫:清單 + 搜尋 → 客戶明細(基本資料、地址、往來紀錄、累計消費) → 編輯
- `/review`　待確認:補完資料標記已處理 / 忽略
- `/issues`　物流異常:選處理方式、可一鍵產生「理賠重寄」訂單
- `/products` 商品目錄:新增 / 編輯(含各客戶分級定價)
- `/batches`　焙製批次:新增 / 編輯,顯示產出 / 已出 / 剩餘 / 賣出比例
- `/channels` 銷售管道:新增 / 編輯(抽成、帳期)
- `/reports`　獲利分析(依商品 / 依銷售管道)

## 進行中 / 待做

**v2 改造 —— 產品線全成本損益 + 報稅支援。** 施工回合與進度見
[docs/開發路線.md](docs/開發路線.md)。回合 1(產品線三層結構)已完成。

> 資料庫中的數字目前皆為 `seed.py` 產生的假資料,僅供試跑。
