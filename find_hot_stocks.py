def get_concept_and_heat(code, data_df):
    concept = "—"
    try:
        # 增加 headers 的強壯度
        url = f"https://tw.stock.yahoo.com/quote/{code}"
        res = scraper.get(url, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            # 修正選取概念股的方式：改抓所有包含特定類別的 tag
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
