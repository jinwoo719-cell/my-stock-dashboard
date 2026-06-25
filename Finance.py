import streamlit as st
import pandas as pd
import altair as alt
import requests
import FinanceDataReader as fdr
import datetime
from pykrx import stock as pykrx_stock
import yfinance as yf

st.set_page_config(page_title="주식 가격 분석 대시보드", page_icon="📈", layout="wide")

def fmt_commas(d):
    out = d.copy()
    for col in out.columns:
        out[col] = out[col].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "-")
    return out

def compute_rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def fg_rating_label(score):
    if score <= 25: return "극단적 공포"
    elif score <= 45: return "공포"
    elif score <= 55: return "중립"
    elif score <= 75: return "탐욕"
    else: return "극단적 탐욕"

def build_price_chart(df, ma_cols, fair_price=None):
    try:
        plot_df = df[["Close"] + ma_cols + ["일일수익률"]].copy()
        plot_df["전일대비"] = plot_df["일일수익률"] * 100
        plot_df = plot_df.reset_index()
        plot_df.columns = ["날짜"] + list(plot_df.columns[1:])

        value_cols = ["Close"] + ma_cols
        if fair_price is not None:
            plot_df["적정주가"] = fair_price
            value_cols = value_cols + ["적정주가"]

        all_values = plot_df[value_cols].values.astype(float).flatten()
        all_values = all_values[~pd.isna(all_values)]
        if len(all_values) == 0:
            st.line_chart(plot_df.set_index("날짜")[value_cols])
            return
        y_min, y_max = float(all_values.min()), float(all_values.max())
        pad = (y_max - y_min) * 0.08 or (y_max * 0.02) or 1
        y_domain = [y_min - pad, y_max + pad]

        common_tooltip = [
            alt.Tooltip("날짜:T", title="날짜", format="%Y-%m-%d"),
            alt.Tooltip("Close:Q", title="종가", format=",.0f"),
            alt.Tooltip("전일대비:Q", title="전일대비(%)", format="+.2f"),
        ]
        for col in ma_cols:
            common_tooltip.append(alt.Tooltip(f"{col}:Q", title=col, format=",.0f"))

        color_cycle = ["#F58518", "#54A24B", "#E45756", "#B279A2", "#9D755D"]
        layers = [
            alt.Chart(plot_df).mark_line(color="#4C78A8", size=2.5).encode(
                x=alt.X("날짜:T", title=None),
                y=alt.Y("Close:Q", title=None, scale=alt.Scale(domain=y_domain)),
                tooltip=common_tooltip,
            )
        ]
        for i, col in enumerate(ma_cols):
            layers.append(
                alt.Chart(plot_df).mark_line(
                    color=color_cycle[i % len(color_cycle)],
                    size=2.5 if col == "MA200" else 1.3,
                ).encode(
                    x="날짜:T",
                    y=alt.Y(f"{col}:Q", title=None, scale=alt.Scale(domain=y_domain)),
                    tooltip=common_tooltip,
                )
            )
        if fair_price is not None:
            layers.append(
                alt.Chart(plot_df).mark_line(color="gray", strokeDash=[4, 4]).encode(
                    x="날짜:T",
                    y=alt.Y("적정주가:Q", title=None, scale=alt.Scale(domain=y_domain)),
                    tooltip=common_tooltip,
                )
            )
        
        nearest = alt.selection_point(nearest=True, on='mouseover', fields=['날짜'], empty=False)
        selectors = alt.Chart(plot_df).mark_point(size=500, color='transparent').encode(
            x='날짜:T',
            tooltip=common_tooltip
        ).add_params(nearest)

        rules = alt.Chart(plot_df).mark_rule(color='gray', strokeDash=[4,4]).encode(
            x='날짜:T',
        ).transform_filter(nearest)

        chart = alt.layer(*layers, rules, selectors).resolve_scale(y="shared").properties(height=380).interactive()
        st.altair_chart(chart, use_container_width=True)
    except Exception as e:
        st.warning(f"차트 표시 중 문제가 있어서 기본 차트로 보여드려요. (오류: {e})")
        st.line_chart(df[["Close"] + ma_cols])

