"""Каталог scalp/momentum-стратегий для research-фазы."""
from research.strategies.orb_scalper import generate_signals as orb_signals
from research.strategies.ema_cross_volume import generate_signals as ema_signals
from research.strategies.bb_squeeze_breakout import generate_signals as bb_signals
from research.strategies.vwap_reversion import generate_signals as vwap_signals
from research.strategies.donchian_breakout import generate_signals as donchian_signals

STRATEGIES = {
    "ORB_5m": {"fn": orb_signals, "tf": "5m", "default_params": {"range_bars": 12, "vol_mult": 1.5}},
    "EMA+Vol_5m": {"fn": ema_signals, "tf": "5m", "default_params": {"fast": 9, "slow": 21, "vol_mult": 1.3}},
    "BBSqueeze_15m": {"fn": bb_signals, "tf": "15m", "default_params": {"bb_window": 20, "kc_window": 20, "kc_mult": 1.5}},
    "VWAPRev_5m": {"fn": vwap_signals, "tf": "5m", "default_params": {"deviation_pct": 0.5, "rsi_filter": True}},
    "Donchian_15m": {"fn": donchian_signals, "tf": "15m", "default_params": {"lookback": 20, "adx_min": 22}},
}
