"""
Hunting Funding — Python 完整移植版
100% 對應 Pine Script v6 邏輯（含自訂修訂）
功能：即時掃描 + 發報 + 自動交易進場 + 回測分析
資料來源：Binance 永續合約 API（USDT-M Futures / USDT.P）
"""

import os
import time
import math
import logging
import argparse
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import requests

from core.universe import top_usdt_pairs_by_volume

# ── 選用套件（telegram / binance order） ──────────────────────────
try:
    import telegram
    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False

try:
    from binance.um_futures import UMFutures
    HAS_BINANCE_CLIENT = True
except ImportError:
    HAS_BINANCE_CLIENT = False

# ═══════════════════════════════════════════════════════════════════
# 日誌設定
# ═══════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("hunting_funding.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("HuntingFunding")

# 預設掃描每日 24h 成交量 Top N 的 USDT 永續（USDT.P）
SYMBOL_TOP = "TOP"


# ═══════════════════════════════════════════════════════════════════
# 參數設定（對應 Pine Script 所有 input）
# ═══════════════════════════════════════════════════════════════════
@dataclass
class Config:
    # ── 交易對 / 週期 ────────────────────────────────────────────
    symbol: str        = SYMBOL_TOP   # TOP = 每日成交量 Top N USDT 永續
    top_n: int         = 100          # 成交量榜單數量
    interval: str      = "5m"          # Binance K 線週期

    # ── 評分設定 ─────────────────────────────────────────────────
    lookback: int      = 4             # OI/CVD 變化回看根數
    min_stars: int     = 5             # 進場需滿五星
    cooldown: int      = 24            # 訊號冷卻根數
    show_long: bool    = True
    show_short: bool   = True

    # ── 趨勢閘門 (EMA150) ────────────────────────────────────────
    use_htf: bool      = True
    htf_ema_len: int   = 150
    max_dist_pct: float= 5.0           # 距 EMA150 最大距離 %

    # ── ① OI ─────────────────────────────────────────────────────
    use_oi: bool       = True
    w_oi: float        = 1.0
    oi_min_pct: float  = 0.0           # OI 最小變化幅度 %

    # ── ② CVD ────────────────────────────────────────────────────
    use_cvd: bool      = True
    w_cvd: float       = 1.0

    # ── ③ 量能 ───────────────────────────────────────────────────
    use_vol: bool      = True
    w_vol: float       = 1.0
    vol_len: int       = 20

    # ── ④ 趨勢（EMA150）──────────────────────────────────────────
    use_trend: bool    = True
    w_trend: float     = 1.0

    # ── ⑤ 動能 ───────────────────────────────────────────────────
    use_mom: bool      = True
    w_mom: float       = 1.0
    mom_len: int       = 10

    # ── 風險 / 目標 ───────────────────────────────────────────────
    show_rr: bool      = True
    sl_swing: int      = 24            # 止損波段回看根數
    max_sl_pct: float  = 5.0           # 開倉→止損最大距離 %
    tp1_reduce_pct: float = 0.30         # 1R 減倉比例

    # ── 自動交易 ─────────────────────────────────────────────────
    auto_trade: bool   = False         # 開啟才會真實下單
    total_capital: float = 100.0       # 總資金 USDT
    position_pct: float  = 2.0         # 每單保證金佔總資金 %
    leverage: int      = 0             # 0 = 使用該交易對最大槓桿

    @property
    def margin_per_trade(self) -> float:
        return self.total_capital * self.position_pct / 100.0

    # ── Binance API ───────────────────────────────────────────────
    api_key: str       = ""
    api_secret: str    = ""
    testnet: bool      = True          # 預設用測試網，安全第一

    # ── Telegram 通知 ─────────────────────────────────────────────
    tg_token: str      = ""
    tg_chat_id: str    = ""

    # ── 方向冷卻（連續止損）──────────────────────────────────────
    use_direction_cooldown: bool = True
    max_consecutive_sl_dir: int  = 2   # 同方向連續止損 N 次後冷卻

    # ── 回測設定 ─────────────────────────────────────────────────
    backtest_limit: int = 1000         # 回測 K 線根數（最大 1500）


# ═══════════════════════════════════════════════════════════════════
# Binance REST 工具
# ═══════════════════════════════════════════════════════════════════
FAPI = "https://fapi.binance.com"


def fetch_top_volume_symbols(top_n: int = 100) -> list[str]:
    """取得每日 24h 成交量 Top N 的 USDT 永續合約（USDT.P）。"""
    df = top_usdt_pairs_by_volume(top_n=top_n, market="futures")
    if df.empty:
        log.warning("成交量榜單為空，無可掃描交易對。")
        return []
    return df["symbol"].tolist()


def resolve_symbols(cfg: Config) -> list[str]:
    """將設定中的 symbol 解析為實際交易對清單。"""
    sym = cfg.symbol.upper()
    if sym in (SYMBOL_TOP, "ALL"):
        return fetch_top_volume_symbols(cfg.top_n)
    return [sym]


