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
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")

global_session = requests.Session()
retry = Retry(connect=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retry)
global_session.mount('http://', adapter)
global_session.mount('https://', adapter)
global_session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})

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
    except Exception:
        pass
    return tickers

def get_institutional_data():
    inst_data = {}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/fund/T86_ALL", timeout=10)
        if res.status_code == 200:
            for item in res.json():
                code = item.get("Code")
                fi = item.get("Foreign_Investor_Diff", 0)
                it = item.get("Investment_Trust_Diff", 0)
                try:
                    inst_data[code] = {
                        "FI": int(str(fi).replace(',', '')) // 1000,
                        "IT": int(str(it).replace(',', '')) // 1000
                    }
                except ValueError:
                    pass
    except Exception:
        pass
    return inst_data

def analyze_news_sentiment(code):
    pos_words = ['營收', '創高', '雙增', '大單', '受惠', '看好', '成長', '突破', '轉機', '拉貨', '優於預期', '爆發', '買超', '漲停', '利多', '上修']
    neg_words = ['衰退', '減', '降', '不如預期', '保守', '下修', '看壞', '出脫', '跌停', '虧損', '法說會失靈', '利空', '賣超', '走弱']
    
    url = f"https://tw.stock.yahoo.com/quote/{code}/news"
    sentiment_label = "無消息"
    
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            titles = soup.find_all('h3')
            
            pos_score = 0
            neg_score = 0
            
            for t in titles[:10]:
                title_text = t.text
                for word in pos_words:
                    if word in title_text: pos_score += 1
                for word in neg_words:
                    if word in title_text: neg_score += 1
            
            if pos_score == 0 and neg_score == 0:
                sentiment_label = "中性"
            elif pos_score > neg_score:
                sentiment_label = f"利多 ({pos_score})"
            else:
                sentiment_label = f"利空 ({neg_score})"
    except Exception:
        sentiment_label = "讀取失敗"
        
    return sentiment_label, url

def check_stock(ticker, name, inst_data):
    try:
        time.sleep(random.uniform(0.1, 0.4)) 
        
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

        tech_a_pass = (latest['Close'] >= data['Close'].tail(200).max()) and (500 <= volume_lots <= 5000)

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

        code_only = ticker.split('.')[0]
        inst = inst_data.get(code_only, {"FI": 0, "IT": 0})
        
        if code_only in inst_data:
            if (inst["FI"] + inst["IT"]) < 0:
                return None

        stock = yf.Ticker(ticker)
        info = stock.info
        capital = (info.get('sharesOutstanding') or 0) * 10
        book_value = info.get('bookValue') or 0
        
        rev_growth = info.get('revenueGrowth')
        rev_growth_pct = round(rev_growth * 100, 2) if rev_growth is not None else None
        bias_20 = ((latest['Close'] - latest['MA20']) / latest['MA20']) * 100

        is_strategy_a = tech_a_pass and (capital < 1_000_000_000) and (book_value > 5)
        is_strategy_b = tech_b_pass and (capital < 5_000_000_000)

        if is_strategy_a or is_strategy_b:
            industry = info.get('industry', '未知')
            vol_ratio = latest['Volume'] / latest['VMA5'] if latest['VMA5'] > 0 else 0
            heat = "極高" if vol_ratio >= 3 else "高" if vol_ratio >= 1.5 else "一般"
            exchange = "TWSE" if ticker.endswith(".TW") else "TPEX"

            return {
                'stock_data': {
                    '交易所': exchange,
                    '股票代號': code_only,
                    '股票名稱': name,
                    '產業面': industry,
                    '熱門程度': heat,
                    '現價': round(float(latest['Close']), 2),
                    '單日漲跌幅(%)': round(float(daily_return), 2),
                    '成交量(張)': int(volume_lots),
                    '乖離率(%)': round(float(bias_20), 2),
                    '營收YoY(%)': rev_growth_pct,
                    '外資買賣(張)': inst["FI"],
                    '投信買賣(張)': inst["IT"],
                    '進榜天數': 1
                },
                'is_a': is_strategy_a,
                'is_b': is_strategy_b
            }
    except Exception:
        pass
    return None

def get_streak(code, list_name, prev_data):
    for stock in prev_data.get(list_name, []):
        if stock.get('股票代號') == code:
            return stock.get('進榜天數', 1) + 1
    return 1

def main():
    previous_data = {"update_date": "無", "original_strategy": [], "ai_strategy": [], "intersection": []}
    if os.path.exists('daily_hot_stocks.json') and os.path.getsize('daily_hot_stocks.json') > 0:
        try:
            with open('daily_hot_stocks.json', 'r', encoding='utf-8') as f:
                old_json = json.load(f)
                previous_data = old_json.get("latest_data", previous_data)
        except Exception:
            pass

    tickers_dict = get_all_tw_tickers()
    if not tickers_dict: tickers_dict = {'2330.TW': '台積電', '2317.TW': '鴻海', '2454.TW': '聯發科'}
    inst_data = get_institutional_data()
        
    results_intersection, results_original, results_ai = [], [], []

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(check_stock, t, n, inst_data): t for t, n in tickers_dict.items()}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                s_data = res['stock_data']
                if res['is_a']: results_original.append(s_data)
                if res['is_b']: results_ai.append(s_data)
                if res['is_a'] and res['is_b']: results_intersection.append(s_data)

    for lst, name in [(results_original, 'original_strategy'), (results_ai, 'ai_strategy'), (results_intersection, 'intersection')]:
        for s in lst:
            s['進榜天數'] = get_streak(s['股票代號'], name, previous_data)
            sentiment, news_url = analyze_news_sentiment(s['股票代號'])
            s['情緒分析'] = sentiment
            s['新聞連結'] = news_url

    tz_tw = timezone(timedelta(hours=8))
    now = datetime.now(tz_tw)
    
    output_data = {
        "update_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "latest_data": {
            "update_date": now.strftime("%Y-%m-%d"),
            "original_strategy": results_original,
            "ai_strategy": results_ai,
            "intersection": results_intersection
        },
        "previous_data": previous_data
    }

    with open('daily_hot_stocks.json', 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=4)
        
    all_results = []
    for s in results_original: s_copy = s.copy(); s_copy['策略'] = '飆股策略'; all_results.append(s_copy)
    for s in results_ai: s_copy = s.copy(); s_copy['策略'] = 'AI選股策略'; all_results.append(s_copy)
    for s in results_intersection: s_copy = s.copy(); s_copy['策略'] = '核心交集'; all_results.append(s_copy)

    if all_results:
        df_new = pd.DataFrame(all_results)
        df_new.insert(0, '日期', now.strftime("%Y-%m-%d"))
        csv_file = 'history_records.csv'
        file_exists = os.path.isfile(csv_file) and os.path.getsize(csv_file) > 0
        df_new.to_csv(csv_file, mode='a', index=False, encoding='utf-8-sig', header=not file_exists)

if __name__ == "__main__":
    main()