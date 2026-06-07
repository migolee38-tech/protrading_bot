"""策略與風控參數（不含 API 密鑰）。"""

DEFAULT_SYMBOL = "BTC/USDT"

# --- EMA 趨勢交叉策略（勿與唐奇安共用下列參數）---
STRATEGY = "ema"  # "ema" | "donchian" | "rsi" | "macd" | "hunting_funding"
TIMEFRAME = "5m"

EMA_FAST = 12
EMA_MID = 30
EMA_SLOW = 55
EMA_CROSS_SLOW = 55
EMA_VOLUME_PRICE = 20

TREND_BARS_MIN = 48
STOP_LOOKBACK = 48
SOFT_STOP_PCT = 0.12
MAX_STOP_PCT = 0.20
STOP_BUFFER_PCT = 0.01

VOLUME_MA = 20

RR_FINAL = 10.0
RR_PARTIAL_1 = 1.0
RR_PARTIAL_2 = 2.0
REDUCE_AT_1R_PCT = 0.30
REDUCE_AT_2R_PCT = 0.30
TRAIL_STEP_R = 0.5

SL_WINDOW_HOURS = 24
MAX_CONSECUTIVE_SL = 2
COOLDOWN_HOURS = 24

ALLOWED_SIDE: str | None = None

# --- 唐奇安（唐安麒）策略專用；不影響上方 EMA 設定 ---
DONCHIAN_TIMEFRAME = "1h"
DONCHIAN_LEN = 100
DONCHIAN_SL_BUFFER_PCT = 0.01
DONCHIAN_MAX_SL_PCT = 0.10
DONCHIAN_RISK_USDT = 2.0
DONCHIAN_ENTRY_EXPIRE_BARS = 24
DONCHIAN_RR_TP1 = 2.0
DONCHIAN_RR_TP2 = 5.0
DONCHIAN_RR_TP3 = 10.0
DONCHIAN_REDUCE_TP1_PCT = 0.50
DONCHIAN_REDUCE_TP2_PCT = 0.50
DONCHIAN_STOP_AFTER_TP2_R = 3.0
DONCHIAN_TRAIL_OFFSET_R = 2.0

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
HUNTING_POSITION_PCT = 2.0
HUNTING_USE_DIRECTION_COOLDOWN = True
HUNTING_MAX_CONSECUTIVE_SL_DIR = 2
HUNTING_OI_MAX_HIST = 500          # Binance openInterestHist 單次上限


def active_timeframe() -> str:
    """依目前 STRATEGY 回傳對應週期（EMA 用 TIMEFRAME，唐奇安用 DONCHIAN_TIMEFRAME）。"""
    if STRATEGY == "donchian":
        return DONCHIAN_TIMEFRAME
    if STRATEGY == "hunting_funding":
        return HUNTING_FUNDING_TIMEFRAME
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
    raise ValueError(f"不支援的時間週期: {tf}")
