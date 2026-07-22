#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACRO LENS / Mode W 資料抓取腳本
=================================
用途：在 GitHub Actions 排程中執行，呼叫 FinMind / Twelve Data / FRED 抓取
台股 + 美股的 OHLC 與籌碼面數據，整理成單一 JSON 檔寫入 data/market_data.json，
供 Claude 之後透過 raw.githubusercontent.com 讀取（不需 API 金鑰，因為金鑰只存在
GitHub Secrets，只在 Action 執行環境內使用，不會出現在輸出檔案或 commit 紀錄中）。

環境變數（由 GitHub Actions workflow 從 Secrets 注入）：
    FINMIND_TOKEN
    TWELVE_DATA_KEY
    FRED_API_KEY
    FINNHUB_KEY   （目前腳本未使用，保留給未來擴充，例如美股 Fear & Greed 替代源）

設計原則：
- 每一個資料來源獨立 try/except，單一來源失敗不影響其他來源
- 失敗時記錄 "error" 欄位，不中斷、不假造數字
- 第一次實際執行後，如果欄位對不上（FinMind/Twelve Data 部分 dataset 的實際
  回傳格式可能與文件描述有出入），請把 Actions 執行紀錄（log）貼給 Claude，
  由 Claude 依實際 JSON 結構修正 parse 邏輯。這是正常的第一輪除錯，不是設計錯誤。
