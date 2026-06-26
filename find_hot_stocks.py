import yfinance as yf
import pandas as pd
import concurrent.futures
import warnings
import requests
import time
import random
import json
import os
from datetime import datetime, timezone, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

warnings.filterwarnings("ignore")

global_session = requests.Session()
retry = Retry(connect=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retry)
global_session.mount('http://', adapter)
global_session.mount('https://', adapter)
global_session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

def get_all_tw_tickers():
    tickers = {}
    try:
        modes = {'2': '.TW', '4': '.TWO'}
        for mode, suffix in modes.items():
            url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
            res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            df = pd.read_html(res.text)[0]
            df.columns = df.iloc[0]
            df = df.iloc[1:]
            for _, row in df.iterrows():
                raw_data = str(row['有價證券代號及名稱'])
                parts = raw_data.split(' ')
                if len(parts) >= 2:
                    code = parts[0].strip()
                    name = parts[1].strip()
                    if len(code) == 4 and code.isdigit():
                        tickers[f"{code}{suffix}"] = name
    except Exception as e:
        print(f"代號獲取異常: {e}")
    return tickers

def check_stock(ticker, name):
    try:
        time.sleep(random.uniform(0.1, 0.5)) 
        
        data = yf.download(ticker, period="1y", progress=False, session=global_session)
        if len(data) < 200: return None
        if isinstance(data.columns, pd.MultiIndex): data.columns = data.columns.droplevel(1)

        data['MA10'] = data['Close'].rolling(window=10).mean()
        data['MA20'] = data['Close'].rolling(window=20).mean()
        data['MA60'] = data['Close'].rolling(window=60).mean()
        data['VMA5'] = data['Volume'].rolling(window=5).mean()
        
        data['TR'] = pd.concat([
            data['High'] - data['Low'],
            (data['High'] - data['Close'].shift(1)).abs(),
            (data['Low'] - data['Close'].shift(1)).abs()
        ], axis=1).max(axis=1)
        data['ATR'] = data['TR'].rolling(window=14).mean()

        latest = data.iloc[-1]
        prev = data.iloc[-2] if len(data) > 1 else latest
        
        daily_return = ((latest['Close'] - prev['Close']) / prev['Close']) * 100
        volume_lots = latest['Volume'] / 1000

        # === 策略 A：流動性門檻調高至 500 張 ===
        tech_a_pass = (latest['Close'] >= data['Close'].tail(200).max()) and (500 <= volume_lots <= 5000)

        # === 策略 B ===
        cond_b_price = latest['Close'] > 10
        cond_b_liquidity = latest['VMA5'] / 1000 > 100
        
        ma_max = max(latest['MA10'], latest['MA20'], latest['MA60'])
        ma_min = min(latest['MA10'], latest['MA20'], latest['MA60'])
        cond_b_ma_convergence = (ma_max / ma_min) < 1.05 and (latest['Close'] > ma_max)
        cond_b_vol_amp = latest['Volume'] > (latest['VMA5'] * 2)
        cond_b_atr_expand = latest['ATR'] > data['ATR'].iloc[-5:-1].mean()
        
        amplitude = latest['High'] - latest['Low']
        if amplitude > 0:
            body_ratio = (latest['Close'] - latest['Open']) / amplitude
            upper_shadow_ratio = (latest['High'] - latest['Close']) / amplitude
            cond_b_kline = (latest['Close'] > latest['Open']) and (body_ratio > 0.5) and (upper_shadow_ratio < 0.3) and (latest['High'] > latest['Low'])
        else:
            cond_b_kline = False

        tech_b_pass = cond_b_price and cond_b_liquidity and cond_b_ma_convergence and cond_b_vol_amp and cond_b_atr_expand and cond_b_kline

        if not (tech_a_pass or tech_b_pass):
            return None

        stock = yf.Ticker(ticker)
        info = stock.info
        capital = (info.get('sharesOutstanding') or 0) * 10
        book_value = info.get('bookValue') or 0
        
        # 新增實戰指標：營收 YoY 與月線乖離率
        rev_growth = info.get('revenueGrowth')
        rev_growth_pct = round(rev_growth * 100, 2) if rev_growth is not None else None
        bias_20 = ((latest['Close'] - latest['MA20']) / latest['MA20']) * 100

        is_strategy_a = tech_a_pass and (capital < 1_000_000_000) and (book_value > 5)
        is_strategy_b = tech_b_pass and (capital < 5_000_000_000)

        if is_strategy_a or is_strategy_b:
            industry_en = info.get('industry', '未知')
            ind_map = {
                'Semiconductors': '半導體', 'Electronic Components': '電子零組件', 
                'Computer Hardware': '電腦及週邊', 'Communication Equipment': '通信網路',
                'Electronic Gaming & Multimedia': '光電業', 'Consumer Electronics': '消費電子',
                'Auto Parts': '汽車零組件', 'Biotechnology': '生技醫療',
                'Metal Fabrication': '金屬製造', 'Specialty Industrial Machinery': '特殊工業機械',
                'Tools & Accessories': '工具與配件', 'Electronics & Computer Distribution': '電子電腦通路',
                'Specialty Retail': '專賣零售', 'Software - Application': '軟體應用',
                'Internet Content & Information': '網路資訊'
            }
            industry = ind_map.get(industry_en, industry_en)

            vol_ratio = latest['Volume'] / latest['VMA5'] if latest['VMA5'] > 0 else 0
            if vol_ratio >= 3: heat = "極高"
            elif vol_ratio >= 1.5: heat = "高"
            else: heat = "一般"

            exchange = "TWSE" if ticker.endswith(".TW") else "TPEX"

            return {
                'stock_data': {
                    '交易所': exchange,
                    '股票代號': ticker.split('.')[0],
                    '股票名稱': name,
                    '產業面': industry,
                    '熱門程度': heat,
                    '現價': round(float(latest['Close']), 2),
                    '單日漲跌幅(%)': round(float(daily_return), 2),
                    '成交量(張)': int(volume_lots),
                    '乖離率(%)': round(float(bias_20), 2),
                    '營收YoY(%)': rev_growth_pct,
                    '進榜天數': 1
                },
                'is_a': is_strategy_a,
                'is_b': is_strategy_b
            }
    except Exception:
        pass
    return None

