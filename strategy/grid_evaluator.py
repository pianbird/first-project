"""
Pure grid strategy signal evaluation logic for MagicTrader.
Evaluates current price and position state against grid config, producing actionable signals
without executing external order side-effects.
"""
from dataclasses import dataclass
from typing import Dict, Any, Optional
from config.constants import SAFE_STOP_DROP_THRESHOLD

@dataclass
class TradingSignal:
    action: str  # "INVALID_CONFIG", "SAFE_STOP_DROP", "CLEAR_SELL", "HARD_STOP_LOSS", "GRID_PROFIT_SELL", "GRID_BUY", "HOLD"
    stock_code: str
    stock_name: str
    cur_price: int
    qty: int = 0
    price: int = 0
    order_type: str = ""  # "BUY" or "SELL"
    strategy_id: str = ""
    cycle_id: int = 0
    message: str = ""
    current_step: int = 0
    target_qty: int = 0

class GridEvaluator:
    """
    Pure calculator for grid trading decisions.
    Preserves 100% of original decision tree logic and parameters.
    """
    @staticmethod
    def evaluate(
        code: str,
        stock: Dict[str, Any],
        mdata: Dict[str, Any],
        actual_qty: int
    ) -> TradingSignal:
        cur_price = mdata.get("current_price", 0)
        stock_name = stock.get("name", code)
        clear_price = stock.get("clear_price", 0)
        grid = stock.get("grid", {})
        steps = grid.get("steps", [])
        current_step = stock.get("current_step", 0)

        # 1. 주가 급변 감지 (Overnight Drop > 15%)
        prev_close = mdata.get("prev_close", cur_price)
        if prev_close > 0 and cur_price <= prev_close * SAFE_STOP_DROP_THRESHOLD:
            return TradingSignal(
                action="SAFE_STOP_DROP",
                stock_code=code,
                stock_name=stock_name,
                cur_price=cur_price,
                message=f"[{stock_name}({code})] 주가 급변 감지 (전일비 {mdata.get('change_rate', 0):.2f}%)"
            )

        if clear_price <= 0:
            return TradingSignal(
                action="INVALID_CONFIG",
                stock_code=code,
                stock_name=stock_name,
                cur_price=cur_price,
                message=f"[{stock_name}({code})] clear_price 미설정/비정상({clear_price}) - 판정 보류"
            )

        # 2. 전량 최종 청산 (Clear Price 도달 시)
        if cur_price >= clear_price and actual_qty > 0:
            return TradingSignal(
                action="CLEAR_SELL",
                stock_code=code,
                stock_name=stock_name,
                cur_price=cur_price,
                qty=actual_qty,
                price=cur_price,
                order_type="SELL",
                strategy_id="GRID_CLEAR",
                cycle_id=current_step,
                message=f"🎉 [최종 전량 청산 접수] [{stock_name}] 청산가({clear_price:,}원) 도달! {actual_qty}주 매도 전송",
                current_step=current_step
            )

        # 2.5. 하드 손절가(Hard Stop-Loss) 이탈 감지 및 강제 시장가 전량 청산
        hsl_enabled = stock.get("hard_stop_loss_enabled", False)
        hsl_price = stock.get("hard_stop_loss_price", 0)
        hsl_halted = stock.get("stop_loss_halted", False)

        if hsl_enabled and hsl_price > 0 and cur_price <= hsl_price and actual_qty > 0 and not hsl_halted:
            return TradingSignal(
                action="HARD_STOP_LOSS",
                stock_code=code,
                stock_name=stock_name,
                cur_price=cur_price,
                qty=actual_qty,
                price=cur_price,
                order_type="SELL",
                strategy_id="HARD_STOP_LOSS",
                cycle_id=0,
                message=f"🛑 [하드 손절 발동] [{stock_name}({code})] 현재가({cur_price:,}원) <= 손절가({hsl_price:,}원)! {actual_qty}주 시장가 전량 손절 전송",
                current_step=0
            )

        # 3. 현재 주가 기반 목표 차수 탐색
        start_step = grid.get("start_step", 1)
        start_step_idx = max(0, min(start_step - 1, len(steps) - 1)) if steps else 0
        start_step_obj = steps[start_step_idx] if steps else {"price": 0, "target_total_qty": 0}
        start_step_price = start_step_obj.get("price", 0)
        start_target_qty = start_step_obj.get("target_total_qty", 0)

        evaluated_step = 0
        target_qty = 0
        for s in steps:
            if cur_price <= s["price"]:
                evaluated_step = s["step"]
                target_qty = s["target_total_qty"]

        if steps and cur_price >= start_step_price and cur_price < clear_price and actual_qty > 0:
            evaluated_step = start_step
            target_qty = start_target_qty

        # 4. 수량 자동 보정 공식 (목표 수량 - 실제 보유 수량)
        needed_qty = target_qty - actual_qty

        if needed_qty < 0:
            sell_qty = abs(needed_qty)
            return TradingSignal(
                action="GRID_PROFIT_SELL",
                stock_code=code,
                stock_name=stock_name,
                cur_price=cur_price,
                qty=sell_qty,
                price=cur_price,
                order_type="SELL",
                strategy_id="GRID_PROFIT",
                cycle_id=evaluated_step,
                message=f"📈 [차수 익절 매도] [{stock_name}] {evaluated_step}차 도달, {sell_qty}주 매도 전송",
                current_step=evaluated_step,
                target_qty=target_qty
            )
        elif needed_qty > 0:
            return TradingSignal(
                action="GRID_BUY",
                stock_code=code,
                stock_name=stock_name,
                cur_price=cur_price,
                qty=needed_qty,
                price=cur_price,
                order_type="BUY",
                strategy_id="GRID_BUY",
                cycle_id=evaluated_step,
                message=f"📉 [차수 분할 매수] [{stock_name}] {evaluated_step}차 진입, {needed_qty}주 추가 매수 전송",
                current_step=evaluated_step,
                target_qty=target_qty
            )

        return TradingSignal(
            action="HOLD",
            stock_code=code,
            stock_name=stock_name,
            cur_price=cur_price,
            current_step=evaluated_step,
            target_qty=target_qty
        )
