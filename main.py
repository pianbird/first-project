"""
MagicTrader - Always-on Headless Daemon Main Entry Point (main.py)
"""
from trading_bot.main import run_daemon, is_market_open, is_market_hours, setup_logger

if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    run_daemon()