def vix_chart_with_zones(vix_series):
    s = vix_series.dropna()
    if s.empty:
        st.line_chart(vix_series)
        return
    plot_df = s.reset_index()
    plot_df.columns = ["날짜", "VIX"]

    nearest = alt.selection_point(nearest=True, on='mouseover', fields=['날짜'], empty=False)
    selectors = alt.Chart(plot_df).mark_point(size=500, color='transparent').encode(
        x='날짜:T', tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), alt.Tooltip("VIX:Q", format=".1f")]
    ).add_params(nearest)
    vertical_rules = alt.Chart(plot_df).mark_rule(color='gray').encode(x='날짜:T').transform_filter(nearest)

    line = alt.Chart(plot_df).mark_line(color="#4C78A8").encode(x=alt.X("날짜:T", title=None), y=alt.Y("VIX:Q", title=None))
    
    bands_df = pd.DataFrame({"y": [20, 30], "label": ["20 안정/불안 경계", "30 불안 진입"]})
    rules = alt.Chart(bands_df).mark_rule(strokeDash=[4, 4], color="#888888").encode(y="y:Q")
    labels_df = bands_df.copy()
    labels_df["날짜"] = plot_df["날짜"].max()
    text = alt.Chart(labels_df).mark_text(align="right", dx=-4, dy=-6, color="#888888", fontSize=11).encode(x="날짜:T", y="y:Q", text="label")

    chart = alt.layer(line, vertical_rules, rules, text, selectors).properties(height=300).interactive()
    st.altair_chart(chart, use_container_width=True)

def line_chart_zoomed(series):
    s = series.dropna()
    if s.empty:
        st.line_chart(series)
        return
    y_min, y_max = float(s.min()), float(s.max())
    pad = (y_max - y_min) * 0.1 or (y_max * 0.02) or 1
    plot_df = s.reset_index()
    plot_df.columns = ["날짜", "값"]
    
    nearest = alt.selection_point(nearest=True, on='mouseover', fields=['날짜'], empty=False)
    selectors = alt.Chart(plot_df).mark_point(size=500, color='transparent').encode(
        x='날짜:T', tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), alt.Tooltip("값:Q", format=",.2f")]
    ).add_params(nearest)
    rules = alt.Chart(plot_df).mark_rule(color='gray').encode(x='날짜:T').transform_filter(nearest)

    line = alt.Chart(plot_df).mark_line(color="#4C78A8").encode(
        x=alt.X("날짜:T", title=None), y=alt.Y("값:Q", title=None, scale=alt.Scale(domain=[y_min - pad, y_max + pad]))
    )
    chart = alt.layer(line, rules, selectors).properties(height=300).interactive()
    st.altair_chart(chart, use_container_width=True)


FALLBACK_KR = {
    "삼성전자": "005930", "SK하이닉스": "000660", "삼성바이오로직스": "207940",
    "LG에너지솔루션": "373220", "현대차": "005380", "기아": "000270",
    "셀트리온": "068270", "POSCO홀딩스": "005490", "삼성SDI": "006400",
    "NAVER": "035420", "카카오": "035720", "LG화학": "051910",
    "삼성물산": "028260", "한미약품": "128940", "유한양행": "000100",
    "SK바이오팜": "326030", "SK이노베이션": "096770", "현대모비스": "012330",
    "LG전자": "066570", "신한지주": "055550", "KB금융": "105560",
    "하나금융지주": "086790", "삼성생명": "032830", "한국전력": "015760",
    "KT": "030200", "SK텔레콤": "017670", "HMM": "011200",
    "대한항공": "003490", "포스코퓨처엠": "003670", "에코프로비엠": "247540",
    "에코프로": "086520", "두산에너빌리티": "034020", "한화에어로스페이스": "012450",
    "LIG넥스원": "079550", "크래프톤": "259960", "엔씨소프트": "036570",
    "하이브": "352820", "JYP Ent.": "035900", "오리온": "271560",
    "CJ제일제당": "097950", "아모레퍼시픽": "090430", "삼성전기": "009150",
    "LG이노텍": "011070", "SK스퀘어": "402340", "두산밥캣": "241560",
    "HD현대중공업": "329180", "넷마블": "251270", "SK바이오사이언스": "302440",
}

US_TICKER_TO_KR = {
    "AAPL": "애플", "MSFT": "마이크로소프트", "GOOGL": "알파벳(구글)", "GOOG": "알파벳(구글)",
    "AMZN": "아마존", "NVDA": "엔비디아", "TSLA": "테슬라", "META": "메타",
    "BRK-B": "버크셔 해서웨이", "LLY": "일라이릴리", "TSM": "TSMC",
    "AVGO": "브로드컴", "V": "비자", "JPM": "JP모건", "WMT": "월마트",
    "JNJ": "존슨앤존슨", "MA": "마스터카드", "PG": "P&G", "HD": "홈디포",
    "COST": "코스트코", "MRK": "머크", "KO": "코카콜라", "PEP": "펩시",
    "NFLX": "넷플릭스", "AMD": "AMD", "DIS": "디즈니", "INTC": "인텔",
    "QCOM": "퀄컴", "PLTR": "팔란티어", "SOFI": "소파이", "ARM": "ARM",
    "SMCI": "슈퍼마이크로", "UNH": "유나이티드헬스", "PFE": "화이자",
    "ABBV": "애브비", "MRNA": "모더나", "BAC": "뱅크오브아메리카",
    "RKLB": "로켓랩", "IONQ": "아이온큐"
}

