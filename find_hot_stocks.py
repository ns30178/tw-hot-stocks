import yfinance as yf
import pandas as pd
import concurrent.futures
import warnings
import requests
import time
import random
import json
from datetime import datetime, timezone, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

warnings.filterwarnings("ignore")

# 建立全域共用的連線 Session
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
                parts = raw_data.split('　')
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
        time.sleep(random.uniform(0.1, 0.5)) # 共用 session 且減少 info 請求後，延遲可稍微縮短
        
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

        # === 策略 A 技術面初步判斷 ===
        tech_a_pass = (latest['Close'] >= data['Close'].tail(200).max()) and (50 < volume_lots < 5000)

        # === 策略 B 技術面初步判斷 ===
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

        # 關鍵優化：如果兩個策略的技術面都不符合，直接跳過，不浪費時間去抓 stock.info
        if not (tech_a_pass or tech_b_pass):
            return None

        # 以下只有技術面達標的少數股票會執行
        stock = yf.Ticker(ticker)
        info = stock.info
        capital = (info.get('sharesOutstanding') or 0) * 10
        book_value = info.get('bookValue') or 0
        eps = info.get('trailingEps')
        pe = info.get('trailingPE')

        is_strategy_a = tech_a_pass and (capital < 1_000_000_000) and (book_value > 5)
        is_strategy_b = tech_b_pass and (capital < 5_000_000_000)

        if is_strategy_a or is_strategy_b:
            industry_en = info.get('industry', '未知')
            ind_map = {
                'Semiconductors': '半導體', 'Electronic Components': '電子零組件', 
                'Computer Hardware': '電腦及週邊', 'Communication Equipment': '通信網路',
                'Electronic Gaming & Multimedia': '光電業', 'Consumer Electronics': '消費電子',
                'Auto Parts': '汽車零組件', 'Biotechnology': '生技醫療'
            }
            industry = ind_map.get(industry_en, industry_en)

            vol_ratio = latest['Volume'] / latest['VMA5'] if latest['VMA5'] > 0 else 0
            if vol_ratio >= 3:
                heat = "極高"
            elif vol_ratio >= 1.5:
                heat = "高"
            else:
                heat = "一般"

            return {
                'stock_data': {
                    '股票代號': ticker.split('.')[0],
                    '股票名稱': name,
                    '產業面': industry,
                    '熱門程度': heat,
                    '現價': round(float(latest['Close']), 2),
                    '單日漲跌幅(%)': round(float(daily_return), 2),
                    '成交量(張)': int(volume_lots),
                    '資本額(億)': round(capital / 100_000_000, 2),
                    '每股盈餘(EPS)': round(float(eps), 2) if eps else None,
                    '本益比': round(float(pe), 2) if pe else None
                },
                'is_a': is_strategy_a,
                'is_b': is_strategy_b
            }
    except Exception:
        pass
    return None

def main():
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
                if res['is_a'] and res['is_b']:
                    results_intersection.append(s_data)
                elif res['is_a']:
                    results_original.append(s_data)
                elif res['is_b']:
                    results_ai.append(s_data)

    tz_tw = timezone(timedelta(hours=8))
    update_time_str = datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M:%S")

    output_data = {
        "update_time": update_time_str,
        "intersection": results_intersection,
        "original_strategy": results_original,
        "ai_strategy": results_ai
    }

    with open('daily_hot_stocks.json', 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=4)
        
    print(f"掃描完成。交集: {len(results_intersection)} 檔, 原始: {len(results_original)} 檔, AI量化: {len(results_ai)} 檔。")

if __name__ == "__main__":
    main()