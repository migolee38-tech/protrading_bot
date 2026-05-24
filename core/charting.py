"""Plotly K 線 + 策略訊號標記。"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.strategy_registry import scan_signals_for


def build_candlestick_chart(
    df: pd.DataFrame,
    strategy_id: str,
    prepared_df: pd.DataFrame | None = None,
    title: str = "",
    height: int = 620,
) -> go.Figure:
    prep = prepared_df
    if prep is None:
        from core.strategy_registry import get_strategy

        prep = get_strategy(strategy_id).prepare_df(df)

    signals = scan_signals_for(strategy_id, prep)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.75, 0.25],
    )

    fig.add_trace(
        go.Candlestick(
            x=prep["datetime"] if "datetime" in prep.columns else prep.index,
            open=prep["open"],
            high=prep["high"],
            low=prep["low"],
            close=prep["close"],
            name="K線",
        ),
        row=1,
        col=1,
    )

    xs = prep["datetime"] if "datetime" in prep.columns else prep.index
    long_x, long_y, short_x, short_y = [], [], [], []
    for sig in signals:
        idx = sig.bar_index
        if idx >= len(xs):
            continue
        x = xs.iloc[idx] if hasattr(xs, "iloc") else xs[idx]
        y = float(prep.iloc[idx]["low"] if sig.side == "long" else prep.iloc[idx]["high"])
        if sig.side == "long":
            long_x.append(x)
            long_y.append(y)
        else:
            short_x.append(x)
            short_y.append(y)

    if long_x:
        fig.add_trace(
            go.Scatter(
                x=long_x,
                y=long_y,
                mode="markers",
                name="做多訊號",
                marker=dict(symbol="triangle-up", size=14, color="#26a69a"),
            ),
            row=1,
            col=1,
        )
    if short_x:
        fig.add_trace(
            go.Scatter(
                x=short_x,
                y=short_y,
                mode="markers",
                name="做空訊號",
                marker=dict(symbol="triangle-down", size=14, color="#ef5350"),
            ),
            row=1,
            col=1,
        )

    vol_x = prep["datetime"] if "datetime" in prep.columns else prep.index
    colors = [
        "#26a69a" if c >= o else "#ef5350"
        for c, o in zip(prep["close"], prep["open"])
    ]
    fig.add_trace(
        go.Bar(x=vol_x, y=prep["volume"], name="成交量", marker_color=colors, opacity=0.5),
        row=2,
        col=1,
    )

    fig.update_layout(
        title=title or f"{strategy_id.upper()} 訊號圖",
        xaxis_rangeslider_visible=False,
        height=height,
        template="plotly_dark",
        legend=dict(orientation="h", y=1.02),
    )
    fig.update_xaxes(type="date")
    return fig