FALLBACK_US = {
    "Apple": "AAPL", "Microsoft": "MSFT", "Alphabet (Google)": "GOOGL",
    "Amazon": "AMZN", "NVIDIA": "NVDA", "Tesla": "TSLA", "Meta": "META",
    "Berkshire Hathaway": "BRK-B", "Eli Lilly": "LLY", "Johnson & Johnson": "JNJ",
    "UnitedHealth": "UNH", "Pfizer": "PFE", "Merck": "MRK", "AbbVie": "ABBV",
    "Moderna": "MRNA", "Visa": "V", "Mastercard": "MA", "JPMorgan Chase": "JPM",
    "Bank of America": "BAC", "Walmart": "WMT", "Costco": "COST",
    "Coca-Cola": "KO", "PepsiCo": "PEP", "Netflix": "NFLX", "Disney": "DIS",
    "Intel": "INTC", "AMD": "AMD", "Qualcomm": "QCOM", "Broadcom": "AVGO",
    "Rocket Lab": "RKLB", "IonQ": "IONQ", "Palantir": "PLTR", "SoFi": "SOFI",
}

CODE_TO_NAME = {}
CODE_TO_NAME.update({c: n for n, c in FALLBACK_KR.items()})
CODE_TO_NAME.update({c: n for n, c in FALLBACK_US.items()})

PEER_GROUPS = {
    "005930": ["000660"], "000660": ["005930"],
    "373220": ["006400", "096770"], "006400": ["373220", "096770"], "096770": ["373220", "006400"],
    "207940": ["068270", "128940", "000100", "326030"],
    "068270": ["207940", "128940", "000100", "326030"],
    "128940": ["207940", "068270", "000100", "326030"],
    "000100": ["207940", "068270", "128940", "326030"],
    "326030": ["207940", "068270", "128940", "000100"],
    "005380": ["000270"], "000270": ["005380"],
    "035420": ["035720"], "035720": ["035420"],
    "055550": ["105560", "086790"], "105560": ["055550", "086790"], "086790": ["055550", "105560"],
    "AAPL": ["MSFT", "GOOGL"], "MSFT": ["AAPL", "GOOGL"], "GOOGL": ["AAPL", "MSFT"],
    "NVDA": ["AMD", "INTC", "QCOM", "AVGO"], "AMD": ["NVDA", "INTC", "QCOM"],
    "INTC": ["NVDA", "AMD", "QCOM"], "QCOM": ["NVDA", "AMD", "AVGO"], "AVGO": ["NVDA", "QCOM"],
    "LLY": ["JNJ", "PFE", "MRK", "ABBV"], "JNJ": ["LLY", "PFE", "MRK", "ABBV"],
    "PFE": ["LLY", "JNJ", "MRK", "ABBV"], "MRK": ["LLY", "JNJ", "PFE", "ABBV"],
    "ABBV": ["LLY", "JNJ", "PFE", "MRK"],
}

FX_PAIRS = {
    "달러/원 (USD/KRW)": "USD/KRW",
    "엔/원 (JPY/KRW)": "JPY/KRW",
    "유로/원 (EUR/KRW)": "EUR/KRW",
}

COMMODITY_TICKERS = {
    "금 (Gold)": "GC=F",
    "WTI 원유": "CL=F",
    "브렌트유 (Brent)": "BZ=F",
}

POLICY_RATES = {
    "🇰🇷 한국 (한국은행)": {"rate": "2.50%", "as_of": "2026-05-28 동결 (8연속)", "next": "2026-07-16"},
    "🇺🇸 미국 (Fed)": {"rate": "3.50% ~ 3.75%", "as_of": "2026-06-17 동결", "next": "2026-07-28 ~ 29"},
    "🇯🇵 일본 (BOJ)": {"rate": "1.00%", "as_of": "2026-06-16 인상(+0.25%p)", "next": "2026-07-30 ~ 31"},
}

@st.cache_data(ttl=3600, show_spinner=False)
def load_kr_listing():
    try:
        df = fdr.StockListing('KRX')[['Code', 'Name']].dropna()
        return df if not df.empty else None
    except Exception:
        return None

@st.cache_data(ttl=3600, show_spinner=False)
def load_us_listing():
    try:
        df = fdr.StockListing('S&P500').rename(columns={'Symbol': 'Code'})[['Code', 'Name']].dropna()
        df['Code'] = df['Code'].str.replace('.', '-', regex=False)
        return df if not df.empty else None
    except Exception:
        return None

