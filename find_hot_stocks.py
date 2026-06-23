import yfinance as yf
import pandas as pd
import concurrent.futures
import warnings
import requests
import time
import random
import json
from datetime import datetime, timezone, timedelta

warnings.filterwarnings("ignore")

def get_all_tw_tickers():
    tickers = {}
    try:
        modes = {'2': '.TW', '4': '.TWO'}
        for mode, suffix in modes.items():
            url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
            res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
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
        time.sleep(random.uniform(0.5, 1.5))
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0'})
        
        data = yf.download(ticker, period="1y", progress=False, session=session)
        if len(data) < 200: return None
        if isinstance(data.columns, pd.MultiIndex): data.columns = data.columns.droplevel(1)

        data['MA50'] = data['Close'].rolling(window=50).mean()
        latest = data.iloc[-1]
        prev = data.iloc[-2] if len(data) > 1 else latest
        
        daily_return = ((latest['Close'] - prev['Close']) / prev['Close']) * 100
        volume_lots = latest['Volume'] / 1000

        # 策略條件：200日新高、成交量 50~5000張、距離50日均線 20%~30% (絕對值)
        cond_200_high = latest['Close'] >= data['Close'].tail(200).max()
        cond_volume = 50 < volume_lots < 5000
        
        distance_ma50 = abs(latest['Close'] - latest['MA50']) / latest['MA50']
        cond_ma50 = 0.20 <= distance_ma50 <= 0.30
        
        if cond_200_high and cond_volume and cond_ma50:
            stock = yf.Ticker(ticker)
            info = stock.info
            capital = info.get('sharesOutstanding', 0) * 10
            book_value = info.get('bookValue', 0)
            
            # 策略條件：資本額 < 10億 與 最新淨值 > 5
            if capital < 1_000_000_000 and book_value > 5:
                eps = info.get('trailingEps')
                pe = info.get('trailingPE')
                
                return {
                    '股票代號': ticker.split('.')[0],
                    '股票名稱': name,
                    '現價': round(float(latest['Close']), 2),
                    '單日漲跌幅(%)': round(float(daily_return), 2),
                    '成交量(張)': int(volume_lots),
                    '資本額(億)': round(capital / 100_000_000, 2),
                    '每股盈餘(EPS)': round(float(eps), 2) if eps else None,
                    '本益比': round(float(pe), 2) if pe else None
                }
    except Exception:
        pass
    return None

def main():
    tickers_dict = get_all_tw_tickers()
    
    if not tickers_dict: 
        print("【警告】無法獲取全市場代號，啟用備用清單 (共 3 檔)。")
        tickers_dict = {'2330.TW': '台積電', '2317.TW': '鴻海', '2454.TW': '聯發科'}
    else:
        print(f"【執行確認】成功獲取市場代號，開始掃描共 {len(tickers_dict)} 檔標的...")
        
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(check_stock, t, n): t for t, n in tickers_dict.items()}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: 
                results.append(res)
                print(f"發現標的: {res['股票代號']} {res['股票名稱']}")

    # 取得台灣時間
    tz_tw = timezone(timedelta(hours=8))
    update_time_str = datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M:%S")

    output_data = {
        "update_time": update_time_str,
        "data": results
    }

    with open('daily_hot_stocks.json', 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=4)
        
    if results:
        print(f"掃描完成，共 {len(results)} 檔符合條件，已輸出至 JSON。")
    else:
        print("掃描完成，今日無符合條件標的。")

if __name__ == "__main__":
    main()