def get_streak(code, list_name, prev_data):
    prev_list = prev_data.get(list_name, [])
    for stock in prev_list:
        if stock.get('股票代號') == code:
            return stock.get('進榜天數', 1) + 1
    return 1

def main():
    # 1. 載入前次歷史資料
    previous_data = {"update_date": "無", "original_strategy": [], "ai_strategy": [], "intersection": []}
    
    # 【防呆機制】：確保檔案存在，且檔案大小大於 0 byte 才去讀取
    if os.path.exists('daily_hot_stocks.json') and os.path.getsize('daily_hot_stocks.json') > 0:
        try:
            with open('daily_hot_stocks.json', 'r', encoding='utf-8') as f:
                old_json = json.load(f)
                if "latest_data" in old_json:
                    previous_data = old_json["latest_data"]
                else:
                    # 舊版格式過渡處理
                    update_date = old_json.get("update_time", "").split(" ")[0] if "update_time" in old_json else "無"
                    previous_data = {
                        "update_date": update_date,
                        "original_strategy": old_json.get("original_strategy", []),
                        "ai_strategy": old_json.get("ai_strategy", []),
                        "intersection": old_json.get("intersection", [])
                    }
        except Exception as e:
            print(f"讀取舊檔失敗: {e}")

    # 2. 獲取代號並掃描
    tickers_dict = get_all_tw_tickers()
    if not tickers_dict: 
        print("【警告】無法獲取全市場代號，啟用防崩潰備用清單 (共 3 檔)。")
        tickers_dict = {'2330.TW': '台積電', '2317.TW': '鴻海', '2454.TW': '聯發科'}
    else:
        print(f"【執行確認】成功獲取市場代號，開始掃描共 {len(tickers_dict)} 檔標的...")
        
    results_intersection = []
    results_original = []
    results_ai = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(check_stock, t, n): t for t, n in tickers_dict.items()}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                s_data = res['stock_data']
                if res['is_a']: results_original.append(s_data)
                if res['is_b']: results_ai.append(s_data)
                if res['is_a'] and res['is_b']: results_intersection.append(s_data)

    # 3. 計算連續進榜天數
    for s in results_original: s['進榜天數'] = get_streak(s['股票代號'], 'original_strategy', previous_data)
    for s in results_ai: s['進榜天數'] = get_streak(s['股票代號'], 'ai_strategy', previous_data)
    for s in results_intersection: s['進榜天數'] = get_streak(s['股票代號'], 'intersection', previous_data)

    # 4. 輸出雙層結構 JSON
    tz_tw = timezone(timedelta(hours=8))
    now = datetime.now(tz_tw)
    update_time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    update_date_str = now.strftime("%Y-%m-%d")

    latest_data = {
        "update_date": update_date_str,
        "original_strategy": results_original,
        "ai_strategy": results_ai,
        "intersection": results_intersection
    }

    output_data = {
        "update_time": update_time_str,
        "latest_data": latest_data,
        "previous_data": previous_data
    }

    with open('daily_hot_stocks.json', 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=4)
        
    print(f"掃描完成。交集: {len(results_intersection)} 檔, 原始: {len(results_original)} 檔, AI量化: {len(results_ai)} 檔。")

if __name__ == "__main__":
    main()