@st.cache_data(ttl=3600, show_spinner=False)
def build_stock_options():
    seen = set()
    options = []

    def add(name, code, kr_name=None):
        if code not in seen:
            if kr_name:
                options.append((f"{kr_name} ({name}) ({code})", code))
            else:
                options.append((f"{name} ({code})", code))
            seen.add(code)

    kr_df = load_kr_listing()
    if kr_df is not None:
        for n, c in zip(kr_df['Name'], kr_df['Code']):
            add(n, c)
    for n, c in FALLBACK_KR.items():
        add(n, c)

    us_df = load_us_listing()
    if us_df is not None:
        for n, c in zip(us_df['Name'], us_df['Code']):
            add(n, c, kr_name=US_TICKER_TO_KR.get(c))
    for n, c in FALLBACK_US.items():
        add(n, c, kr_name=US_TICKER_TO_KR.get(c))

    return options

@st.cache_data(ttl=300, show_spinner=False)
def get_price_data(code, start, end):
    try:
        return fdr.DataReader(code, start, end)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=300, show_spinner=False)
def get_fundamentals(code):
    if code.isdigit():
        try:
            end = datetime.date.today()
            start = end - datetime.timedelta(days=7)
            df_f = pykrx_stock.get_market_fundamental_by_date(
                start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), code
            )
            if df_f.empty:
                return None, None, None, None, "빈 데이터"
            row = df_f.iloc[-1]
            return float(row["EPS"]), float(row["PER"]), float(row["BPS"]), float(row["PBR"]), None
        except Exception as e:
            return None, None, None, None, f"pykrx 호출 오류: {e}"
    else:
        try:
            info = yf.Ticker(code).info
            eps = info.get("trailingEps")
            if eps is None:
                return None, None, None, None, "yfinance 데이터 누락"
            return (
                eps,
                info.get("trailingPE"),
                info.get("priceToSalesTrailing12Months"),
                info.get("enterpriseToRevenue"),
                None,
            )
        except Exception as e:
            return None, None, None, None, f"yfinance 호출 오류: {e}"

@st.cache_data(ttl=3600, show_spinner=False)
def get_earnings_date(code):
    if code.isdigit():
        return "국내 기업 (DART 전자공시 확인 요망)"
    try:
        tk = yf.Ticker(code)
        calendar = tk.calendar
        if calendar and 'Earnings Date' in calendar and len(calendar['Earnings Date']) > 0:
            return calendar['Earnings Date'][0].strftime("%Y-%m-%d")
        ed = tk.get_earnings_dates(limit=5)
        if ed is not None and not ed.empty:
            ed = ed[ed.index >= pd.Timestamp.now(tz='UTC')].sort_index()
            if not ed.empty:
                return ed.index[0].strftime("%Y-%m-%d")
        return "예정된 실적발표일 정보 없음"
    except Exception:
        return "실적발표일 조회 불가"

@st.cache_data(ttl=300, show_spinner=False)
def get_quarterly_earnings(code):
    if code.isdigit():
        return None, "한국 종목의 분기 실적은 DART(전자공시) API 연동이 필요해요."
    try:
        tk = yf.Ticker(code)
        q = getattr(tk, "quarterly_income_stmt", None)
        if q is None or q.empty:
            q = getattr(tk, "quarterly_financials", None)
        if q is None or q.empty:
            return None, "분기 실적 데이터를 가져오지 못했어요."

        def find_row(candidates):
            for c in candidates:
                if c in q.index:
                    return c
            return None

        rev = find_row(["Total Revenue", "TotalRevenue", "Revenue"])
        op = find_row(["Operating Income", "OperatingIncome"])
        net = find_row(["Net Income", "NetIncome", "Net Income Common Stockholders"])
        wanted = [x for x in [rev, op, net] if x]
        if not wanted:
            return None, "매출·영업이익·순이익 항목을 찾지 못했어요."

        result = q.loc[wanted].T.sort_index()
        rename_map = {}
        if rev: rename_map[rev] = "매출"
        if op: rename_map[op] = "영업이익"
        if net: rename_map[net] = "순이익"
        result = result.rename(columns=rename_map) / 1_000_000
        result.index = result.index.strftime("%Y-%m")
        return result, None
    except Exception as e:
        return None, f"yfinance 호출 오류: {e}"

@st.cache_data(ttl=1800, show_spinner=False)
def get_vix(days=180):
    try:
        end = datetime.date.today()
        start = end - datetime.timedelta(days=days)
        df = fdr.DataReader('VIX', start, end)
        return df, None
    except Exception as e:
        return None, f"VIX 호출 오류: {e}"

