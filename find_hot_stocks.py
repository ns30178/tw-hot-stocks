import yfinance as yf
import pandas as pd
import concurrent.futures
import warnings
import requests
import time
import random
import json
import os
import traceback
import cloudscraper
from datetime import datetime, timezone, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")

# ==========================================
# 系統設定區 (Telegram 推播)
# ==========================================
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
# ==========================================

# 建立突破防火牆專用的 Scraper
scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

# 一般的 Session
global_session = requests.Session()
retry = Retry(connect=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retry)
global_session.mount('http://', adapter)
global_session.mount('https://', adapter)

headers_fake = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'Connection': 'keep-alive',
}
global_session.headers.update(headers_fake)

def send_telegram_notify(msg):
    if not TG_BOT_TOKEN or TG_BOT_TOKEN == "":
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TG_CHAT_ID, "text": msg}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception:
        pass

def get_all_tw_tickers():
    tickers = {}
    try:
        res = scraper.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=15)
        if res.status_code == 200:
            for item in res.json():
                code = item.get("Code")
                if code and len(code) == 4 and code.isdigit():
                    tickers[f"{code}.TW"] = item.get("Name")
    except Exception: pass

    try:
        res = scraper.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=15)
        if res.status_code == 200:
            for item in res.json():
                code = item.get("SecuritiesCompanyCode")
                if code and len(code) == 4 and code.isdigit():
                    tickers[f"{code}.TWO"] = item.get("CompanyName")
    except Exception: pass

    if len(tickers) < 1000:
        print("⚠️ OpenAPI 獲取清單失敗，啟用 Github 備援清單...")
        try:
            res = scraper.get("https://raw.githubusercontent.com/shihzhan/twstock/master/twstock/codes.json", timeout=15)
            if res.status_code == 200:
                data = res.json()
                for code, info in data.items():
                    if info['type'] == '股票' and len(code) == 4:
                        suffix = ".TW" if info['market'] == '上市' else ".TWO"
                        tickers[f"{code}{suffix}"] = info['name']
        except Exception: pass

    return tickers

def get_institutional_data():
    inst_data = {}
    tw_time = datetime.now(timezone(timedelta(hours=8)))
    
    for i in range(5):
        check_time = tw_time - timedelta(days=i)
        if check_time.weekday() >= 5:
            continue
            
        twse_date = check_time.strftime("%Y%m%d")
        tpex_date = f"{check_time.year - 1911}/{check_time.strftime('%m/%d')}"
        data_found = False
        
        try:
            res_open = scraper.get("https://openapi.twse.com.tw/v1/fund/T86_ALL", timeout=10)
            if res_open.status_code == 200 and len(res_open.json()) > 100:
                for item in res_open.json():
                    code = item.get("Code")
                    fi = item.get("Foreign_Investor_Diff", 0)
                    it = item.get("Investment_Trust_Diff", 0)
                    try:
                        inst_data[code] = {
                            "FI": int(str(fi).replace(',', '')) // 1000,
                            "IT": int(str(it).replace(',', '')) // 1000
                        }
                    except ValueError: pass
                data_found = True
            else:
                url_twse = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={twse_date}&selectType=ALL&response=json"
                res_twse = scraper.get(url_twse, timeout=10)
                if res_twse.status_code == 200:
                    data = res_twse.json().get('data', [])
                    if data:
                        for row in data:
                            code = row[0]
                            try:
                                fi = int(str(row[4]).replace(',', '').strip()) // 1000
                                it = int(str(row[10]).replace(',', '').strip()) // 1000
                                inst_data[code] = {"FI": fi, "IT": it}
                            except ValueError: pass
                        data_found = True
        except Exception: pass

        try:
            url_tpex = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&d={tpex_date}"
            res_tpex = scraper.get(url_tpex, timeout=10)
            if res_tpex.status_code == 200:
                json_data = res_tpex.json()
                data = json_data.get('aaData', json_data.get('data', []))
                if data:
                    for row in data:
                        if len(row) >= 12:
                            code = row[0]
                            try:
                                fi = int(str(row[8]).replace(',', '').strip()) // 1000
                                it = int(str(row[11]).replace(',', '').strip()) // 1000
                                inst_data[code] = {"FI": fi, "IT": it}
                            except ValueError: pass
                    data_found = True
        except Exception: pass
            
        if data_found and len(inst_data) > 0:
            break
            
    return inst_data

