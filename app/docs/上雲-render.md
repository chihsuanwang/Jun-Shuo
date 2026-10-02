# 上雲:Render(Starter 方案)

> 目的:一個**固定的網址**,家易隨時連得進來用,不用開你的電腦。
>
> **2026-10-02 取代原本的 PythonAnywhere 方案**:PythonAnywhere 免費版只吃 WSGI,
> FastAPI 是 ASGI 框架,中間要靠 `a2wsgi` 橋接——這個組合在 PythonAnywhere 上會
> **卡死被 HARAKIRI 砍掉**(每個請求都沒有回應,是已知的相容性問題,不是我們設定錯),
> 兩次部署嘗試都是同樣的結果。詳細除錯過程見 [工作日誌.md](工作日誌.md) 2026-10-02 條目、
> 決策沿革見 [開發路線.md](開發路線.md) §六回合 11。
>
> **Render 原生支援 ASGI**,直接跑 `uvicorn main:app`,不用 `wsgi.py`/`a2wsgi` 那層橋接
> (那層正是卡死的原因),同時解掉了之前「雲端網站要怎麼跟上 GitHub 最新版」的問題——
> Render 內建 git push 自動重新部署,不用自己寫腳本或排程。
>
> **費用**:免費版的硬碟是暫存的(服務睡眠 / 重啟,SQLite 資料庫檔案會被清空),
> 我們需要資料庫一直留著,所以要用 **Starter 方案**($7 美金/月,512MB RAM)+
> **持久硬碟**($0.25 美金/GB/月,1GB 很夠用)——大約 **$7.25 美金/月**。
>
> 程式碼這邊早就備好了,跟原本 PythonAnywhere 那版共用同一套機制,不用重寫:
> `GY_DATA_DIR`(資料放程式碼目錄外)、`GY_PASSWORD`(整站共用密碼)、
> `ensure_db()`(首次自動建示範庫)。

---

## 1. 註冊

<https://render.com> —— 建議用 **GitHub 帳號登入**,等一下連 repo 比較方便。

## 2. 建立 Web Service

Dashboard 右上 **New** → **Web Service**。選 **Build and deploy from a Git repository**,
連接你的 GitHub 帳號(第一次要 Authorize Render 存取),選 `chihsuanwang/Jun-Shuo` 這個 repo。

## 3. 基本設定

| 欄位 | 填入 |
|---|---|
| **Name** | 自己取(會變成網址的一部分,例如 `guiyuan-ledger` → `guiyuan-ledger.onrender.com`) |
| **Region** | **Singapore**(離台灣最近,延遲較低) |
| **Root Directory** | `app`(程式碼在 repo 的 `app/` 子資料夾裡) |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| **Instance Type** | **Starter**($7/月——免費版沒有持久硬碟選項,不能選免費版) |

## 4. 加環境變數

往下找 **Environment Variables**,加三個:

| Key | Value |
|---|---|
| `GY_DATA_DIR` | `/data` |
| `GY_PASSWORD` | 你要給家易用的密碼(帳號欄隨便打都行) |
| `PYTHON_VERSION` | `3.11.11`(固定版本,避免跟本機 / 其他雲端機器不一致) |

## 5. 加持久硬碟

找 **Disks** 區塊 → **Add Disk**:

| 欄位 | 填入 |
|---|---|
| **Mount Path** | `/data` |
| **Size** | 1 GB |

(資料夾本身程式會自己建,不用先手動建好。)

## 6. 建立 + 部署

最下面按 **Create Web Service**。Render 會自動 clone repo、跑 build command、啟動——
第一次通常要等幾分鐘(裝套件 + 開機)。狀態變成綠色的 **Live** 就是部署成功。

## 7. 測試

開 Render 給的網址(`https://你取的名字.onrender.com`)→ 瀏覽器應該跳出帳號密碼的
小視窗 → 帳號隨便打、密碼填 Step 4 設的那組 → 進得去看到儀表板畫面就成功了。

資料庫是 `ensure_db()` 自動建的示範資料,不用自己跑 `seed.py`。

---

## 日常維護

**更新程式碼**:Render 預設 **Auto-Deploy 是開著的**——`git push` 到 `main` 分支,
Render 會自動偵測、自動重新部署,**不用做任何事**,幾分鐘後網站就是最新版。

想手動觸發(不想等自動偵測):Dashboard 該服務 → **Manual Deploy** → **Deploy latest commit**。

想暫停自動部署(怕推上去的東西還沒測好就直接上線):**Settings** 分頁 →
**Auto-Deploy** 關掉,之後都要自己按 Manual Deploy 才會更新。

**重置示範資料**:**Shell** 分頁(Starter 方案才有這個功能)開一個終端機,跑:
```bash
rm /data/guiyuan_ledger.db
```
再 Manual Deploy 重啟一次,`ensure_db()` 會自動重建。

**手動備份**:Render 目前沒有像 PythonAnywhere「Files 分頁直接下載」那種介面,
最簡單(但有點笨)的做法是在 Shell 裡把檔案轉成文字貼出來:
```bash
base64 /data/guiyuan_ledger.db
```
整段複製下來,本機用 `base64 -d` 轉回 `.db` 檔案。**這個步驟還沒有實際驗證過**,
之後真的要備份時再測一次;更乾淨的做法是之後在系統裡自己加一個「下載目前資料庫」
的路由(用 `GY_PASSWORD` 保護),有需要再提出來做。

**升級方案**:之後真的要更穩定 / 流量更大,Render 本身就有更高階的方案可以直接升,
不用像 Fly.io 那樣整個換平台。

---

## 遇到問題

| 狀況 | 看這裡 |
|---|---|
| 部署後開網站是 "Service Unavailable" / 502 | 該服務的 **Logs** 分頁(即時滾動的記錄),看最後面幾行錯誤;常見是 Start Command 打錯、或某個套件裝不起來 |
| 要看伺服器在跑什麼 / debug | **Logs** 分頁——跟 PythonAnywhere 分 Error log / Server log 兩份不同,Render 只有一份即時 log |
| 更新後網站看起來還是舊的 | 先確認 Auto-Deploy 真的有觸發(Dashboard 看部署紀錄),再試試瀏覽器無痕視窗(排除快取) |
| 密碼怎麼都登不進去 | 確認 `GY_PASSWORD` 環境變數有存對、存檔後有沒有觸發重新部署(改環境變數通常會自動重啟一次) |
| 想要自己的網域(不是 `onrender.com`) | **Settings** → **Custom Domain**,功能本身免費,但要自己已經有網域名稱 |
