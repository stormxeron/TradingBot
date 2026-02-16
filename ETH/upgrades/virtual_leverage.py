"""
Virtual Leverage Upgrade - Amplifies capital efficiency without borrowing
"""
import logging
from typing import Dict, Any, Tuple
from decimal import Decimal

class VirtualLeverage:
    """Applies virtual leverage to order volumes"""
    
    def __init__(self, 
                 config: Dict[str, Any],
                 volume_precision: Decimal,
                 price_precision: Decimal,
                 base_asset: str,
                 quote_asset: str):
        self.config = config
        self.volume_precision = volume_precision
        self.price_precision = price_precision
        self.base_asset = base_asset
        self.quote_asset = quote_asset
        
        # Settings
        self.multiplier = Decimal(str(config.get('VIRTUAL_LEVERAGE_MULTIPLIER', 2.0)))
        self.max_leverage = Decimal(str(config.get('MAX_VIRTUAL_LEVERAGE', 3.0)))
        self.min_leverage = Decimal(str(config.get('MIN_VIRTUAL_LEVERAGE', 1.0)))
        
        # State
        self.current_multiplier = self.multiplier
        self.is_active = True
        self._last_data = None
        self.performance_stats = {
            'cycles_processed': 0,
            'leverage_applied': 0,
            'profit_impact': Decimal('0')
        }
        
        logging.info(f"💰 Virtual Leverage initialized: {self.multiplier}x")
    
    def store_data(self, data: Dict[str, Any]):
        """Store data for analysis in next cycle"""
        self._last_data = data
    
    def analyze(self, fresh_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze market conditions and determine optimal leverage
        """
        
        self.performance_stats['cycles_processed'] += 1
        
        try:
            current_price = fresh_data.get('current_price', Decimal('0'))
            base_balance = fresh_data.get('base_balance', Decimal('0'))
            quote_balance = fresh_data.get('quote_balance', Decimal('0'))
            
            # Check for idle capital data from capital_efficiency
            idle_percent = fresh_data.get('idle_percent', Decimal('0'))
            
           # Get safety buffer - ADD DEBUG HERE
            safety_buffer_raw = fresh_data.get('safety_buffer', 0.03)
            # Convert to Decimal
            if isinstance(safety_buffer_raw, float):
                safety_buffer = Decimal(str(safety_buffer_raw))
            elif isinstance(safety_buffer_raw, Decimal):
                safety_buffer = safety_buffer_raw
            else:
                safety_buffer = Decimal(str(safety_buffer_raw))

            # Calculate UNSAFE leverage (no buffer)
            unsafe_multiplier = Decimal('1.0') + (idle_percent * self.multiplier)
            
            # Calculate SAFE leverage multiplier (with buffer)
            safe_multiplier = self._calculate_safe_multiplier(
                base_balance, quote_balance, current_price, idle_percent
            )
            
            self.current_multiplier = safe_multiplier
            
            decision = {
                'action_required': safe_multiplier != Decimal('1.0'),
                'action': f"Apply {safe_multiplier}x (idle: {idle_percent*100:.1f}%, buffer: {safety_buffer*100:.1f}%)",
                'unsafe_leverage': unsafe_multiplier, 
                'safe_leverage': safe_multiplier,     
                'recommended_leverage': safe_multiplier,
                'confidence': self._calculate_confidence(fresh_data),
                'details': {
                    'base_balance': base_balance,
                    'quote_balance': quote_balance,
                    'current_price': current_price,
                    'idle_percent': float(idle_percent),
                    'safety_buffer': float(safety_buffer)
                }
            }
            
            if safe_multiplier > Decimal('1.0'):
                logging.debug(f"💰 Leverage calc: {unsafe_multiplier:.2f}x → {safe_multiplier:.2f}x "
                            f"(idle: {idle_percent*100:.1f}%, buffer: {safety_buffer*100:.1f}%)")
                self.performance_stats['leverage_applied'] += 1
            else:
                logging.debug(f"💰 No leverage needed (idle: {idle_percent*100:.1f}%)")
            
            # Store data for next cycle
            self.store_data(fresh_data)
            
            return decision
            
        except Exception as e:
            logging.error(f"🔥 Virtual Leverage analysis failed: {e}")
            return {
                'action_required': False,
                'error': str(e),
                'recommended_leverage': Decimal('1.0')
            }

    def _calculate_safe_multiplier(self, 
                                 base_balance: Decimal,
                                 quote_balance: Decimal,
                                 current_price: Decimal,
                                 idle_percent: Decimal = Decimal('0')) -> Decimal:
        """Calculate safe leverage multiplier"""
        # ROUND idle_percent
        idle_percent = idle_percent.quantize(Decimal('0.0001'))
        
        # Get safety buffer (default 3%)
        safety_buffer = Decimal('0.03')
        if self._last_data and 'safety_buffer' in self._last_data:
            safety_buffer = Decimal(str(self._last_data['safety_buffer']))
        
        if idle_percent > Decimal('0'):
            # Calculate usable idle capital
            usable_idle = max(Decimal('0'), idle_percent - safety_buffer)
            
            if usable_idle <= Decimal('0'):
                logging.debug(f"📊 No usable idle: {idle_percent*100:.1f}% - "
                            f"{safety_buffer*100:.1f}% buffer = {usable_idle*100:.1f}%")
                return Decimal('1.0')
            
            # Calculate base leverage
            leverage = Decimal('1.0') + (usable_idle * self.multiplier)
            
            logging.debug(f"📊 Leverage calc: 1 + ({usable_idle*100:.1f}% × {self.multiplier}) = {leverage:.3f}x")
            
            # Apply volatility adjustment
            volatility = self._calculate_volatility()
            
            if volatility > Decimal('0.015'):  # High volatility (>1.5%)
                # Reduce the multiplier portion only
                adjusted_multiplier = self.multiplier * Decimal('0.8')
                leverage = Decimal('1.0') + (usable_idle * adjusted_multiplier)
                logging.debug(f"📊 High volatility {volatility*100:.1f}%, "
                            f"adjusted: 1 + ({usable_idle*100:.1f}% × {adjusted_multiplier}) = {leverage:.3f}x")
            elif volatility < Decimal('0.003'):  # Very low volatility (<0.3%)
                # Increase the multiplier portion only
                adjusted_multiplier = self.multiplier * Decimal('1.15')
                leverage = Decimal('1.0') + (usable_idle * adjusted_multiplier)
                logging.debug(f"📊 Low volatility {volatility*100:.1f}%, "
                            f"adjusted: 1 + ({usable_idle*100:.1f}% × {adjusted_multiplier}) = {leverage:.3f}x")
            
            # Apply caps
            leverage = max(min(leverage, self.max_leverage), self.min_leverage)
            leverage = leverage.quantize(Decimal('0.01'))
            
            return leverage
        
        # Fallback: return no leverage (1.0x)
        return Decimal('1.0')
    
    def _calculate_volatility(self) -> Decimal:
        """Calculate market volatility from OHLC data"""
        if not self._last_data or 'market_data' not in self._last_data:
            return Decimal('0.005')  # Default moderate volatility
        
        try:
            market_data = self._last_data['market_data']
            ohlc_data = market_data.get('ohlc', [])
            
            if len(ohlc_data) < 5:  # Not enough data
                return Decimal('0.005')
            
            # Calculate volatility as average of high-low ranges
            ranges = []
            for candle in ohlc_data[-10:]:  # Last 10 candles
                try:
                    if len(candle) >= 5:
                        high = Decimal(str(candle[2]))
                        low = Decimal(str(candle[3]))
                        close = Decimal(str(candle[4]))
                        
                        if close > Decimal('0'):
                            candle_range = (high - low) / close
                            ranges.append(candle_range)
                except (IndexError, ValueError, TypeError):
                    continue
            
            if ranges:
                avg_volatility = sum(ranges) / Decimal(str(len(ranges)))
                return avg_volatility
            
            return Decimal('0.005')
        except Exception as e:
            logging.debug(f"Volatility calculation error: {e}")
            return Decimal('0.005')

    def _calculate_confidence(self, fresh_data: Dict[str, Any]) -> float:
        """Calculate confidence score for leverage recommendation"""
        confidence = 0.7  # Base confidence
        
        # Increase confidence if balances are sufficient
        if fresh_data.get('base_balance', Decimal('0')) > Decimal('0.1'):
            confidence += 0.1
        if fresh_data.get('quote_balance', Decimal('0')) > Decimal('100'):
            confidence += 0.1
            
        return min(confidence, 1.0)
    
    def apply_leverage(self, 
                      base_balance: Decimal,
                      quote_balance: Decimal) -> Tuple[Decimal, Decimal]:
        """
        Apply virtual leverage to balances
        Returns: (leveraged_base, leveraged_quote)
        """
        if not self.is_active:
            return base_balance, quote_balance
        
        # Safety check - Don't exceed actual balance
        leveraged_base = min(base_balance * self.current_multiplier, 
                            base_balance * Decimal('1.5'))  # Max 1.5x for safety
        
        leveraged_quote = min(quote_balance * self.current_multiplier,
                            quote_balance * Decimal('1.5'))
        
        logging.info(f"💰 Leverage: {base_balance:.8f} {self.base_asset} → "
                    f"{leveraged_base:.8f} {self.base_asset} ({self.current_multiplier:.1f}x)")
    
        return leveraged_base, leveraged_quote
    
    def shutdown(self):
        """Clean shutdown"""
        logging.info("🔄 Virtual Leverage shutting down")
        self.is_active = False