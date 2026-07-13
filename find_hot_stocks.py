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
import io
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

scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

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
            if pos_score == 0 and neg_score == 0: return "—", url
            elif pos_score > neg_score: return f"利多 ({pos_score})", url
            else: return f"利空 ({neg_score})", url
    except Exception: return "—", url
    return "—", url

def get_industry_and_heat(code, data_df):
    industry = "無產業分類"
    try:
        url = f"https://tw.stock.yahoo.com/quote/{code}"
        res = scraper.get(url, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            tags = soup.select('a[href*="/class-quote?sectorId="]')
            if tags: industry = tags[0].text.strip()
    except Exception: pass

    heat = "0.0%"
    try:
        if data_df is not None and not data_df.empty and 'Volume' in data_df.columns and len(data_df) >= 5:
            latest_vol = float(data_df['Volume'].iloc[-1])
            vma5 = float(data_df['Volume'].rolling(window=5).mean().iloc[-1])
            if vma5 > 0:
                heat_val = (latest_vol / vma5) * 100
                heat = f"{heat_val:.1f}%"
    except Exception: pass
    return industry, heat

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
        else: cond_b_kline = False

        tech_b_pass = cond_b_price and cond_b_liquidity and cond_b_ma_convergence and cond_b_vol_amp and cond_b_atr_expand and cond_b_kline

        if not (tech_a_pass or tech_b_pass): return None

        code_only = ticker.split('.')[0]
        inst = inst_data.get(code_only, {"FI": 0, "IT": 0})
        if code_only in inst_data and (inst["FI"] + inst["IT"]) < 0: return None

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
            industry, heat = get_industry_and_heat(code_only, data)
            return {
                'stock_data': {
                    '交易所': exchange, '股票代號': code_only, '股票名稱': name,
                    '產業分類': industry, '個股熱度': heat, '現價': round(float(latest['Close']), 2),
                    '單日漲跌幅(%)': round(float(daily_return), 2), '乖離率(%)': round(float(bias_20), 2),
                    '營收YoY(%)': rev_growth_pct, '外資買賣(張)': inst["FI"], '投信買賣(張)': inst["IT"], '進榜天數': 1
                },
                'is_a': is_strategy_a, 'is_b': is_strategy_b
            }
    except Exception: pass
    return None

def calculate_performance(csv_file):
    if not os.path.exists(csv_file) or os.path.getsize(csv_file) == 0: return {}
    try:
        # 【裝甲防護】指定編碼，強制剔除所有表頭可能殘留的不可見字元
        df = pd.read_csv(csv_file, encoding='utf-8-sig')
        df.rename(columns=lambda x: x.strip('\ufeff').strip('ï»¿').strip(), inplace=True)
        
        if df.empty or '股票代號' not in df.columns: return {}
        
        df['日期'] = pd.to_datetime(df['日期'], errors='coerce')
        df = df.dropna(subset=['日期'])
        df['現價'] = pd.to_numeric(df['現價'], errors='coerce')
        
        today = pd.to_datetime(datetime.now(timezone(timedelta(hours=8))).date())
        df['Days'] = (today - df['日期']).dt.days
        df_target = df[df['Days'] >= 5].copy()
        if df_target.empty: return {}
        
        ticker_map = {}
        for _, row in df_target.drop_duplicates('股票代號').iterrows():
            code_str = str(row['股票代號']).replace('.0', '')
            suffix = ".TW" if row.get('交易所') == "TWSE" else ".TWO"
            ticker_map[code_str] = f"{code_str}{suffix}"
            
        tickers_to_dl = list(ticker_map.values())
        
        # 【修改點 3】抓取 1 個月歷史區間，以利計算觸價停損
        curr_data = yf.download(tickers_to_dl, period="1mo", auto_adjust=True, progress=False)
        is_single = len(tickers_to_dl) == 1
        
        def get_ret(row):
            code_str = str(row['股票代號']).replace('.0', '')
            t_full = ticker_map.get(code_str)
            entry_price = row.get('現價')
            entry_date = row['日期']
            
            if pd.isna(entry_price) or entry_price <= 0 or curr_data.empty:
                return None
                
            try:
                if is_single:
                    t_close = curr_data['Close']
                    t_low = curr_data['Low']
                else:
                    t_close = curr_data['Close'][t_full]
                    t_low = curr_data['Low'][t_full]
                    
                # 篩選進榜日之後的資料
                mask = t_close.index >= pd.to_datetime(entry_date)
                t_close_after = t_close[mask].dropna()
                t_low_after = t_low[mask].dropna()
                
                if not t_close_after.empty:
                    lowest_price = t_low_after.min()
                    latest_close = t_close_after.iloc[-1]
                    
                    # 【修改點 3】-15% 停損機制判斷
                    if lowest_price <= entry_price * 0.85:
                        return -15.0
                    else:
                        return (latest_close - entry_price) / entry_price * 100
            except: pass
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
                    
                    # 【修改點 4】彙整 JSON 明細陣列，供前端 Modal 顯示
                    details = [{"code": str(r['股票代號']).replace('.0', ''), "return": r['Return']} for _, r in p_df.iterrows()]
                    details.sort(key=lambda x: x['return'], reverse=True)
                    
                    top_3 = details[:3]
                    bottom_3 = sorted(details, key=lambda x: x['return'])[:3]
                    
                    stats[strategy][period] = {
                        "count": len(p_df), 
                        "win_rate": round(win_rate, 1), 
                        "avg_return": round(avg_ret, 2), 
                        "details": details,
                        "top_3": top_3,
                        "bottom_3": bottom_3
                    }
                else:
                    stats[strategy][period] = {"count": 0, "win_rate": 0, "avg_return": 0, "details": [], "top_3": [], "bottom_3": []}
        return stats
    except Exception as e:
        print(f"⚠️ 績效運算錯誤: {e}")
        return {}

def main():
    try:
        print("🚀 系統啟動：台股雙策略觀測站自動化掃描")
        tz_tw = timezone(timedelta(hours=8))
        now = datetime.now(tz_tw)
        update_date_str = now.strftime("%Y-%m-%d")
        
        # ==========================================
        # 【全時段閃電退場防線】平日非盤後時段、週末假日，一秒攔截以節省時數
        # ==========================================
        is_weekend = now.weekday() >= 5
        is_wrong_time = now.hour < 17
        is_manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        
        if (is_weekend or is_wrong_time) and not is_manual:
            print(f"💤 偵測到當前時間為台灣 {now.strftime('%Y-%m-%d %H:%M:%S')} (非盤後更新時段)")
            print("🔒 啟動全時段閃電安全退場機制，一秒終止程式，成功守護 GitHub 運算時數！")
            return
            
        # ==========================================
        # 【修改點 2】異常休市與颱風假阻斷機制 - 修正變數名稱避免撞名
        # ==========================================
        try:
            market_check_df = yf.download("0050.TW", period="5d", progress=False)
            if not market_check_df.empty:
                last_trade_date = market_check_df.index[-1].strftime("%Y-%m-%d")
                if last_trade_date != update_date_str and not is_manual:
                    print(f"⚠️ 偵測到今日 ({update_date_str}) 台股未開盤 (最後交易日為 {last_trade_date})。")
                    print("🔒 判斷為異常休市或颱風假，直接凍結系統，避免舊資料錯位覆蓋！")
                    return
        except Exception as e:
            print(f"大盤驗證發生異常，略過休市檢查: {e}")
        # ==========================================

        previous_data = {"update_date": "無", "original_strategy": [], "ai_strategy": [], "intersection": []}
        old_json = {}
        if os.path.exists('daily_hot_stocks.json') and os.path.getsize('daily_hot_stocks.json') > 0:
            try:
                with open('daily_hot_stocks.json', 'r', encoding='utf-8') as f: old_json = json.load(f)
            except Exception: pass

        latest_in_file = old_json.get("latest_data", {})
        is_same_day = latest_in_file.get("update_date") == update_date_str
        if is_same_day: previous_data = old_json.get("previous_data", previous_data)
        else:
            if latest_in_file.get("update_date"): previous_data = latest_in_file

        print("📡 正在向證交所請求全台股上市櫃清單...")
        tickers_dict = get_all_tw_tickers()
        if not tickers_dict: tickers_dict = {'2330.TW': '台積電', '2317.TW': '鴻海', '2454.TW': '聯發科'}
        
        print("📥 正在抓取三大法人籌碼資料...")
        inst_data = get_institutional_data()
            
        results_intersection, results_original, results_ai = [], [], []
        print("⚙️ 執行多執行緒篩選策略...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(check_stock, t, n, inst_data): t for t, n in tickers_dict.items()}
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res:
                    s_data = res['stock_data']
                    if res['is_a']: results_original.append(s_data)
                    if res['is_b']: results_ai.append(s_data)
                    if res['is_a'] and res['is_b']: results_intersection.append(s_data)
        
        print("✅ 篩選完畢，計算進榜連續天數與新聞情緒...")
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
        
        # ==========================================
        # 【UX 凍結機制】若盤後掃描無股票進榜，凍結網頁與 CSV
        # ==========================================
        if not all_results:
            print("⚠️ 本次掃描無標的進榜，啟動版面保護機制，放棄覆蓋資料。")
            notify_msg = (
                f"\n📊 台股雙策略觀測站 (防呆保護)\n"
                f"📅 日期：{update_date_str}\n\n"
                f"⚠️ 目前無標的進榜 (可能尚未收盤或無動能股)。\n"
                f"🔒 網頁資料與歷史紀錄已凍結，維持前次版面不動。"
            )
            send_telegram_notify(notify_msg)
            return
        # ==========================================

        if all_results:
            df_new = pd.DataFrame(all_results)
            df_new.insert(0, '日期', update_date_str)
            if os.path.exists(csv_file) and os.path.getsize(csv_file) > 0:
                try:
                    with open(csv_file, 'r', encoding='utf-8-sig') as f: lines = f.readlines()
                    if lines:
                        lines[0] = lines[0].replace('概念類股', '產業分類')
                        header = lines[0].strip("\n\r").split(',')
                        if '產業分類' not in header: lines[0] = lines[0].strip("\n\r") + ",產業分類,個股熱度\n"

                        expected_cols = len(lines[0].split(','))
                        for i in range(1, len(lines)):
                            cols = lines[i].strip("\n\r").split(',')
                            if len(cols) < expected_cols: lines[i] = lines[i].strip("\n\r") + ",—,—\n"
                        df_existing = pd.read_csv(io.StringIO("".join(lines)))
                        if '日期' in df_existing.columns: df_existing = df_existing[df_existing['日期'] != update_date_str]
                        df_final = pd.concat([df_existing, df_new], ignore_index=True)
                    else: df_final = df_new
                except Exception as e:
                    print(f"⚠️ CSV 自動修復讀取失敗: {e}")
                    df_final = df_new
            else: df_final = df_new
            
            # ==========================================
            # 【修改點 3】CSV 欄位錯位修復
            # ==========================================
            expected_csv_cols = ['日期', '交易所', '股票代號', '股票名稱', '產業分類', '個股熱度', '現價', '單日漲跌幅(%)', '乖離率(%)', '營收YoY(%)', '外資買賣(張)', '投信買賣(張)', '進榜天數', '情緒分析', '新聞連結', '策略']
            df_final = df_final.reindex(columns=expected_csv_cols)
            df_final.to_csv(csv_file, index=False, encoding='utf-8-sig')

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
            f"請至 GitHub Pages 網頁查看最新清單。"
        )
        send_telegram_notify(notify_msg)
        print("🎉 執行成功，所有資料已推播並寫入完畢！")
        
    except Exception as e:
        error_msg = f"\n⚠️ 台股觀測站執行失敗\n錯誤訊息: {str(e)}\n\n詳細 Log:\n{traceback.format_exc()[:500]}"
        print(error_msg)
        send_telegram_notify(error_msg)

if __name__ == "__main__":
    main()