@st.cache_data(ttl=1800, show_spinner=False)
def get_fear_greed():
    try:
        start_date = (datetime.date.today() - datetime.timedelta(days=400)).strftime("%Y-%m-%d")
        url = f"https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start_date}"
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://edition.cnn.com/markets/fear-and-greed",
        }
        resp = requests.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        hist = data["fear_and_greed_historical"]["data"]
        hist_df = pd.DataFrame(hist)
        hist_df["x"] = pd.to_datetime(hist_df["x"], unit="ms")
        hist_df = hist_df.rename(columns={"x": "날짜", "y": "지수"}).set_index("날짜")
        score = float(hist_df["지수"].iloc[-1])
        return score, hist_df, None
    except Exception as e:
        return None, None, f"공포탐욕지수 호출 오류: {type(e).__name__}: {e}"

@st.cache_data(ttl=1800, show_spinner=False)
def get_fx(pair, days=180):
    try:
        end = datetime.date.today()
        start = end - datetime.timedelta(days=days)
        df = fdr.DataReader(pair, start, end)
        return df, None
    except Exception as e:
        return None, f"{pair} 호출 오류: {e}"

@st.cache_data(ttl=1800, show_spinner=False)
def get_commodity(ticker, days=180):
    try:
        end = datetime.date.today()
        start = end - datetime.timedelta(days=days)
        df = yf.Ticker(ticker).history(start=start, end=end)
        if df.empty:
            return None, "데이터가 비어있어요."
        return df, None
    except Exception as e:
        return None, f"호출 오류: {e}"

if "watchlist" not in st.session_state:
    st.session_state.watchlist = []
if "view_history" not in st.session_state:
    st.session_state.view_history = []
if "current_df" not in st.session_state:
    st.session_state.current_df = None
if "current_name" not in st.session_state:
    st.session_state.current_name = None
if "current_code" not in st.session_state:
    st.session_state.current_code = None
if "current_ma" not in st.session_state:
    st.session_state.current_ma = []
if "memos" not in st.session_state:
    st.session_state.memos = {}
if "last_toast_code" not in st.session_state:
    st.session_state.last_toast_code = None

st.title("📈 주식 가격 분석 대시보드")
st.caption("관심 종목의 가격 추이와 기본 통계를 한눈에 확인해보세요.")

with st.sidebar:
    st.header("🔍 종목 검색")
    all_options = build_stock_options()
    labels = [o[0] for o in all_options]
    code_map = dict(all_options)

    if "stock_selector" not in st.session_state:
        default_label = next((l for l, c in all_options if c == "005930"), labels[0] if labels else None)
        st.session_state.stock_selector = default_label
    
    picked_label = st.selectbox(
        "종목명 또는 코드 (타이핑 후 Enter)",
        labels,
        key="stock_selector"
    )
    code = code_map[picked_label]
    name = picked_label.rsplit(" (", 1)[0]

    st.divider()
    period = st.radio("조회 기간", ["1개월", "3개월", "6개월", "1년"], index=2)

    st.caption("이동평균선 — 200일선(장기, 항상 포함) + 필요한 단기선만 선택")
    extra_ma = st.multiselect("단기 이동평균선 추가 (5일·20일·60일·120일)", [5, 20, 60, 120], default=[])
    ma_periods = sorted(set(extra_ma) | {200})

    st.divider()
    st.subheader("⭐ 관심 종목")
    if st.session_state.watchlist:
        for w in st.session_state.watchlist:
            st.write(f"- {w}")
    else:
        st.caption("아직 추가된 종목이 없어요.")

days_map = {"1개월": 30, "3개월": 90, "6개월": 180, "1년": 365}
MA_BUFFER_DAYS = 320

end_date = datetime.date.today()
display_start = end_date - datetime.timedelta(days=days_map[period])
fetch_start = display_start - datetime.timedelta(days=MA_BUFFER_DAYS)

with st.spinner(f"번개처럼 데이터를 불러오는 중..."):
    raw_df = get_price_data(code, fetch_start, end_date)

if raw_df.empty:
    st.error("데이터를 불러오지 못했어요. 종목 코드를 확인해주세요.")