"""

import os
import json
import datetime
import traceback

import requests

FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_KEY", "")
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "market_data.json")

TODAY = datetime.date.today()
START_DATE = (TODAY - datetime.timedelta(days=180)).isoformat()  # 抓半年份，足夠算 60MA


def finmind_get(dataset, data_id=None, start_date=START_DATE, end_date=None):
    """呼叫 FinMind /data 端點，回傳 list of dict（失敗時丟例外，由呼叫端 try/except 接住）"""
    headers = {"Authorization": f"Bearer {FINMIND_TOKEN}"}
    params = {"dataset": dataset, "start_date": start_date}
    if data_id:
        params["data_id"] = data_id
    if end_date:
        params["end_date"] = end_date
    resp = requests.get(FINMIND_URL, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if "data" not in payload:
        raise ValueError(f"FinMind 回傳格式異常，無 data 欄位: {payload}")
    return payload["data"]


def fetch_taiex_ohlc():
    """
    TAIEX 加權指數 OHLC。
    FinMind 對「指數」的歷史 OHLC 支援方式在不同 dataset 間不完全一致，
    這裡依序嘗試兩種常見掛法，都失敗則回報錯誤（之後可改接 TWSE 官方
    open API https://www.twse.com.tw/exchangeReport/MI_INDEX 作為備援，
    該端點不需金鑰）。
    """
    attempts = [
        {"dataset": "TaiwanStockPrice", "data_id": "TAIEX"},
        {"dataset": "TaiwanStockTotalReturnIndex", "data_id": "TAIEX"},
    ]
    last_err = None
    for attempt in attempts:
        try:
            raw = finmind_get(**attempt)
            if not raw:
                continue
            ohlc = []
            for row in raw:
                # FinMind 常見欄位為 open/max/min/close 或 open/high/low/close，兩種都嘗試
                o = row.get("open")
                h = row.get("max", row.get("high"))
                l = row.get("min", row.get("low"))
                c = row.get("close", row.get("price"))
                v = row.get("Trading_Volume", row.get("volume", 0))
                if o is None or h is None or l is None or c is None:
                    continue
                ohlc.append({
                    "date": row.get("date"),
                    "open": float(o), "high": float(h), "low": float(l),
                    "close": float(c), "volume": float(v) if v else 0,
                })
            if ohlc:
                return {"ohlc": ohlc, "source": attempt["dataset"], "error": None}
        except Exception as e:
            last_err = f"{attempt['dataset']}: {e}"
            continue
    return {"ohlc": [], "source": None, "error": last_err or "所有嘗試皆無有效資料"}


def fetch_institutional_flow():
    """整體三大市場法人買賣表（原始資料整包保留，5日彙總留給下游解析，避免欄位誤判）"""
    try:
        raw = finmind_get(
            dataset="TaiwanStockTotalInstitutionalInvestors",
            start_date=(TODAY - datetime.timedelta(days=14)).isoformat(),
        )
        return {"raw_last_14d": raw, "error": None}
    except Exception as e:
        return {"raw_last_14d": [], "error": str(e)}


def fetch_margin_balance():
    """整體市場融資融劵餘額表"""
    try:
        raw = finmind_get(
            dataset="TaiwanStockTotalMarginPurchaseShortSale",
            start_date=(TODAY - datetime.timedelta(days=14)).isoformat(),
        )
        return {"raw_last_14d": raw, "error": None}
    except Exception as e:
        return {"raw_last_14d": [], "error": str(e)}


def fetch_futures_oi():
    """台指期三大法人未平倉（TX）"""
    try:
        raw = finmind_get(
            dataset="TaiwanFuturesInstitutionalInvestors",
            data_id="TX",
            start_date=(TODAY - datetime.timedelta(days=14)).isoformat(),
        )
        return {"raw_last_14d": raw, "error": None}
    except Exception as e:
        return {"raw_last_14d": [], "error": str(e)}


def fetch_twelve_data_series(symbol, outputsize=90):
    try:
        params = {
            "symbol": symbol,
            "interval": "1day",
            "outputsize": outputsize,
            "apikey": TWELVE_DATA_KEY,
        }
        resp = requests.get(TWELVE_DATA_URL, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        if "values" not in payload:
            raise ValueError(f"Twelve Data 回傳無 values 欄位: {payload}")
        ohlc = []
        for row in reversed(payload["values"]):  # Twelve Data 預設新到舊，轉成舊到新
            ohlc.append({
                "date": row.get("datetime"),
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row.get("volume", 0) or 0),
            })
        return {"ohlc": ohlc, "error": None}
    except Exception as e:
        return {"ohlc": [], "error": str(e)}


def fetch_fred_series(series_id, limit=400):
    try:
        params = {
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        }
        resp = requests.get(FRED_URL, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        obs = payload.get("observations", [])
        # FRED 缺值以 "." 表示，過濾掉
        clean = [o for o in obs if o.get("value") not in (".", None, "")]
        return clean
    except Exception as e:
        raise RuntimeError(f"{series_id}: {e}")


def fetch_fred_block():
    result = {"t10y2y_bps": None, "hy_oas_pct": None, "core_pce_yoy_pct": None, "error": None}
    errors = []
    try:
        obs = fetch_fred_series("T10Y2Y", limit=5)
        if obs:
            result["t10y2y_bps"] = round(float(obs[0]["value"]) * 100, 1)
            result["t10y2y_date"] = obs[0]["date"]
    except Exception as e:
        errors.append(str(e))

    try:
        obs = fetch_fred_series("BAMLH0A0HYM2", limit=10)
        if obs:
            result["hy_oas_pct"] = float(obs[0]["value"])
            result["hy_oas_date"] = obs[0]["date"]
            if len(obs) >= 6:
                result["hy_oas_5d_change_bps"] = round(
                    (float(obs[0]["value"]) - float(obs[5]["value"])) * 100, 1
                )
    except Exception as e:
        errors.append(str(e))

    try:
        # Core PCE YoY 需要抓 13 個月份資料自算年增率
        obs = fetch_fred_series("PCEPILFE", limit=15)
        if len(obs) >= 13:
            latest = float(obs[0]["value"])
            year_ago = float(obs[12]["value"])
            result["core_pce_yoy_pct"] = round((latest / year_ago - 1) * 100, 2)
            result["core_pce_date"] = obs[0]["date"]
    except Exception as e:
        errors.append(str(e))

    if errors:
        result["error"] = "; ".join(errors)
    return result


def main():
    output = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "taiex": fetch_taiex_ohlc(),
        "institutional_flow": fetch_institutional_flow(),
        "margin_balance": fetch_margin_balance(),
        "futures_oi": fetch_futures_oi(),
        "spx": fetch_twelve_data_series("SPX"),
        "vix": fetch_twelve_data_series("VIX"),
        "fred": fetch_fred_block(),
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"寫入完成: {OUTPUT_PATH}")
    print(f"TAIEX: {len(output['taiex']['ohlc'])} 筆, error={output['taiex']['error']}")
    print(f"SPX: {len(output['spx']['ohlc'])} 筆, error={output['spx']['error']}")
    print(f"VIX: {len(output['vix']['ohlc'])} 筆, error={output['vix']['error']}")
    print(f"FRED error: {output['fred']['error']}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
