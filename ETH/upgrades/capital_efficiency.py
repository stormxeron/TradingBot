"""
Capital Efficiency Upgrade - Prevents idle capital, maximizes utilization
"""
import logging
from typing import Dict, Any, Optional
from decimal import Decimal

class CapitalEfficiency:
    """Monitors and optimizes capital utilization"""
    
    def __init__(self, config: Dict[str, Any], volume_precision: Decimal, price_precision: Decimal):
        self.config = config
        self.volume_precision = volume_precision
        self.price_precision = price_precision
        
        # 🔥 UPDATED DEFAULTS
        self.max_idle_percent = Decimal(str(config.get('MAX_IDLE_CAPITAL_PERCENT', 0.12)))  # Was 0.05
        self.rebalance_trigger = Decimal(str(config.get('REBALANCE_TRIGGER_PERCENT', 0.20)))  # Was 0.10
        self.min_utilization = Decimal(str(config.get('MIN_CAPITAL_UTILIZATION', 0.80)))  # Was 0.85
        
        # State
        self.idle_capital_history = []
        self.rebalance_count = 0
        
        logging.critical(f"💰 Capital Efficiency initialized:")
        logging.critical(f"   Rebalance trigger: >{self.rebalance_trigger*100:.0f}% idle")
        logging.critical(f"   Target idle: <{self.max_idle_percent*100:.0f}%")
        logging.critical(f"   Min utilization: >{self.min_utilization*100:.0f}%")
    def analyze(self, fresh_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze capital utilization and identify inefficiencies
        """
        try:
            # USE SHARED IDLE PERCENT IF AVAILABLE
            idle_percent = fresh_data.get('idle_percent')
            
            if idle_percent is None:
                # Fallback to calculation if not shared
                base_balance = fresh_data.get('base_balance', Decimal('0'))
                quote_balance = fresh_data.get('quote_balance', Decimal('0'))
                managed_orders = fresh_data.get('managed_orders', {})
                current_price = fresh_data.get('current_price', Decimal('0'))
                
                if current_price == Decimal('0'):
                    logging.warning("No current price for capital efficiency analysis")
                    return {'action_required': False, 'error': 'No price data'}
                
                # Calculate idle capital
                idle_percent = self._calculate_idle_capital(
                    base_balance, quote_balance, managed_orders, current_price
                )
            else:
                # Use shared idle_percent
                idle_percent = Decimal(str(idle_percent))
                utilization = Decimal('1') - idle_percent
                
                #logging.info(f"💰 USING SHARED IDLE DATA: {idle_percent*100:.1f}% idle, {utilization*100:.1f}% utilized")
            
            # Store history
            self.idle_capital_history.append({
                'timestamp': fresh_data.get('timestamp', 0),
                'idle_percent': float(idle_percent),
                'current_price': fresh_data.get('current_price', 0)
            })
            
            # Trim history
            if len(self.idle_capital_history) > 100:
                self.idle_capital_history = self.idle_capital_history[-100:]
            
            # Make decision
            decision = self._make_rebalance_decision(
                idle_percent, Decimal('1') - idle_percent, fresh_data.get('managed_orders', {})
            )
            
            # Add actual calculations to decision for debugging
            decision['actual_utilization'] = float(Decimal('1') - idle_percent)
            decision['actual_idle_percent'] = float(idle_percent)
            
            return decision
            
        except Exception as e:
            logging.error(f"🔥 Capital Efficiency analysis failed: {e}")
            return {'action_required': False, 'error': str(e)}
    
    def _calculate_utilization(self, base_balance, quote_balance, managed_orders, current_price):
        """Calculate what percentage of capital is deployed in orders"""
        
        logging.critical("🔍 DETAILED UTILIZATION CALCULATION:")
        
        total_locked_value = Decimal('0')
    
        for order in managed_orders.values():
            if order['side'] == 'buy':
                # Buy: Locked at order price
                total_locked_value += order['volume'] * order['price']
            else:  # sell
                # Sell: Should be valued at ORDER PRICE, not current!
                total_locked_value += order['volume'] * order['price']  # NOT current_price!
        
        total_portfolio = (base_balance * current_price) + quote_balance
        
        if total_portfolio == Decimal('0'):
            return Decimal('0')
        
        utilization = total_locked_value / total_portfolio
        
        logging.critical(f"🔍 REAL UTILIZATION (using order prices):")
        logging.critical(f"   Locked at order prices: ${total_locked_value:.2f}")
        logging.critical(f"   Portfolio: ${total_portfolio:.2f}")
        logging.critical(f"   Real utilization: {utilization*100:.1f}%")
        
        return utilization
    
    def _calculate_idle_capital(self,
                          base_balance: Decimal,
                          quote_balance: Decimal,
                          managed_orders: Dict,
                          current_price: Decimal,
                          utilization: Optional[Decimal] = None) -> Decimal:  # ADD optional parameter
        """Calculate percentage of capital sitting idle"""
        
        if utilization is not None:
            # Use pre-calculated utilization
            idle_percent = Decimal('1') - utilization
            
            logging.debug(f"📊 Using pre-calculated utilization: {utilization*100:.1f}% → idle: {idle_percent*100:.1f}%")
            return idle_percent
        
        # Fallback to calculation
        utilization = self._calculate_utilization(
            base_balance, quote_balance, managed_orders, current_price
        )
        
        idle_percent = Decimal('1') - utilization
        
        # COMMENT OUT or reduce logging here
        # logging.info(f"💰 IDLE CALCULATION:")
        # logging.info(f"   Utilization: {utilization*100:.1f}%")
        # logging.info(f"   Idle: {idle_percent*100:.1f}%")
        
        # Just log at debug level
        logging.debug(f"💰 Calculated idle: {idle_percent*100:.1f}%")
        
        return idle_percent
    
    def _make_rebalance_decision(self,
                                idle_percent: Decimal,
                                utilization: Decimal,
                                managed_orders: Dict) -> Dict[str, Any]:
        """Determine if rebalancing is needed - WITH REALISTIC THRESHOLDS"""
        
        # 🔥 UPDATED THRESHOLDS
        HIGH_IDLE_THRESHOLD = Decimal('0.20')  # Was 0.10 - Only reset if >20% idle
        CRITICAL_IDLE_THRESHOLD = Decimal('0.25')  # Was 0.20 - Extreme case
        
        if idle_percent > CRITICAL_IDLE_THRESHOLD:
            self.rebalance_count += 1
            return {
                'action_required': True,
                'action': 'CRITICAL: Force rebalance - extremely high idle capital',
                'idle_percent': float(idle_percent),
                'utilization': float(utilization),
                'recommendation': 'Immediate grid reset required',
                'urgency': 'critical',
                'recommended_leverage': Decimal('1.3')  # Suggest higher leverage
            }
        elif idle_percent > HIGH_IDLE_THRESHOLD:
            self.rebalance_count += 1
            return {
                'action_required': True,
                'action': 'Optimize capital - high idle percentage',
                'idle_percent': float(idle_percent),
                'utilization': float(utilization),
                'recommendation': 'Increase order sizes or place additional orders',
                'urgency': 'high',
                'recommended_leverage': Decimal('1.2')  # Moderate leverage increase
            }
        elif utilization < self.min_utilization:
            # This is the OLD logic that's probably triggering
            # Change it to be less sensitive
            if utilization < Decimal('0.70'):  # Only if REALLY low utilization
                return {
                    'action_required': True,
                    'action': 'Review capital allocation - low utilization',
                    'idle_percent': float(idle_percent),
                    'utilization': float(utilization),
                    'recommendation': 'Review grid spacing or order distribution',
                    'urgency': 'medium'
                }
            else:
                # 70-85% utilization is FINE, no action needed
                return {
                    'action_required': False,
                    'action': 'Capital efficiency acceptable',
                    'idle_percent': float(idle_percent),
                    'utilization': float(utilization),
                    'urgency': 'low'
                }
        else:
            return {
                'action_required': False,
                'action': 'Capital efficiency OK',
                'idle_percent': float(idle_percent),
                'utilization': float(utilization),
                'urgency': 'low'
            }