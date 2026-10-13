"""策略與風控參數（不含 API 密鑰）。"""

DEFAULT_SYMBOL = "BTC/USDT"

# --- 預設策略（儀表板／CLI 單策略情境）---
STRATEGY = "hunting2"  # "hunting_funding" | "hunting2" | "smc_ict" | "ivb_orderflow"
TIMEFRAME = "5m"

ALLOWED_SIDE: str | None = None

STATE_FILE = "state.json"

# --- Hunting Funding（OI/CVD 五星評分）---
HUNTING_FUNDING_TIMEFRAME = "5m"
HUNTING_LOOKBACK = 4
HUNTING_MIN_STARS = 5
HUNTING_COOLDOWN_BARS = 24
HUNTING_HTF_EMA_LEN = 150
HUNTING_MAX_DIST_PCT = 5.0
HUNTING_SL_SWING = 24
HUNTING_MAX_SL_PCT = 5.0
HUNTING_TP1_REDUCE_PCT = 0.30
HUNTING_VOL_LEN = 20
HUNTING_MOM_LEN = 10
HUNTING_OI_MIN_PCT = 0.0
HUNTING_USE_OI = True
HUNTING_USE_CVD = True
HUNTING_USE_VOL = True
HUNTING_USE_TREND = True
HUNTING_USE_MOM = True
HUNTING_W_OI = 1.0
HUNTING_W_CVD = 1.0
HUNTING_W_VOL = 1.0
HUNTING_W_TREND = 1.0
HUNTING_W_MOM = 1.0
HUNTING_TOTAL_CAPITAL = 100.0
HUNTING_POSITION_PCT = 1.0          # 每 leg 保證金佔總資金 %
HUNTING_MAX_CONCURRENT_POSITIONS = 20
HUNTING_MAX_MARGIN_USAGE_PCT = 85.0  # 已用保證金超過此比例則拒絕新單
HUNTING_USE_DIRECTION_COOLDOWN = False
HUNTING_MAX_CONSECUTIVE_SL_DIR = 2
HUNTING_OI_MAX_HIST = 500          # Binance openInterestHist 單次上限

# --- Hunting 2.0（Pine 更新版：五星 + EMA150 + 4H 收盤突破 + SL buffer）---
HUNTING2_TIMEFRAME = "5m"
HUNTING2_LOOKBACK = 4
HUNTING2_MIN_STARS = 5
HUNTING2_COOLDOWN_BARS = 24
HUNTING2_USE_HTF = True
HUNTING2_HTF_EMA_LEN = 150
HUNTING2_TREND_EMA_LEN = 150
HUNTING2_MAX_DIST_PCT = 7.0
HUNTING2_USE_4H = True
HUNTING2_H4_BARS = 15
HUNTING2_SL_SWING = 24
HUNTING2_MAX_SL_PCT = 10.0
HUNTING2_SL_BUFFER_PCT = 1.0       # 止損預留緩衝 %（對應 Pine slBuffer）
HUNTING2_TP1_REDUCE_PCT = 0.30
HUNTING2_VOL_LEN = 20
HUNTING2_MOM_LEN = 10
HUNTING2_OI_MIN_PCT = 0.0
HUNTING2_USE_OI = True
HUNTING2_USE_CVD = True
HUNTING2_USE_VOL = True
HUNTING2_USE_TREND = True
HUNTING2_USE_MOM = True
HUNTING2_W_OI = 1.0
HUNTING2_W_CVD = 1.0
HUNTING2_W_VOL = 1.0
HUNTING2_W_TREND = 1.0
HUNTING2_W_MOM = 1.0
HUNTING2_TOTAL_CAPITAL = 100.0
HUNTING2_POSITION_PCT = 1.0
HUNTING2_MAX_CONCURRENT_POSITIONS = 20
HUNTING2_MAX_MARGIN_USAGE_PCT = 85.0
HUNTING2_USE_DIRECTION_COOLDOWN = False
HUNTING2_MAX_CONSECUTIVE_SL_DIR = 2
HUNTING2_OI_MAX_HIST = 500

# --- SMC / ICT（15m · BOS + Liquidity Sweep + Order Block）---
SMC_TIMEFRAME = "15m"
SMC_SWING_LEFT = 2
SMC_SWING_RIGHT = 2
SMC_OB_LOOKBACK = 20
SMC_OB_TOUCH_PAD_PCT = 0.05
SMC_SWEEP_TOLERANCE_PCT = 0.05
SMC_SWEEP_MAX_BARS = 8
SMC_ENTRY_EXPIRE_BARS = 12
SMC_COOLDOWN_BARS = 8
SMC_SL_BUFFER_PCT = 0.01
SMC_MAX_SL_PCT = 5.0
SMC_TP1_REDUCE_PCT = 0.30
SMC_RR_TP2 = 3.0
SMC_RR_TP3 = 5.0
SMC_TOTAL_CAPITAL = 100.0
SMC_POSITION_PCT = 1.0

# --- IVB Order Flow（NY Initial Balance + VP + Absorption proxy）---
IVB_TIMEFRAME = "5m"
IVB_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
IVB_MINUTES = 30                      # Initial Balance 分鐘數
IVB_SESSION = "NY"                    # NY | UTC（UTC 時用 IVB_UTC_*）
IVB_NY_OPEN = "09:30"                 # America/New_York
IVB_NY_CLOSE = "16:00"
IVB_UTC_OPEN = "13:30"                # 若 SESSION=UTC
IVB_UTC_CLOSE = "20:00"
IVB_VP_BINS = 24
IVB_VA_PCT = 0.70
IVB_MIN_IB_BARS = 3
IVB_COOLDOWN_BARS = 6
IVB_MAX_ENTRIES_PER_DAY = 2
IVB_MAX_CONCURRENT = 1
IVB_ZONE_PAD_PCT = 0.05
IVB_ATR_LEN = 14
IVB_VOL_MA_LEN = 20
IVB_ABS_VOL_MULT = 2.0                # absorption：量能相對均量
IVB_ABS_RANGE_ATR = 0.35              # absorption：振幅相對 ATR
IVB_SL_BUFFER_PCT = 0.01
IVB_FALLBACK_SL_PCT = 0.35
IVB_MAX_SL_PCT = 3.0
IVB_TP1_REDUCE_PCT = 0.30
IVB_RR_TP1 = 1.0
IVB_RR_TP2 = 2.0                      # protection（personal）
IVB_RR_FINAL = 2.0
IVB_RISK_PROFILE = "personal"
IVB_TOTAL_CAPITAL = 100.0
IVB_RISK_PCT_PER_TRADE = 0.35         # personal：單筆風險約 0.35%
IVB_ALLOW_LONG = True
IVB_ALLOW_SHORT = True


def active_timeframe() -> str:
    """依目前 STRATEGY 回傳對應週期。"""
    if STRATEGY == "hunting_funding":
        return HUNTING_FUNDING_TIMEFRAME
    if STRATEGY == "hunting2":
        return HUNTING2_TIMEFRAME
    if STRATEGY == "smc_ict":
        return SMC_TIMEFRAME
    if STRATEGY == "ivb_orderflow":
        return IVB_TIMEFRAME
    return TIMEFRAME


def timeframe_minutes(timeframe: str | None = None) -> int:
    """將 '1h'、'5m' 等字串轉為每根 K 的分鐘數。"""
    tf = (timeframe or active_timeframe()).strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    if tf.endswith("d"):
        return int(tf[:-1]) * 1440
    raise ValueError(f"無法解析 timeframe: {tf}")