def fetch_klines(symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    """抓永續合約 K 線，回傳 DataFrame"""
    url = f"{FAPI}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    df = pd.DataFrame(data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_vol","trades","taker_buy_base",
        "taker_buy_quote","ignore"
    ])
    for col in ["open","high","low","close","volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("open_time", inplace=True)
    return df


def fetch_open_interest_history(symbol: str, interval: str, limit: int = 500) -> pd.Series:
    """
    抓 OI 歷史（/futures/data/openInterestHist）
    回傳與 K 線對齊的 Series（index = timestamp）
    注意：Binance OI hist 最多回 500 筆，且最小週期 5m
    """
    period_map = {
        "1m":"5m","3m":"5m","5m":"5m","15m":"15m",
        "30m":"30m","1h":"1h","2h":"2h","4h":"4h",
        "6h":"6h","12h":"12h","1d":"1d"
    }
    period = period_map.get(interval, "5m")
    url = f"{FAPI}/futures/data/openInterestHist"
    params = {"symbol": symbol, "period": period, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data:
        return pd.Series(dtype=float)
    s = pd.Series(
        {pd.Timestamp(d["timestamp"], unit="ms", tz="UTC"): float(d["sumOpenInterest"])
         for d in data}
    )
    return s


def fetch_latest_oi(symbol: str) -> Optional[float]:
    """抓最新一筆 OI"""
    url = f"{FAPI}/fapi/v1/openInterest"
    r = requests.get(url, params={"symbol": symbol}, timeout=5)
    r.raise_for_status()
    return float(r.json()["openInterest"])


# ═══════════════════════════════════════════════════════════════════
# 指標計算（100% 對應 Pine Script 邏輯）
# ═══════════════════════════════════════════════════════════════════

def calc_ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def calc_cvd(df: pd.DataFrame) -> pd.Series:
    """
    Pine: barDelta = close >= open ? volume : -volume
          cvd 累加
    """
    delta = np.where(df["close"] >= df["open"], df["volume"], -df["volume"])
    return pd.Series(delta, index=df.index).cumsum()


def star_str(n: int) -> str:
    return {1:"★", 2:"★★", 3:"★★★", 4:"★★★★", 5:"★★★★★"}.get(n, "·")


@dataclass
class BarResult:
    """單根 K 棒計算結果"""
    ts: pd.Timestamp
    close: float
    stars_l: int
    stars_s: int
    htf_ema: float
    dist_pct: float
    sl_long: float
    sl_short: float
    sl_pct_long: float
    sl_pct_short: float
    long_sig: bool
    short_sig: bool


class HuntingEngine:
    """
    Pine Script 邏輯移植
    狀態機變數用 instance variable 取代 Pine var
    """
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def compute(self, df: pd.DataFrame, oi_series: pd.Series) -> list[BarResult]:
        """
        傳入完整 K 線 DataFrame + OI Series，回傳每根 BarResult
        df 需包含：open, high, low, close, volume
        """
        cfg = self.cfg
        n   = len(df)

        cvd       = calc_cvd(df)
        vol_ma    = df["volume"].rolling(cfg.vol_len).mean()
        htf_ema   = calc_ema(df["close"], cfg.htf_ema_len)

        if not oi_series.empty:
            oi_aligned = oi_series.reindex(df.index, method="ffill")
        else:
            oi_aligned = pd.Series(np.nan, index=df.index)

        win = cfg.sl_swing + 1
        swing_low  = df["low"].rolling(win).min()
        swing_high = df["high"].rolling(win).max()

        results = []
        last_bar_l: Optional[int] = None
        last_bar_s: Optional[int] = None

        for i in range(n):
            close_i = df["close"].iloc[i]
            htf_i   = htf_ema.iloc[i]

            if not np.isnan(htf_i) and htf_i != 0:
                dist_pct = abs(close_i - htf_i) / htf_i * 100
            else:
                dist_pct = np.nan

            bull_htf = (not cfg.use_htf) or (not np.isnan(htf_i) and close_i > htf_i)
            bear_htf = (not cfg.use_htf) or (not np.isnan(htf_i) and close_i < htf_i)
            near_ema = (not cfg.use_htf) or (not np.isnan(dist_pct) and dist_pct <= cfg.max_dist_pct)

            oi_i = oi_aligned.iloc[i]
            lb   = cfg.lookback
            oi_prev = oi_aligned.iloc[i - lb] if i >= lb else np.nan
            oi_avail = not np.isnan(oi_i) and not np.isnan(oi_prev)
            oi_chg   = (oi_i / oi_prev - 1) * 100 if oi_avail else np.nan
            oi_move  = oi_avail and abs(oi_chg) >= cfg.oi_min_pct

            cvd_i    = cvd.iloc[i]
            cvd_prev = cvd.iloc[i - lb] if i >= lb else np.nan

            vol_i   = df["volume"].iloc[i]
            volma_i = vol_ma.iloc[i]

            close_mom = df["close"].iloc[i - cfg.mom_len] if i >= cfg.mom_len else np.nan

            f_oi      = cfg.use_oi  and oi_move
            f_vol     = cfg.use_vol and not np.isnan(volma_i) and vol_i > volma_i
            f_cvd_l   = cfg.use_cvd and not np.isnan(cvd_prev) and cvd_i > cvd_prev
            f_cvd_s   = cfg.use_cvd and not np.isnan(cvd_prev) and cvd_i < cvd_prev
            f_trend_l = cfg.use_trend and not np.isnan(htf_i) and close_i > htf_i
            f_trend_s = cfg.use_trend and not np.isnan(htf_i) and close_i < htf_i
            f_mom_l   = cfg.use_mom and not np.isnan(close_mom) and close_i > close_mom
            f_mom_s   = cfg.use_mom and not np.isnan(close_mom) and close_i < close_mom

            max_score = (
                (cfg.w_oi    if cfg.use_oi  and oi_avail else 0) +
                (cfg.w_cvd   if cfg.use_cvd   else 0) +
                (cfg.w_vol   if cfg.use_vol   else 0) +
                (cfg.w_trend if cfg.use_trend else 0) +
                (cfg.w_mom   if cfg.use_mom   else 0)
            )
            raw_l = (cfg.w_oi if f_oi else 0) + (cfg.w_cvd if f_cvd_l else 0) + \
                    (cfg.w_vol if f_vol else 0) + (cfg.w_trend if f_trend_l else 0) + \
                    (cfg.w_mom if f_mom_l else 0)
            raw_s = (cfg.w_oi if f_oi else 0) + (cfg.w_cvd if f_cvd_s else 0) + \
                    (cfg.w_vol if f_vol else 0) + (cfg.w_trend if f_trend_s else 0) + \
                    (cfg.w_mom if f_mom_s else 0)

            stars_l = int(round(raw_l / max_score * 5)) if max_score > 0 else 0
            stars_s = int(round(raw_s / max_score * 5)) if max_score > 0 else 0

            prev_stars_l = results[-1].stars_l if results else 0
            prev_stars_s = results[-1].stars_s if results else 0
            cross_l = stars_l >= cfg.min_stars and prev_stars_l < cfg.min_stars
            cross_s = stars_s >= cfg.min_stars and prev_stars_s < cfg.min_stars

            can_fire_l = last_bar_l is None or (i - last_bar_l) >= cfg.cooldown
            can_fire_s = last_bar_s is None or (i - last_bar_s) >= cfg.cooldown

            sl_long  = swing_low.iloc[i]
            sl_short = swing_high.iloc[i]
            sl_pct_l = (close_i - sl_long)  / close_i * 100 if close_i != 0 and not np.isnan(sl_long)  else np.nan
            sl_pct_s = (sl_short - close_i) / close_i * 100 if close_i != 0 and not np.isnan(sl_short) else np.nan
            sl_ok_l  = not np.isnan(sl_pct_l) and sl_pct_l  <= cfg.max_sl_pct
            sl_ok_s  = not np.isnan(sl_pct_s) and sl_pct_s  <= cfg.max_sl_pct

            long_sig  = cfg.show_long  and cross_l and bull_htf and near_ema and sl_ok_l  and can_fire_l
            short_sig = cfg.show_short and cross_s and bear_htf and near_ema and sl_ok_s  and can_fire_s

            if long_sig:
                last_bar_l = i
            if short_sig:
                last_bar_s = i

            results.append(BarResult(
                ts=df.index[i], close=close_i,
                stars_l=stars_l, stars_s=stars_s,
                htf_ema=htf_i, dist_pct=dist_pct,
                sl_long=sl_long, sl_short=sl_short,
                sl_pct_long=sl_pct_l, sl_pct_short=sl_pct_s,
                long_sig=long_sig, short_sig=short_sig,
            ))

        return results


# ═══════════════════════════════════════════════════════════════════
# 出場計畫：1R 減倉30%保本 → 3R 止損移至1R → 5R 全出
# ═══════════════════════════════════════════════════════════════════

def calc_exit_levels(entry: float, sl: float, direction: str) -> dict[str, float]:
    """計算 R 倍數目標價。"""
    if direction == "LONG":
        risk = entry - sl
        return {
            "risk": risk,
            "sl": sl,
            "r1": entry + risk,
            "r3": entry + risk * 3,
            "r5": entry + risk * 5,
        }
    risk = sl - entry
    return {
        "risk": risk,
        "sl": sl,
        "r1": entry - risk,
        "r3": entry - risk * 3,
        "r5": entry - risk * 5,
    }


@dataclass
class OpenPosition:
    """持倉狀態（回測用）。"""
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    initial_sl: float
    sl: float
    r1: float
    r3: float
    r5: float
    remaining: float = 1.0
    realized_r: float = 0.0
    stage: int = 0
    events: list[str] = field(default_factory=list)


@dataclass
class Trade:
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    sl: float
    r1: float
    r3: float
    r5: float
    exit_price: float = 0.0
    exit_time: Optional[pd.Timestamp] = None
    result: str = ""
    pnl_r: float = 0.0


def _sl_hit(pos: OpenPosition, low: float, high: float) -> bool:
    if pos.direction == "LONG":
        return low <= pos.sl
    return high >= pos.sl


def _tp_hit(pos: OpenPosition, level: float, low: float, high: float) -> bool:
    if pos.direction == "LONG":
        return high >= level
    return low <= level


def _process_bar_exits(
    pos: OpenPosition, high: float, low: float, tp1_reduce: float = 0.30,
) -> Optional[tuple[float, str]]:
    """
    單根 K 棒出場邏輯：
    - 1R：減倉 30%，止損移至進場價保本
    - 3R：不移倉，止損上移至 1R 價位
    - 5R：剩餘倉位全部出場
    """
    while pos.remaining > 1e-9:
        acted = False

        if pos.stage == 0:
            if _sl_hit(pos, low, high):
                pos.realized_r += pos.remaining * (-1.0)
                pos.remaining = 0.0
                return pos.realized_r, "SL"
            if _tp_hit(pos, pos.r1, low, high):
                pos.realized_r += tp1_reduce * 1.0
                pos.remaining = 1.0 - tp1_reduce
                pos.sl = pos.entry_price
                pos.stage = 1
                pos.events.append("1R")
                acted = True
                continue

        if pos.stage == 1:
            if _tp_hit(pos, pos.r3, low, high):
                pos.sl = pos.r1
                pos.stage = 2
                pos.events.append("3R")
                acted = True
                continue
            if _sl_hit(pos, low, high):
                pos.remaining = 0.0
                return pos.realized_r, "+".join(pos.events + ["BE"])

        if pos.stage == 2:
            if _tp_hit(pos, pos.r5, low, high):
                pos.realized_r += pos.remaining * 5.0
                pos.remaining = 0.0
                pos.events.append("5R")
                return pos.realized_r, "+".join(pos.events)
            if _sl_hit(pos, low, high):
                pos.realized_r += pos.remaining * 1.0
                pos.remaining = 0.0
                return pos.realized_r, "+".join(pos.events + ["SL@1R"])

        if not acted:
            break

    return None


def _open_position(direction: str, entry_time: pd.Timestamp, entry: float, sl: float) -> OpenPosition:
    lv = calc_exit_levels(entry, sl, direction)
    return OpenPosition(
        direction=direction,
        entry_time=entry_time,
        entry_price=entry,
        initial_sl=sl,
        sl=sl,
        r1=lv["r1"],
        r3=lv["r3"],
        r5=lv["r5"],
    )


# ═══════════════════════════════════════════════════════════════════
# 方向冷卻：同幣種同方向連續 2 次止損 → 停止該方向，直到反向訊號解鎖
# ═══════════════════════════════════════════════════════════════════

@dataclass
class DirectionCooldownState:
    long_blocked: bool = False
    short_blocked: bool = False
    consecutive_long_sl: int = 0
    consecutive_short_sl: int = 0


def _is_initial_stop_loss(result: str) -> bool:
    """僅計入觸及原始止損（全倉 -1R）。"""
    return result == "SL"


def _unlock_on_opposite_signal(state: DirectionCooldownState, bar: BarResult) -> None:
    """反向訊號觸發時，解除該幣種被冷卻的方向。"""
    if bar.short_sig:
        state.long_blocked = False
        state.consecutive_long_sl = 0
    if bar.long_sig:
        state.short_blocked = False
        state.consecutive_short_sl = 0


def _record_direction_close(
    state: DirectionCooldownState, direction: str, result: str, max_consecutive: int,
) -> None:
    if direction == "LONG":
        if _is_initial_stop_loss(result):
            state.consecutive_long_sl += 1
            if state.consecutive_long_sl >= max_consecutive:
                state.long_blocked = True
        else:
            state.consecutive_long_sl = 0
    else:
        if _is_initial_stop_loss(result):
            state.consecutive_short_sl += 1
            if state.consecutive_short_sl >= max_consecutive:
                state.short_blocked = True
        else:
            state.consecutive_short_sl = 0


def _can_enter_direction(
    state: DirectionCooldownState, direction: str, use_cooldown: bool,
) -> bool:
    if not use_cooldown:
        return True
    if direction == "LONG":
        return not state.long_blocked
    return not state.short_blocked


def _simulate_trades(
    results: list[BarResult],
    df: pd.DataFrame,
    cfg: Config,
) -> tuple[list[Trade], DirectionCooldownState, bool, bool]:
    """
    模擬交易並套用方向冷卻。
    回傳 (trades, 最終冷卻狀態, 最後一根有效多單訊號, 最後一根有效空單訊號)
    """
    trades: list[Trade] = []
    open_pos: Optional[OpenPosition] = None
    cd = DirectionCooldownState()
    eff_long = eff_short = False

    for i, bar in enumerate(results):
        row = df.iloc[i]

        if open_pos is not None:
            closed = _process_bar_exits(open_pos, row["high"], row["low"], cfg.tp1_reduce_pct)
            if closed is not None:
                pnl_r, result = closed
                if cfg.use_direction_cooldown:
                    _record_direction_close(
                        cd, open_pos.direction, result, cfg.max_consecutive_sl_dir,
                    )
                trades.append(Trade(
                    direction=open_pos.direction,
                    entry_time=open_pos.entry_time,
                    entry_price=open_pos.entry_price,
                    sl=open_pos.initial_sl,
                    r1=open_pos.r1,
                    r3=open_pos.r3,
                    r5=open_pos.r5,
                    exit_time=bar.ts,
                    exit_price=row["close"],
                    result=result,
                    pnl_r=pnl_r,
                ))
                open_pos = None

        if cfg.use_direction_cooldown:
            _unlock_on_opposite_signal(cd, bar)

        eff_long = bar.long_sig and _can_enter_direction(cd, "LONG", True)
        eff_short = bar.short_sig and _can_enter_direction(cd, "SHORT", True)

        if open_pos is None:
            if eff_long:
                open_pos = _open_position("LONG", bar.ts, bar.close, bar.sl_long)
            elif eff_short:
                open_pos = _open_position("SHORT", bar.ts, bar.close, bar.sl_short)

    if open_pos is not None:
        trades.append(Trade(
            direction=open_pos.direction,
            entry_time=open_pos.entry_time,
            entry_price=open_pos.entry_price,
            sl=open_pos.initial_sl,
            r1=open_pos.r1,
            r3=open_pos.r3,
            r5=open_pos.r5,
            result="OPEN",
            pnl_r=open_pos.realized_r,
        ))

    return trades, cd, eff_long, eff_short


def run_backtest(results: list[BarResult], df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """
    回測出場規則：
    - 1R：減倉 30%，止損移至進場價（保本）
    - 3R：止損上移至 1R 價位（鎖定 1R 利潤）
    - 5R：剩餘倉位全部出場
    - 方向冷卻：同方向連續 2 次原始止損後暫停，反向訊號解鎖
    """
    trades, _, _, _ = _simulate_trades(results, df, cfg)
    if not trades:
        return pd.DataFrame()
    return pd.DataFrame([vars(t) for t in trades])


def print_backtest_report(tdf: pd.DataFrame, symbol: str = ""):
    if tdf.empty:
        log.info("回測期間無任何訊號。")
        return

    closed = tdf[tdf["result"] != "OPEN"]
    if closed.empty:
        log.info("無已結束交易。")
        return

    total  = len(closed)
    wins   = (closed["pnl_r"] > 0).sum()
    losses = (closed["pnl_r"] < 0).sum()
    win_r  = wins / total * 100 if total else 0
    total_r= closed["pnl_r"].sum()
    avg_w  = closed[closed["pnl_r"] > 0]["pnl_r"].mean() if wins else 0
    avg_l  = closed[closed["pnl_r"] < 0]["pnl_r"].mean() if losses else 0
    pf     = (wins * avg_w) / (losses * abs(avg_l)) if losses and avg_l else float("inf")

    result_counts = closed["result"].value_counts().to_dict()
    title = f"  Hunting Funding 回測報告{f' — {symbol}' if symbol else ''}"

    print("\n" + "="*55)
    print(title)
    print("="*55)
    print(f"  總交易數     : {total}")
    print(f"  勝率         : {win_r:.1f}%  (勝:{wins} 敗:{losses})")
    print(f"  累計 R       : {total_r:.2f}R")
    print(f"  獲利因子(PF) : {pf:.2f}")
    print(f"  平均獲利     : {avg_w:.2f}R")
    print(f"  平均虧損     : {avg_l:.2f}R")
    print(f"  結果分布     : {result_counts}")
    print("="*55)

    long_t  = closed[closed["direction"]=="LONG"]
    short_t = closed[closed["direction"]=="SHORT"]
    if not long_t.empty:
        print(f"  多單 {len(long_t)} 筆  |  勝率 {(long_t['pnl_r']>0).sum()/len(long_t)*100:.1f}%  |  累計 {long_t['pnl_r'].sum():.2f}R")
    if not short_t.empty:
        print(f"  空單 {len(short_t)} 筆  |  勝率 {(short_t['pnl_r']>0).sum()/len(short_t)*100:.1f}%  |  累計 {short_t['pnl_r'].sum():.2f}R")
    print("="*55 + "\n")


# ═══════════════════════════════════════════════════════════════════
# 通知
# ═══════════════════════════════════════════════════════════════════

def send_telegram(token: str, chat_id: str, text: str):
    if not HAS_TELEGRAM or not token or not chat_id:
        return
    try:
        import asyncio
        bot = telegram.Bot(token=token)
        asyncio.run(bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML"))
    except Exception as e:
        log.warning(f"Telegram 發送失敗: {e}")


def format_signal(bar: BarResult, direction: str, symbol: str, interval: str) -> str:
    if direction == "LONG":
        sl     = bar.sl_long
        stars  = star_str(bar.stars_l)
        sl_pct = bar.sl_pct_long
    else:
        sl     = bar.sl_short
        stars  = star_str(bar.stars_s)
        sl_pct = bar.sl_pct_short

    lv = calc_exit_levels(bar.close, sl, direction)
    r1, r3, r5 = lv["r1"], lv["r3"], lv["r5"]
    arrow = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
    return (
        f"<b>Hunting Funding {arrow}</b>\n"
        f"交易對: {symbol} ({interval})\n"
        f"星等: {stars}\n"
        f"OP: {bar.close:.4f}\n"
        f"SL: {sl:.4f}  (-{sl_pct:.2f}%)\n"
        f"1R: {r1:.4f}  減倉30% → 保本\n"
        f"3R: {r3:.4f}  止損移至1R價位\n"
        f"5R: {r5:.4f}  全部出場\n"
        f"時間: {bar.ts.strftime('%Y-%m-%d %H:%M')} UTC"
    )


# ═══════════════════════════════════════════════════════════════════
# 自動下單（Binance USDT-M Futures）
# ═══════════════════════════════════════════════════════════════════

def _round_qty(qty: float, step: float) -> float:
    if step <= 0:
        return round(qty, 3)
    precision = int(round(-math.log10(step)))
    adjusted = math.floor(qty / step) * step
    return round(adjusted, precision)


def _get_lot_step(client: UMFutures, symbol: str) -> tuple[float, int]:
    info = client.exchange_info()
    sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
    step = float(next(f["stepSize"] for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE"))
    precision = int(round(-math.log10(step))) if step > 0 else 3
    return step, precision


def resolve_leverage(client: UMFutures, symbol: str, cfg: Config) -> int:
    """0 表示使用該交易對允許的最大槓桿。"""
    if cfg.leverage > 0:
        return cfg.leverage
    try:
        brackets = client.leverage_brackets(symbol=symbol)
        if brackets:
            return max(int(b["initialLeverage"]) for b in brackets[0]["brackets"])
    except Exception as e:
        log.warning(f"取得 {symbol} 最大槓桿失敗: {e}")
    return 20


def place_order(cfg: Config, direction: str, bar: BarResult, symbol: str):
    if not cfg.auto_trade:
        return
    if not HAS_BINANCE_CLIENT:
        log.error("請安裝 binance-futures-connector: pip install binance-futures-connector")
        return

    client = UMFutures(
        key=cfg.api_key, secret=cfg.api_secret,
        base_url="https://testnet.binancefuture.com" if cfg.testnet else None
    )

    side = "BUY" if direction == "LONG" else "SELL"
    exit_side = "SELL" if direction == "LONG" else "BUY"
    sl = bar.sl_long if direction == "LONG" else bar.sl_short
    lv = calc_exit_levels(bar.close, sl, direction)

    lev = resolve_leverage(client, symbol, cfg)
    try:
        client.change_leverage(symbol=symbol, leverage=lev)
        log.info(f"{symbol} 槓桿設為 {lev}x")
    except Exception as e:
        log.warning(f"設定槓桿失敗: {e}")

    margin = cfg.margin_per_trade
    qty_raw = margin * lev / bar.close
    reduce_pct = cfg.tp1_reduce_pct
    try:
        step, _ = _get_lot_step(client, symbol)
        qty = _round_qty(qty_raw, step)
        qty_tp1 = _round_qty(qty * reduce_pct, step)
        qty_rest = _round_qty(qty - qty_tp1, step)
    except Exception:
        qty = round(qty_raw, 3)
        qty_tp1 = round(qty * reduce_pct, 3)
        qty_rest = round(qty - qty_tp1, 3)

    if qty <= 0:
        log.error(f"{symbol} 計算數量為 0，略過下單")
        return

    try:
        client.new_order(symbol=symbol, side=side, type="MARKET", quantity=qty)
        log.info(f"已下單 {side} {qty} {symbol} @ market")

        # 初始止損（全倉）
        client.new_order(
            symbol=symbol, side=exit_side, type="STOP_MARKET",
            stopPrice=round(lv["sl"], 8), quantity=qty, reduceOnly=True,
        )
        log.info(f"初始止損 @ {lv['sl']:.4f}")

        # 1R 減倉 30%
        if qty_tp1 > 0:
            client.new_order(
                symbol=symbol, side=exit_side, type="TAKE_PROFIT_MARKET",
                stopPrice=round(lv["r1"], 8), quantity=qty_tp1, reduceOnly=True,
            )
            log.info(f"1R 減倉{reduce_pct*100:.0f}% @ {lv['r1']:.4f}  qty={qty_tp1}")

        # 5R 全出剩餘倉位
        if qty_rest > 0:
            client.new_order(
                symbol=symbol, side=exit_side, type="TAKE_PROFIT_MARKET",
                stopPrice=round(lv["r5"], 8), quantity=qty_rest, reduceOnly=True,
            )
            log.info(f"5R 全出 @ {lv['r5']:.4f}  qty={qty_rest}")

        log.info(
            f"保證金 {margin:.2f}U ({cfg.position_pct}% of {cfg.total_capital}U)  "
            f"出場計畫：1R減倉{reduce_pct*100:.0f}%後止損移至 {bar.close:.4f} 保本；"
            f"3R觸及後止損移至1R價 {lv['r1']:.4f}；5R全出"
        )
    except Exception as e:
        log.error(f"下單失敗: {e}")


def _kline_limit(cfg: Config) -> int:
    return min(max(500, cfg.htf_ema_len * 2 + cfg.sl_swing + 10), 1500)


def scan_symbol(cfg: Config, symbol: str, engine: HuntingEngine) -> tuple[pd.DataFrame, list[BarResult]]:
    """抓取單一交易對資料並計算訊號。"""
    df   = fetch_klines(symbol, cfg.interval, limit=_kline_limit(cfg))
    oi_s = fetch_open_interest_history(symbol, cfg.interval, limit=500)
    return df, engine.compute(df, oi_s)


# ═══════════════════════════════════════════════════════════════════
# 即時掃描主迴圈
# ═══════════════════════════════════════════════════════════════════

def live_scan(cfg: Config):
    engine = HuntingEngine(cfg)
    symbols = resolve_symbols(cfg)
    log.info(f"🚀 即時掃描啟動  Top {cfg.top_n} 成交量 ({len(symbols)} 個)  {cfg.interval}")

    while True:
        try:
            for symbol in symbols:
                try:
                    df, results = scan_symbol(cfg, symbol, engine)
                except Exception as e:
                    log.warning(f"{symbol} 資料取得失敗: {e}")
                    continue

                if not results:
                    continue

                bar = results[-1]
                _, cd, eff_long, eff_short = _simulate_trades(results, df, cfg)

                if len(results) >= 2:
                    _, _, prev_eff_long, prev_eff_short = _simulate_trades(
                        results[:-1], df.iloc[:-1], cfg,
                    )
                else:
                    prev_eff_long, prev_eff_short = False, False

                if eff_long and not prev_eff_long:
                    msg = format_signal(bar, "LONG", symbol, cfg.interval)
                    print("\n" + msg.replace("<b>","").replace("</b>",""))
                    send_telegram(cfg.tg_token, cfg.tg_chat_id, msg)
                    if cfg.auto_trade:
                        place_order(cfg, "LONG", bar, symbol)
                elif bar.long_sig and cd.long_blocked:
                    log.debug(f"{symbol} 做多訊號因方向冷卻略過（待空單訊號解鎖）")

                if eff_short and not prev_eff_short:
                    msg = format_signal(bar, "SHORT", symbol, cfg.interval)
                    print("\n" + msg.replace("<b>","").replace("</b>",""))
                    send_telegram(cfg.tg_token, cfg.tg_chat_id, msg)
                    if cfg.auto_trade:
                        place_order(cfg, "SHORT", bar, symbol)
                elif bar.short_sig and cd.short_blocked:
                    log.debug(f"{symbol} 做空訊號因方向冷卻略過（待多單訊號解鎖）")

                if len(symbols) == 1:
                    ts_str   = bar.ts.strftime("%H:%M")
                    ema_dist = f"{bar.dist_pct:.2f}%" if not np.isnan(bar.dist_pct) else "N/A"
                    print(
                        f"\r[{ts_str}] {symbol} {bar.close:.2f}  "
                        f"多:{bar.stars_l}★ 空:{bar.stars_s}★  "
                        f"EMA距離:{ema_dist}",
                        end="", flush=True
                    )

        except KeyboardInterrupt:
            log.info("\n掃描已停止。")
            break
        except Exception as e:
            log.error(f"掃描錯誤: {e}")

        time.sleep(30)


# ═══════════════════════════════════════════════════════════════════
# 回測模式
# ═══════════════════════════════════════════════════════════════════

def run_backtest_mode(cfg: Config):
    symbols = resolve_symbols(cfg)
    engine  = HuntingEngine(cfg)
    all_trades: list[pd.DataFrame] = []

    log.info(f"📊 回測模式  Top {cfg.top_n} 成交量 ({len(symbols)} 個)  {cfg.interval}  最近 {cfg.backtest_limit} 根")

    for symbol in symbols:
        try:
            df   = fetch_klines(symbol, cfg.interval, limit=cfg.backtest_limit)
            oi_s = fetch_open_interest_history(symbol, cfg.interval, limit=500)
            results = engine.compute(df, oi_s)
        except Exception as e:
            log.warning(f"{symbol} 回測跳過: {e}")
            continue

        sigs = [(r, "LONG") for r in results if r.long_sig] + \
               [(r, "SHORT") for r in results if r.short_sig]
        sigs.sort(key=lambda x: x[0].ts)

        if sigs:
            print(f"\n── {symbol}：共 {len(sigs)} 個訊號 ──")
            for bar, d in sigs:
                sl  = bar.sl_long  if d == "LONG"  else bar.sl_short
                pct = bar.sl_pct_long if d == "LONG" else bar.sl_pct_short
                print(f"  {bar.ts.strftime('%Y-%m-%d %H:%M')}  {d:5s}  "
                      f"OP={bar.close:.4f}  SL={sl:.4f} (-{pct:.2f}%)  "
                      f"星:{bar.stars_l if d=='LONG' else bar.stars_s}")

        tdf = run_backtest(results, df, cfg)
        if not tdf.empty:
            tdf["symbol"] = symbol
            all_trades.append(tdf)
            if len(symbols) == 1:
                print_backtest_report(tdf, symbol)

    if len(symbols) > 1 and all_trades:
        combined = pd.concat(all_trades, ignore_index=True)
        print_backtest_report(combined, f"Top {cfg.top_n} USDT.P")
        out = f"backtest_TOP{cfg.top_n}_{cfg.interval}.csv"
        combined.to_csv(out, index=False)
        log.info(f"回測結果已儲存：{out}")
    elif len(symbols) == 1 and all_trades:
        out = f"backtest_{symbols[0]}_{cfg.interval}.csv"
        all_trades[0].to_csv(out, index=False)
        log.info(f"回測結果已儲存：{out}")
    elif not all_trades:
        log.info("回測期間無任何訊號。")


# ═══════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(description="Hunting Funding — Python 移植版")
    p.add_argument("--mode",     choices=["live","backtest"], default="backtest",
                   help="live=即時掃描  backtest=回測分析")
    p.add_argument("--symbol",   default=SYMBOL_TOP,
                   help=f"交易對，預設 {SYMBOL_TOP}=每日成交量 Top N USDT 永續 (USDT.P)")
    p.add_argument("--top-n",    type=int, default=100,
                   help="成交量榜單數量（symbol=TOP 時生效）")
    p.add_argument("--interval", default="5m",
                   choices=["1m","3m","5m","15m","30m","1h","2h","4h","6h","12h","1d"])
    p.add_argument("--min-stars",     type=int,   default=5)
    p.add_argument("--lookback",      type=int,   default=4)
    p.add_argument("--cooldown",      type=int,   default=24)
    p.add_argument("--htf-ema-len",   type=int,   default=150)
    p.add_argument("--max-dist-pct",  type=float, default=5.0)
    p.add_argument("--sl-swing",      type=int,   default=24)
    p.add_argument("--max-sl-pct",    type=float, default=5.0)
    p.add_argument("--max-consecutive-sl-dir", type=int, default=2,
                   help="同方向連續止損 N 次後冷卻")
    p.add_argument("--no-direction-cooldown", dest="use_direction_cooldown",
                   action="store_false", default=True,
                   help="關閉方向冷卻（連續止損停單）")
    p.add_argument("--backtest-limit",type=int,   default=1000)
    p.add_argument("--auto-trade",    action="store_true", default=False)
    p.add_argument("--testnet",       action="store_true", default=True)
    p.add_argument("--total-capital", type=float, default=100.0,
                   help="總資金 USDT")
    p.add_argument("--position-pct",  type=float, default=2.0,
                   help="每單保證金佔總資金 %")
    p.add_argument("--leverage",      type=int,   default=0,
                   help="槓桿倍數，0=使用交易對最大槓桿")
    p.add_argument("--api-key",    default=os.getenv("BINANCE_API_KEY",""))
    p.add_argument("--api-secret", default=os.getenv("BINANCE_API_SECRET",""))
    p.add_argument("--tg-token",   default=os.getenv("TG_TOKEN",""))
    p.add_argument("--tg-chat-id", default=os.getenv("TG_CHAT_ID",""))
    return p.parse_args()


def main():
    args = parse_args()
    cfg  = Config(
        symbol           = args.symbol,
        top_n            = args.top_n,
        interval         = args.interval,
        min_stars        = args.min_stars,
        lookback         = args.lookback,
        cooldown         = args.cooldown,
        htf_ema_len      = args.htf_ema_len,
        max_dist_pct     = args.max_dist_pct,
        sl_swing         = args.sl_swing,
        max_sl_pct       = args.max_sl_pct,
        use_direction_cooldown = args.use_direction_cooldown,
        max_consecutive_sl_dir   = args.max_consecutive_sl_dir,
        backtest_limit   = args.backtest_limit,
        auto_trade       = args.auto_trade,
        testnet          = args.testnet,
        total_capital    = args.total_capital,
        position_pct     = args.position_pct,
        leverage         = args.leverage,
        api_key          = args.api_key,
        api_secret       = args.api_secret,
        tg_token         = args.tg_token,
        tg_chat_id       = args.tg_chat_id,
    )

    if args.mode == "live":
        live_scan(cfg)
    else:
        run_backtest_mode(cfg)


if __name__ == "__main__":
    main()
