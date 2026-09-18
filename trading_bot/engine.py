"""
PythonAnywhere Always-on Task 전용 자동매매 엔진 데몬 호환용 진입점 (trading_bot/engine.py)
"""
import os
import sys

base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if base_dir not in sys.path:
    sys.path.append(base_dir)

try:
    from web_app.backend.engine import WebTradingEngine
except ImportError:
    try:
        from engine import WebTradingEngine
    except ImportError:
        WebTradingEngine = None

try:
    from trading_bot_runner import run_trading_bot_daemon
except ImportError:
    run_trading_bot_daemon = None

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MagicTrader Always-on Task Daemon")
    parser.add_argument("--once", action="store_true", help="Run 1 cycle and exit (for verification test)")
    args = parser.parse_args()

    if run_trading_bot_daemon:
        run_trading_bot_daemon(run_once=args.once)