else:
    for p in ma_periods:
        raw_df[f"MA{p}"] = raw_df["Close"].rolling(p).mean()
    raw_df["RSI"] = compute_rsi(raw_df["Close"])
    raw_df["일일수익률"] = raw_df["Close"].pct_change()

    df = raw_df.loc[raw_df.index >= pd.Timestamp(display_start)].copy()

    st.session_state.current_df = df
    st.session_state.current_name = name
    st.session_state.current_code = code
    st.session_state.current_ma = ma_periods

    if name not in st.session_state.view_history:
        st.session_state.view_history.append(name)
        
    if st.session_state.last_toast_code != code:
        if len(df) > 1:
            cur_price_check = df["Close"].iloc[-1]
            prv_price_check = df["Close"].iloc[-2]
            daily_change_pct = (cur_price_check / prv_price_check - 1) * 100
            
            if daily_change_pct > 0:
                st.toast(f"🚀 {name} 상승 마감! ({daily_change_pct:+.2f}%)", icon="📈")
            elif daily_change_pct < 0:
                st.toast(f"📉 {name} 하락 마감... ({daily_change_pct:+.2f}%)", icon="📉")
        st.session_state.last_toast_code = code

if st.session_state.current_df is not None:
    df = st.session_state.current_df
    shown_name = st.session_state.current_name
    shown_code = st.session_state.current_code
    shown_ma = st.session_state.current_ma
    current_price = df["Close"].iloc[-1]
    currency_symbol = "₩" if shown_code.isdigit() else "$"

    with st.spinner("재무 데이터 불러오는 중..."):
        eps, per, alt1, alt2, fund_err = get_fundamentals(shown_code)

    fair_price = None
    valuation_label = None
    default_per = default_pbr = None
    fair_per_key = fair_pbr_key = fair_psr_key = None

    if eps is not None and eps > 0:
        default_per = float(per) if per else 15.0
        fair_per_key = f"fair_per_{shown_code}"
        fair_per_now = st.session_state.get(fair_per_key, default_per)
        fair_price = eps * fair_per_now
        valuation_label = f"적정주가(PER {fair_per_now:.1f}배)"
    elif eps is not None and eps <= 0:
        if shown_code.isdigit():
            bps, pbr = alt1, alt2
            if bps is not None:
                default_pbr = max(pbr, 0.1)
                fair_pbr_key = f"fair_pbr_{shown_code}"
                fair_pbr_now = st.session_state.get(fair_pbr_key, default_pbr)
                fair_price = bps * fair_pbr_now
                valuation_label = f"적정주가(PBR {fair_pbr_now:.1f}배)"
        else:
            psr, evs = alt1, alt2
            if psr is not None:
                fair_psr_key = f"fair_psr_{shown_code}"
                fair_psr_now = st.session_state.get(fair_psr_key, float(psr))
                fair_price = current_price * (fair_psr_now / psr)
                valuation_label = f"적정주가(PSR {fair_psr_now:.1f}배)"

    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs(
        ["📉 가격 차트", "📊 통계 요약", "💰 적정주가", "📋 실적보고서",
         "😨 시장 지수", "💱 환율", "🏦 기준금리", "🛢️ 원자재"]
    )

    with tab1:
        prev_close = df["Close"].iloc[-2] if len(df) > 1 else current_price
        daily_drop = current_price - prev_close
        daily_pct = (current_price / prev_close - 1) * 100 if prev_close else 0

        if daily_drop > 0:
            price_color = "#FF4B4B"
            arrow = "▲"
        elif daily_drop < 0:
            price_color = "#3B82F6"
            arrow = "▼"
        else:
            price_color = "inherit"
            arrow = "-"

        st.markdown(
            f"""
            <div style='background-color: transparent; padding: 5px 0px; margin-bottom: 10px;'>
                <div style='font-size: 1.1rem; color: #888; margin-bottom: 4px;'>{shown_name} 현재가 (전일 대비)</div>
                <div style='font-size: 2.8rem; font-weight: 700; color: {price_color}; display: flex; align-items: baseline; gap: 12px;'>
                    {currency_symbol}{current_price:,.0f}
                    <span style='font-size: 1.3rem; font-weight: 600;'>
                        {arrow} {abs(daily_drop):,.0f} ({daily_pct:+.2f}%)
                    </span>
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )
        
        ma_cols = [f"MA{p}" for p in shown_ma]
        build_price_chart(df, ma_cols, fair_price)
        st.caption("💡 팁: 차트 영역 안으로 마우스를 가져가기만 하면 세로선(크로스헤어)이 나타나 쉽게 날짜별 수치를 확인할 수 있습니다. 휠을 굴리거나 드래그하여 확대/이동도 가능해요.")

        show_rsi = st.checkbox("RSI(14일) 같이 보기", value=False)
        if show_rsi:
            st.divider()
            rsi_df = df[["RSI"]].copy()
            rsi_df["과매수(70)"] = 70
            rsi_df["과매도(30)"] = 30
            st.line_chart(rsi_df)
            
        st.divider()
        st.subheader("📝 일자별 메모")
        st.caption("이 종목에 대한 매매 일지나 주요 이슈를 기록해보세요. 내용을 비우고 저장하면 메모가 삭제됩니다.")
        
        if shown_code not in st.session_state.memos:
            st.session_state.memos[shown_code] = {}
            
        with st.form(key=f"memo_form_{shown_code}", clear_on_submit=True):
            col_date, col_text, col_btn = st.columns([2, 5, 1])
            memo_date = col_date.date_input("날짜", value=datetime.date.today())
            memo_text = col_text.text_input("메모 내용")
            submit_memo = col_btn.form_submit_button("저장")
            
        if submit_memo:
            date_str = memo_date.strftime("%Y-%m-%d")
            if memo_text.strip():
                st.session_state.memos[shown_code][date_str] = memo_text.strip()
                st.success(f"✔️ {date_str} 메모가 정상적으로 저장되었습니다!")
            else:
                if date_str in st.session_state.memos[shown_code]:
                    del st.session_state.memos[shown_code][date_str]
                    st.info(f"🗑️ {date_str} 메모가 삭제되었습니다.")

        saved_memos = st.session_state.memos.get(shown_code, {})
        if saved_memos:
            memo_df = pd.DataFrame(list(saved_memos.items()), columns=["날짜", "메모"])
            memo_df = memo_df.sort_values("날짜", ascending=False).reset_index(drop=True)
            st.dataframe(memo_df, use_container_width=True)

    with tab2:
        col1, col2, col3, col4 = st.columns(4)
        total_return = (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
        volatility = df["일일수익률"].std() * 100
        max_price = df["Close"].max()
        min_price = df["Close"].min()

        col1.metric("지정 기간 수익률", f"{total_return:.2f}%", delta=f"{total_return:.2f}%")
        col2.metric("일일 변동성 (표준편차)", f"{volatility:.2f}%")
        col3.metric("최고가 / 최저가", f"{max_price:,.0f} / {min_price:,.0f}")
        col4.metric("다음 실적발표일(예정)", get_earnings_date(shown_code))

        st.divider()
        st.write("최근 5거래일 데이터")
        st.dataframe(fmt_commas(df.tail(5)[["Open", "High", "Low", "Close", "Volume"]]))

    with tab3:
        # 🔥 사용자 요청에 따라 가장 상단에 미장 전용 안내 문구 추가
        st.info("💡 **안내:** 이 탭의 동종업계 비교 및 적정주가 산출 기능은 현재 **미국 주식(미장) 전용**으로 구성되어 있습니다.")

        if eps is None:
            st.warning("EPS·PER 데이터를 가져오지 못했어요. (상장 직후 종목이거나 일시적 오류일 수 있어요)")

        elif eps > 0:
            col1, col2 = st.columns(2)
            col1.metric("EPS (주당순이익)", f"{currency_symbol}{eps:,.0f}")
            col2.metric("현재 PER", f"{per:.1f}배" if per else "N/A")
            
            st.divider()
            st.write("🔎 **동종업계 피어(Peer) 비교 및 적정주가 산출**")
            st.caption("검색하여 종목을 추가/제외하면 평균 PER이 실시간 연산되어 아래 적정주가 공식에 **자동으로 반영**됩니다.")

            current_peers = PEER_GROUPS.get(shown_code, [])
            default_sel = []
            for p in current_peers:
                matched = next((l for l in labels if l.endswith(f"({p})")), None)
                if matched: default_sel.append(matched)

            peer_selections = st.multiselect(
                "동종업계 종목 검색", options=labels, default=default_sel, key=f"peer_{shown_code}"
            )

            avg_peer_per = None
            if peer_selections:
                with st.spinner("종목 데이터를 가져오는 중입니다..."):
                    peer_rows = []
                    for p_label in peer_selections:
                        p_code = code_map[p_label]
                        _, p_per, _, _, _ = get_fundamentals(p_code)
                        if p_per: 
                            short_name = p_label.split(" (")[0]
                            peer_rows.append({"종목": short_name, "PER": round(p_per, 1)})
                
                if peer_rows:
                    peer_df = pd.DataFrame(peer_rows)
                    avg_peer_per = peer_df["PER"].mean()
                    peer_df.loc[len(peer_df)] = {"종목": "💡 평균 PER", "PER": round(avg_peer_per, 1)}
                    st.dataframe(peer_df, use_container_width=True)
                    st.success(f"✔️ 동종업계 평균 PER **{avg_peer_per:.1f}배**를 적정 PER로 하단 공식에 적용했습니다.")
            
            target_per = avg_peer_per if avg_peer_per else default_per

            fair_price_tab = eps * target_per
            gap = (fair_price_tab / current_price - 1) * 100

            st.divider()
            col3, col4, col5 = st.columns(3)
            col3.metric("현재가", f"{currency_symbol}{current_price:,.0f}")
            col4.metric(f"적정주가 (EPS × {target_per:.1f}배)", f"{currency_symbol}{fair_price_tab:,.0f}")
            col5.metric("현재가 대비 차이", f"{gap:+.1f}%", delta=f"{gap:+.1f}%")

        else:
            st.warning(f"EPS가 {eps:,.0f}로 적자 상태라 PER 기반 적정주가는 의미가 없어요.")

    with tab4:
        with st.spinner("분기 실적 불러오는 중..."):
            q_data, q_err = get_quarterly_earnings(shown_code)

        if q_data is None:
            st.info(q_err)
        else:
            n = len(q_data)
            st.caption(f"최근 {n}개 분기 (~{n/4:.1f}년) 실적 — 단위: 백만달러.")
            st.bar_chart(q_data)
            st.divider()
            st.write("분기별 상세 수치 (백만달러)")
            st.dataframe(fmt_commas(q_data))

    with tab5:
        st.subheader("😨 시장 심리 지수")
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**VIX (변동성 지수)**")
            vix_df, vix_err = get_vix()
            if vix_df is not None:
                st.metric("현재 VIX", f"{vix_df['Close'].iloc[-1]:.1f}")
                vix_chart_with_zones(vix_df["Close"])
            else:
                st.info(vix_err)

        with col2:
            st.markdown("**공포탐욕지수 (Fear & Greed)**")
            fg_score, fg_hist, fg_err = get_fear_greed()
            if fg_score is not None:
                st.metric("현재 지수", f"{fg_score:.0f} ({fg_rating_label(fg_score)})")
                st.line_chart(fg_hist["지수"])
            else:
                st.info(fg_err)

    with tab6:
        st.subheader("💱 환율")
        cols = st.columns(len(FX_PAIRS))
        for c, (label, pair) in zip(cols, FX_PAIRS.items()):
            fx_df, fx_err = get_fx(pair)
            with c:
                if fx_df is not None and len(fx_df) >= 2:
                    rate = fx_df["Close"].iloc[-1]
                    chg = (fx_df["Close"].iloc[-1] / fx_df["Close"].iloc[-2] - 1) * 100
                    st.metric(label, f"{rate:,.2f}", delta=f"{chg:+.2f}%")
                    if pair == "JPY/KRW":
                        jpy_per_100_krw = rate * 100 if rate < 50 else rate
                        st.caption(f"💡 (100엔 기준 = 약 {jpy_per_100_krw:,.1f} 원)")
                else:
                    st.metric(label, "N/A")

    with tab7:
        st.subheader("🏦 주요국 기준금리")
        cols = st.columns(3)
        for c, (rname, info) in zip(cols, POLICY_RATES.items()):
            with c:
                st.metric(rname, info["rate"])
                st.caption(f"최근 결정: {info['as_of']} / 다음: {info['next']}")

    with tab8:
        st.subheader("🛢️ 금 & 원유")
        cols = st.columns(len(COMMODITY_TICKERS))
        for c, (label, ticker) in zip(cols, COMMODITY_TICKERS.items()):
            cdf, cerr = get_commodity(ticker)
            with c:
                if cdf is not None and len(cdf) >= 2:
                    price = cdf["Close"].iloc[-1]
                    chg = (cdf["Close"].iloc[-1] / cdf["Close"].iloc[-2] - 1) * 100
                    st.metric(label, f"${price:,.2f}", delta=f"{chg:+.2f}%")
                else:
                    st.metric(label, "N/A")

    if st.button(f"⭐ {shown_name} 관심종목에 추가"):
        if shown_name not in st.session_state.watchlist:
            st.session_state.watchlist.append(shown_name)
            st.success(f"{shown_name}을 관심종목에 추가했어요!")
        else:
            st.info("이미 추가된 종목이에요.")

def set_stock(label): st.session_state.stock_selector = label

if st.session_state.view_history:
    st.divider()
    st.write("🕘 **오늘 조회한 종목 (클릭하면 바로 이동합니다)**")
    
    unique_history = list(dict.fromkeys(reversed(st.session_state.view_history)))
    cols = st.columns(min(len(unique_history), 8))
    
    for i, hist_name in enumerate(unique_history[:8]):
        matched_label = next((l for l in labels if l.startswith(hist_name)), None)
        if matched_label:
            cols[i].button(hist_name, key=f"hist_btn_{i}", on_click=set_stock, args=(matched_label,))