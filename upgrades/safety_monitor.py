"""
Safety Monitor - Emergency shutdown and risk management
"""
import logging
from typing import Dict, Any
from decimal import Decimal

class SafetyMonitor:
    """Monitors for dangerous conditions and triggers emergency shutdown"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        # Settings
        self.max_daily_drawdown = Decimal(str(config.get('MAX_DAILY_DRAWDOWN', 0.20)))
        self.max_position_imbalance = Decimal(str(config.get('MAX_POSITION_IMBALANCE', 0.70)))
        self.emergency_enabled = config.get('EMERGENCY_SHUTDOWN_ENABLED', True)
        
        # State
        self.starting_balance = None
        self.worst_drawdown = Decimal('0')
        self.emergency_count = 0
        
        logging.info("🛡️ Safety Monitor initialized")
    
    def check(self, fresh_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check for safety violations
        Returns: Dict with safety status
        """
        if not self.emergency_enabled:
            return {'trading_allowed': True}
        
        try:
            violations = []
            
            # Check 1: Position imbalance
            imbalance = self._check_position_imbalance(fresh_data)
            if imbalance:
                violations.append(imbalance)
            
            # Check 2: Drawdown limit
            drawdown = self._check_drawdown(fresh_data)
            if drawdown:
                violations.append(drawdown)
            
            # Check 3: Rapid price movement
            rapid_move = self._check_rapid_movement(fresh_data)
            if rapid_move:
                violations.append(rapid_move)
            
            if violations:
                self.emergency_count += 1
                logging.critical(f"🚨 SAFETY VIOLATIONS: {violations}")
                
                if self.emergency_count >= 3:
                    return {
                        'trading_allowed': False,
                        'reason': f'Multiple safety violations: {violations}',
                        'emergency_action': 'SHUTDOWN',
                        'violations': violations
                    }
                else:
                    return {
                        'trading_allowed': True,  # Still allow but warn
                        'warning': f'Safety concerns: {violations}',
                        'violations': violations
                    }
            
            return {'trading_allowed': True}
            
        except Exception as e:
            logging.error(f"🔥 Safety check failed: {e}")
            # On error, be conservative
            return {'trading_allowed': False, 'reason': f'Safety system error: {e}'}
    
    def _check_position_imbalance(self, fresh_data: Dict[str, Any]) -> str:
        """Check if buy/sell positions are too imbalanced"""
        managed_orders = fresh_data.get('managed_orders', {})
        
        buy_count = len([o for o in managed_orders.values() 
                         if o.get('side') == 'buy'])
        sell_count = len([o for o in managed_orders.values() 
                          if o.get('side') == 'sell'])
        
        total = buy_count + sell_count
        if total == 0:
            return ""
        
        buy_ratio = Decimal(str(buy_count)) / Decimal(str(total))
        
        if buy_ratio > self.max_position_imbalance:
            return f"Buy position too large: {buy_ratio*100:.1f}%"
        elif buy_ratio < (Decimal('1') - self.max_position_imbalance):
            return f"Sell position too large: {(1-buy_ratio)*100:.1f}%"
        
        return ""
    
    def _check_drawdown(self, fresh_data: Dict[str, Any]) -> str:
        """Check if drawdown exceeds limits"""
        # This would require tracking P&L over time
        # Placeholder implementation
        return ""
    
    def _check_rapid_movement(self, fresh_data: Dict[str, Any]) -> str:
        """Check for rapid price movements"""
        # Would analyze price history for sudden spikes/drops
        return ""
    
    def reset(self):
        """Reset safety monitor state"""
        self.emergency_count = 0
        logging.info("🔄 Safety Monitor reset")