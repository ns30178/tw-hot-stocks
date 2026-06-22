import yfinance as yf
import pandas as pd
import concurrent.futures
import warnings
import requests
import time
import random

# 忽略警告訊息
warnings.filterwarnings("ignore")

def get_all_tw_tickers():
    """
    從台灣證交所 ISIN 網頁抓取所有上市與上櫃股票代號
    """
    tickers = []
    try:
        # strMode=2 為上市, strMode=4 為上櫃
        modes = {'2': '.TW', '4': '.TWO'}
        for mode, suffix in modes.items():
            url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
            res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
            df = pd.read_html(res.text)[0]
            
            # 整理資料，過濾出股票 (排除權證、牛熊證等)
            df.columns = df.iloc[0]
            df = df.iloc[1:]
            df = df.dropna(thresh=3, axis=0).dropna(how='all', axis=1)
            
            # 格式為 "代號　名稱"，透過空白分割
            stock_data = df['有價證券代號及名稱'].str.split('　', expand=True)
            if len(stock_data.columns) >= 2:
                # 篩選長度為4碼的標準股票代號
                valid_tickers = stock_data[stock_data[0].str.len() == 4][0].tolist()
                tickers.extend([f"{t}{suffix}" for t in valid_tickers])
    except Exception as e:
        print(f"抓取股票代號失敗: {e}")
    
    return list(set(tickers))

def check_stock(ticker):
    """
    獲取單一檔股票資料並檢驗是否符合飆股條件
    """
    try:
        # 隨機延遲 0.5 ~ 2 秒，避免 yfinance API 阻擋
        time.sleep(random.uniform(0.5, 2.0))
        
        stock = yf.Ticker(ticker)
        data = yf.download(ticker, period="1y", progress=False)
        
        # 資料筆數不足以計算200日則跳過
        if len(data) < 200:
            return None

        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.droplevel(1)

        data['MA20'] = data['Close'].rolling(window=20).mean()
        data['MA60'] = data['Close'].rolling(window=60).mean()
        data['VMA5'] = data['Volume'].rolling(window=5).mean()
        data['Daily_Return'] = data['Close'].pct_change()

        latest = data.iloc[-1]

        if pd.isna(latest['MA20']) or pd.isna(latest['MA60']) or pd.isna(latest['VMA5']):
            return None

        # 技術面條件
        cond_price_above_ma = (latest['Close'] > latest['MA20']) and (latest['Close'] > latest['MA60'])
        cond_ma_trend = latest['MA20'] > latest['MA60']
        cond_volume_breakout = latest['Volume'] > (latest['VMA5'] * 2)
        cond_price_momentum = latest['Daily_Return'] > 0.05
        cond_200_high = latest['Close'] >= data['Close'].tail(200).max()
        
        volume_lots = latest['Volume'] / 1000
        cond_volume_range = (50 < volume_lots < 5000)

        if cond_price_above_ma and cond_ma_trend and cond_volume_breakout and cond_price_momentum and cond_200_high and cond_volume_range:
            info = stock.info
            
            # 基本面條件
            shares = info.get('sharesOutstanding')
            if shares is None:
                return None
            capital = shares * 10
            cond_capital = capital < 1_000_000_000

            book_value = info.get('bookValue')
            if book_value is None:
                return None
            cond_book_value = book_value > 5

            if cond_capital and cond_book_value:
                return {
                    '股票代號': ticker.replace('.TW', '').replace('.TWO', ''),
                    '收盤價': round(float(latest['Close']), 2),
                    '單日漲跌幅(%)': round(float(latest['Daily_Return']) * 100, 2),
                    '成交量(張)': int(volume_lots),
                    '資本額(億)': round(capital / 100_000_000, 2),
                    '最新淨值': round(float(book_value), 2)
                }
    except Exception:
        pass
    
    return None

def main():
    print("正在獲取上市櫃股票代號...")
    tickers_list = get_all_tw_tickers()
    
    if not tickers_list:
        print("無法獲取股票代號，改用備用清單執行。")
        tickers_list = ['2330.TW', '2317.TW', '2454.TW']

    print(f"開始掃描 {len(tickers_list)} 檔股票... (需時較長，請耐心等候)")
    results = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(check_stock, ticker): ticker for ticker in tickers_list}
        
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res is not None:
                results.append(res)
                print(f"發現符合條件標的: {res['股票代號']}")

    if results:
        df_results = pd.DataFrame(results)
        df_results = df_results.sort_values(by='單日漲跌幅(%)', ascending=False).reset_index(drop=True)
        df_results.to_json('daily_hot_stocks.json', orient='records', force_ascii=False)
        print("\n資料已匯出至 daily_hot_stocks.json")
    else:
        print("\n目前無符合條件的股票。")
        with open('daily_hot_stocks.json', 'w', encoding='utf-8') as f:
            f.write('[]')

if __name__ == "__main__":
    main()