def analyze_news_sentiment(code):
    pos_words = ['營收', '創高', '雙增', '大單', '受惠', '看好', '成長', '突破', '轉機', '拉貨', '優於預期', '爆發', '買超', '漲停', '利多', '上修']
    neg_words = ['衰退', '減', '降', '不如預期', '保守', '下修', '看壞', '出脫', '跌停', '虧損', '法說會失靈', '利空', '賣超', '走弱']
    url = f"https://tw.stock.yahoo.com/quote/{code}/news"
    try:
        res = scraper.get(url, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            titles = soup.find_all('h3')
            pos_score, neg_score = 0, 0
            for t in titles[:10]:
                title_text = t.text
                for word in pos_words:
                    if word in title_text: pos_score += 1
                for word in neg_words:
                    if word in title_text: neg_score += 1
            if pos_score == 0 and neg_score == 0:
                return "—", url
            elif pos_score > neg_score:
                return f"利多 ({pos_score})", url
            else:
                return f"利空 ({neg_score})", url
    except Exception:
        return "—", url
    return "—", url

def get_concept_and_heat(code, data_df):
    concept = "—"
    try:
        url = f"https://tw.stock.yahoo.com/quote/{code}"
        res = scraper.get(url, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            tags = soup.find_all('a', href=True)
            concepts = [t.text.strip() for t in tags if '/concept/' in t['href']]
            if concepts:
                unique_tags = list(dict.fromkeys(concepts))
                concept = "、".join(unique_tags[:3])
    except Exception:
        pass

    heat = "—"
    try:
       # 確保 data_df 為 DataFrame 且 Volume 欄位存在 
        if data_df is not None and 'Volume' in data_df.columns and len(data_df) >= 5:
            latest_vol = float(data_df['Volume'].iloc[-1])
            vma5 = float(data_df['Volume'].rolling(window=5).mean().iloc[-1])
            if vma5 > 0:
                heat_val = (latest_vol / vma5) * 100
                heat = f"{heat_val:.1f}%"
    except Exception:
        pass

    return concept, heat

def check_stock(ticker, name, inst_data):
    try:
        time.sleep(random.uniform(1.0, 2.5)) 
        
        data = yf.download(ticker, period="1y", auto_adjust=True, progress=False, session=global_session)
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
            exchange = "TWSE" if ticker.endswith(".TW") else "TPEX"
            concept, heat = get_concept_and_heat(code_only, data)
            
            return {
                'stock_data': {
                    '交易所': exchange,
                    '股票代號': code_only,
                    '股票名稱': name,
                    '概念類股': concept,
                    '個股熱度': heat,
                    '現價': round(float(latest['Close']), 2),
                    '單日漲跌幅(%)': round(float(daily_return), 2),
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

def calculate_performance(csv_file):
    if not os.path.exists(csv_file) or os.path.getsize(csv_file) == 0:
        return {}
    try:
        df = pd.read_csv(csv_file)
        if df.empty or '股票代號' not in df.columns: return {}
        df['日期'] = pd.to_datetime(df['日期'])
        today = pd.to_datetime(datetime.now(timezone(timedelta(hours=8))).date())
        df['Days'] = (today - df['日期']).dt.days
        df_target = df[df['Days'] >= 5].copy()
        if df_target.empty: return {}
        
        ticker_map = {}
        for _, row in df_target.drop_duplicates('股票代號').iterrows():
            suffix = ".TW" if row.get('交易所') == "TWSE" else ".TWO"
            ticker_map[str(row['股票代號'])] = f"{row['股票代號']}{suffix}"
            
        tickers_to_dl = list(ticker_map.values())
        curr_data = yf.download(tickers_to_dl, period="5d", auto_adjust=True, progress=False)
        
        latest_prices = {}
        if len(tickers_to_dl) == 1:
            latest_prices[tickers_to_dl[0]] = float(curr_data['Close'].iloc[-1])
        else:
            for t in tickers_to_dl:
                try:
                    latest_prices[t] = float(curr_data['Close'][t].dropna().iloc[-1])
                except:
                    pass
                    
        def get_ret(row):
            t_full = ticker_map.get(str(row['股票代號']))
            lp = latest_prices.get(t_full)
            if lp and row.get('現價', 0) > 0:
                return (lp - row['現價']) / row['現價'] * 100
            return None
            
        df_target['Return'] = df_target.apply(get_ret, axis=1)
        df_target = df_target.dropna(subset=['Return'])
        
        def get_period(d):
            if 5 <= d <= 9: return "5日"
            elif 10 <= d <= 19: return "10日"
            elif d >= 20: return "20日"
            return None
            
        df_target['Period'] = df_target['Days'].apply(get_period)
        df_target = df_target.dropna(subset=['Period'])
        
        stats = {}
        for strategy in ['飆股策略', 'AI選股策略']:
            stats[strategy] = {}
            strat_df = df_target[df_target['策略'] == strategy]
            for period in ["5日", "10日", "20日"]:
                p_df = strat_df[strat_df['Period'] == period]
                if not p_df.empty:
                    win_rate = (p_df['Return'] > 0).mean() * 100
                    avg_ret = p_df['Return'].mean()
                    best_row = p_df.loc[p_df['Return'].idxmax()]
                    best_str = f"{best_row['股票代號']}(+{best_row['Return']:.1f}%)"
                    stats[strategy][period] = {
                        "count": len(p_df),
                        "win_rate": round(win_rate, 1),
                        "avg_return": round(avg_ret, 2),
                        "best_stock": best_str
                    }
                else:
                    stats[strategy][period] = {"count": 0, "win_rate": 0, "avg_return": 0, "best_stock": "—"}
        return stats
    except Exception:
        return {}

def main():
    try:
        print("🚀 系統啟動：台股雙策略觀測站自動化掃描")
        tz_tw = timezone(timedelta(hours=8))
        now = datetime.now(tz_tw)
        update_date_str = now.strftime("%Y-%m-%d")

        previous_data = {"update_date": "無", "original_strategy": [], "ai_strategy": [], "intersection": []}
        if os.path.exists('daily_hot_stocks.json') and os.path.getsize('daily_hot_stocks.json') > 0:
            try:
                with open('daily_hot_stocks.json', 'r', encoding='utf-8') as f:
                    old_json = json.load(f)
                    previous_data = old_json.get("latest_data", previous_data)
            except Exception: pass

        is_same_day = previous_data.get("update_date") == update_date_str

        print("📡 正在向證交所請求全台股上市櫃清單 (套用 Cloudscraper 破防模組)...")
        tickers_dict = get_all_tw_tickers()
        if not tickers_dict: 
            print("⚠️ 嚴重警告：所有清單管道皆失效，僅使用備用名單測試！")
            tickers_dict = {'2330.TW': '台積電', '2317.TW': '鴻海', '2454.TW': '聯發科'}
        else:
            print(f"✅ 成功取得 {len(tickers_dict)} 檔上市櫃股票，準備執行運算。")

        print("📥 正在向證交所與櫃買中心抓取三大法人籌碼資料...")
        inst_data = get_institutional_data()
        print(f"✅ 成功抓取 {len(inst_data)} 檔法人買賣超資料！")
            
        results_intersection, results_original, results_ai = [], [], []

        print("⚙️ 執行多執行緒技術面與基本面掃描 (已啟動降速防封鎖機制，請耐心等候)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(check_stock, t, n, inst_data): t for t, n in tickers_dict.items()}
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res:
                    s_data = res['stock_data']
                    if res['is_a']: results_original.append(s_data)
                    if res['is_b']: results_ai.append(s_data)
                    if res['is_a'] and res['is_b']: results_intersection.append(s_data)
        
        print("✅ 掃描完畢，正在進行資料彙整與儲存...")

        def get_streak(code, list_name):
            for stock in previous_data.get(list_name, []):
                if stock.get('股票代號') == code:
                    base_streak = stock.get('進榜天數', 1)
                    return base_streak if is_same_day else base_streak + 1
            return 1

        for lst, name in [(results_original, 'original_strategy'), (results_ai, 'ai_strategy'), (results_intersection, 'intersection')]:
            for s in lst:
                s['進榜天數'] = get_streak(s['股票代號'], name)
                sentiment, news_url = analyze_news_sentiment(s['股票代號'])
                s['情緒分析'] = sentiment
                s['新聞連結'] = news_url
        
        csv_file = 'history_records.csv'
        all_results = []
        for s in results_original: s_copy = s.copy(); s_copy['策略'] = '飆股策略'; all_results.append(s_copy)
        for s in results_ai: s_copy = s.copy(); s_copy['策略'] = 'AI選股策略'; all_results.append(s_copy)
        for s in results_intersection: s_copy = s.copy(); s_copy['策略'] = '核心交集'; all_results.append(s_copy)
        if all_results:
            df_new = pd.DataFrame(all_results)
            df_new.insert(0, '日期', update_date_str)
            file_exists = os.path.isfile(csv_file) and os.path.getsize(csv_file) > 0
            df_new.to_csv(csv_file, mode='a', index=False, encoding='utf-8-sig', header=not file_exists)

        perf_stats = calculate_performance(csv_file)

        output_data = {
            "update_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "latest_data": {
                "update_date": update_date_str,
                "original_strategy": results_original,
                "ai_strategy": results_ai,
                "intersection": results_intersection
            },
            "previous_data": previous_data,
            "performance_stats": perf_stats
        }

        with open('daily_hot_stocks.json', 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=4)
            
        notify_msg = (
            f"\n📊 台股雙策略觀測站 更新完成\n"
            f"📅 日期：{update_date_str}\n\n"
            f"🔥 核心交集：{len(results_intersection)} 檔\n"
            f"📈 飆股策略：{len(results_original)} 檔\n"
            f"🤖 AI 選股：{len(results_ai)} 檔\n\n"
            f"請至 GitHub Pages 網頁查看最新清單與績效面板。"
        )
        send_telegram_notify(notify_msg)
        print("🎉 執行成功，所有資料已推播並寫入完畢！")
        
    except Exception as e:
        error_msg = f"\n⚠️ 台股觀測站執行失敗\n錯誤訊息: {str(e)}\n\n詳細 Log:\n{traceback.format_exc()[:500]}"
        print(error_msg)
        send_telegram_notify(error_msg)

if __name__ == "__main__":
    main()
