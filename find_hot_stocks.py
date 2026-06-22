import yfinance as yf
import pandas as pd
import concurrent.futures
import warnings
import requests
import time
import random

warnings.filterwarnings("ignore")

def get_all_tw_tickers():
    """
    獲取上市櫃股票代號，並嚴格過濾掉 5 碼異常代號
    """
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
                    # 強制只取 4 碼數字代號
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
        
        # 加入 session 機制修復 401 錯誤
        data = yf.download(ticker, period="1y", progress=False, session=session)
        if len(data) < 200: return None
        if isinstance(data.columns, pd.MultiIndex): data.columns = data.columns.droplevel(1)

        data['MA20'] = data['Close'].rolling(window=20).mean()
        data['MA60'] = data['Close'].rolling(window=60).mean()
        data['VMA5'] = data['Volume'].rolling(window=5).mean()
        data['Daily_Return'] = data['Close'].pct_change()
        latest = data.iloc[-1]

        # 原始嚴格篩選條件
        cond_price_above_ma = (latest['Close'] > latest['MA20']) and (latest['Close'] > latest['MA60']) and (latest['MA20'] > latest['MA60'])
        cond_200_high = latest['Close'] >= data['Close'].tail(200).max()
        cond_price_momentum = latest['Daily_Return'] > 0.05
        cond_volume_breakout = latest['Volume'] > (latest['VMA5'] * 2)
        volume_lots = latest['Volume'] / 1000
        cond_volume_range = (50 <= volume_lots <= 5000)

        if cond_price_above_ma and cond_200_high and cond_price_momentum and cond_volume_breakout and cond_volume_range:
            stock = yf.Ticker(ticker)
            info = stock.info
            capital = info.get('sharesOutstanding', 0) * 10
            book_value = info.get('bookValue', 0)
            
            if capital < 1_000_000_000 and book_value > 5:
                return {
                    '股票代號': ticker.split('.')[0],
                    '股票名稱': name,
                    '現價': round(float(latest['Close']), 2),
                    '單日漲跌幅(%)': round(float(latest['Daily_Return']) * 100, 2),
                    '成交量(張)': int(volume_lots),
                    '資本額(億)': round(capital / 100_000_000, 2),
                    '最新淨值': round(float(book_value), 2)
                }
    except Exception:
        pass
    return None

def main():
    tickers_dict = get_all_tw_tickers()
    if not tickers_dict: tickers_dict = {'2330.TW': '台積電', '2317.TW': '鴻海', '2454.TW': '聯發科'}
    
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(check_stock, t, n): t for t, n in tickers_dict.items()}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
                print(f"發現標的: {res['股票代號']} {res['股票名稱']}")

    if results:
        df_results = pd.DataFrame(results).sort_values(by='單日漲跌幅(%)', ascending=False)
        df_results.to_json('daily_hot_stocks.json', orient='records', force_ascii=False)
    else:
        with open('daily_hot_stocks.json', 'w', encoding='utf-8') as f: f.write('[]')

if __name__ == "__main__":
    main()
