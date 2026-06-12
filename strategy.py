# 策略模組（互不覆蓋設定，僅 STRATEGY 切換執行哪一套）：
#   EMA:     config.TIMEFRAME、RR_*、REDUCE_AT_* → strategies/ema_trend_cross.py
#   唐奇安: config.DONCHIAN_*、DONCHIAN_TIMEFRAME → strategies/donchian_multi_tp.py
# config.STRATEGY = "ema" | "donchian" | "hunting_funding"
