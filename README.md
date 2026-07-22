# MACRO LENS — 自動抓數 Pipeline 設定說明

這個 repo 的用途：每天固定時間自動呼叫 FinMind / Twelve Data / FRED，
把 TAIEX、SPX、VIX 的 OHLC 與台股籌碼面數據算好、存成一個 JSON 檔。
Claude 之後直接讀這個 JSON 檔（透過 `raw.githubusercontent.com`），
不需要再由 Claude 本身去連線這些 API（Claude 的執行環境連不到這些網域）。

**金鑰只會存在 GitHub 的 Secrets 裡，永遠不會出現在 commit 紀錄或這個
repo 任何看得到的檔案中**，所以即使把 repo 設成 Public 也不會外流金鑰。

---

## 一次性設定步驟

### 1. 建立新 Repo
到 GitHub 建一個新 repository，名稱自訂（例如 `macro-lens-data`）。
**建議設為 Public**（原因見下方「為什麼要 Public」）。

### 2. 上傳這三個檔案（保持資料夾結構）
```
.github/workflows/fetch_market_data.yml
scripts/fetch_data.py
scripts/requirements.txt
data/market_data.json   ← 這是 placeholder，第一次執行後會被覆蓋
```
可以直接把整個資料夾拖拉上傳（GitHub 網頁的 "Add file → Upload files" 支援拖拉整個資料夾結構），或用 git clone 後複製檔案進去再 push。

### 3. 設定四把金鑰（Settings → Secrets and variables → Actions → New repository secret）
新增以下四個 secret，name 要完全一致：

| Secret 名稱 | 值 |
|---|---|
| `FINMIND_TOKEN` | 你的 FinMind token |
| `TWELVE_DATA_KEY` | 你的 Twelve Data key |
| `FRED_API_KEY` | 你的 FRED key |
| `FINNHUB_KEY` | 你的 Finnhub key |

### 4. 手動測試一次
到 repo 的 **Actions** 分頁 → 左側選 "Fetch Macro Lens Market Data" →
右上角 "Run workflow" → 手動觸發一次。

等它跑完（約 30 秒~1 分鐘），檢查：
- ✅ 綠勾勾代表整個 workflow 沒有崩潰
- 點進去看 log，確認 `TAIEX: N 筆, error=...` 這幾行 — 如果某個來源 error 不是 None，代表那個資料源需要微調（見下方「常見除錯」）
- 檢查 `data/market_data.json` 是否已經被自動 commit 更新（不再是 placeholder 內容）

### 5. 之後怎麼用
把這個 repo 的 raw 檔案網址告訴 Claude，格式是：
```
https://raw.githubusercontent.com/你的帳號/repo名稱/main/data/market_data.json
```
之後執行 Mode W 時，Claude 直接 `web_fetch` 這個網址就能拿到最新數據，
不需要重新設定，也不需要你再手動貼數字。

---

## 為什麼要 Public

`raw.githubusercontent.com` 讀取 **private** repo 的檔案需要帶身分驗證（token），
而 Claude 的網路白名單雖然開放這個網域，但沒有你的 GitHub 帳號權杖可以用。
設為 Public 可以讓 Claude 直接讀到檔案，不需要任何驗證。

這個 repo 裡**只有算好的市場公開數據**（TAIEX/SPX/VIX 收盤價、法人買賣超等），
不含你的部位、金額、策略邏輯，公開沒有實質風險。真正敏感的是那四把 API
金鑰，而金鑰只活在 GitHub Secrets 裡，Public 的是 repo 內容，不是 Secrets。

如果你還是希望 repo 私有，也可以，只是屆時 Claude 沒辦法直接 fetch，
你需要每次手動把 `data/market_data.json` 的內容貼到對話裡。

---

## 常見除錯

跑完第一次後，如果某個欄位一直是空的或 error 不為 null，把 Actions 執行紀錄
（log 全文）貼給 Claude，讓 Claude 依照 FinMind/Twelve Data 實際回傳的
JSON 結構調整 `scripts/fetch_data.py` 裡對應的 parse 邏輯即可 —— 這是正常的
第一輪校準，不代表整個架構有問題。已知比較可能需要微調的地方：

- **TAIEX OHLC**：FinMind 對「指數」歷史資料的 dataset 掛法，文件描述跟實際
  行為可能有落差，腳本已內建兩種嘗試順序，若都失敗，備援方案是改接
  TWSE 官方 open API（`https://www.twse.com.tw/exchangeReport/MI_INDEX`），
  不需金鑰。
- **法人買賣超 / 融資餘額 / 期貨 OI**：這三項腳本目前是把 FinMind 原始資料
  整包存下來（`raw_last_14d`），刻意不先做加總判斷，避免欄位名稱猜錯導致
  誤判。Claude 在讀取時會直接解析這包原始 JSON。

---

## 排程時間（可自行調整）

目前設定：
- `10 6 * * 1-5`（UTC）= 台北時間 14:10，台股收盤後
- `45 21 * * 1-5`（UTC）= 美股收盤後（涵蓋夏令/冬令時間差）

如果想改時間，編輯 `.github/workflows/fetch_market_data.yml` 裡的 cron
表達式即可，改完直接 commit，下次排程就會套用新時間。
