"""
Momentum Analyzer Upgrade - Detects price momentum for smarter order placement
"""
import logging
import time
from typing import Dict, Any, List
from decimal import Decimal

class MomentumAnalyzer:
    """Analyzes price momentum for optimal order stacking"""
    
    def __init__(self, config: Dict[str, Any], price_precision: Decimal):
        self.config = config
        self.price_precision = price_precision
        
        # Settings
        self.lookback_periods = config.get('MOMENTUM_LOOKBACK_PERIODS', 20)
        self.min_momentum = Decimal(str(config.get('MIN_MOMENTUM_STRENGTH', 0.001)))
        self.confidence_threshold = config.get('MOMENTUM_CONFIDENCE_THRESHOLD', 0.7)
        
        # State
        self.price_history = []
        self.momentum_history = []
        self.current_trend = 'neutral'
        
        logging.info("📈 Momentum Analyzer initialized")
    
    def analyze(self, fresh_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze price momentum and market direction
        """
        try:
            current_price = fresh_data.get('current_price', Decimal('0'))
            
            # Update price history
            self._update_price_history(fresh_data)
            
            # Calculate momentum metrics
            momentum = self._calculate_momentum()
            trend = self._determine_trend(momentum)
            confidence = self._calculate_confidence()
            
            # Update state
            self.current_trend = trend
            self.momentum_history.append({
                'timestamp': fresh_data.get('timestamp', 0),
                'momentum': momentum,
                'trend': trend,
                'confidence': confidence
            })
            
            # Trim history
            if len(self.momentum_history) > 100:
                self.momentum_history = self.momentum_history[-100:]
            
            # Generate recommendations
            decision = self._generate_recommendations(
                momentum, trend, confidence, fresh_data
            )

            # Determine symbols
            if trend == 'bullish':
                trend_symbol = "📈"
            elif trend == 'bearish':
                trend_symbol = "📉"
            else:  # neutral
                trend_symbol = "➡️"

            # Action tracking
            action_required = decision.get('action_required', False)
            action_symbol = "⚡" if action_required else "➖"
            
            # SINGLE CONSOLIDATED LOG LINE
            #logging.info(f"Momentum: {trend_symbol} {momentum*100:+.3f}% ({trend}, {confidence*100:.0f}% conf) {action_symbol}")
            
            # Only show details if action is required
            if action_required:
                recommendations = decision.get('recommendations', [])
                if recommendations:
                    logging.info(f"   📋 Recommendations ({len(recommendations)}):")
                    for rec in recommendations:
                        logging.info(f"     • {rec.get('action', 'Unknown')}: {rec.get('reason', 'No reason')}")

            return decision
        
        except Exception as e:
            logging.error(f"🔥 Momentum Analyzer failed: {e}")
            return {'action_required': False, 'error': str(e)}
    
    def _update_price_history(self, fresh_data: Dict[str, Any]):
        """Smart update: OHLC for initial load, then append new candles"""
        
        # If history is empty or very small, load from OHLC
        if len(self.price_history) < 10:
            ohlc_data = fresh_data.get('ohlc_data', [])
            if not ohlc_data:
                market_data = fresh_data.get('market_data', {})
                if isinstance(market_data, dict):
                    ohlc_data = market_data.get('ohlc', [])
            
            if ohlc_data and len(ohlc_data) > 5:
                # Initial load from OHLC
                self.price_history = []
                for candle in ohlc_data[-self.lookback_periods:]:
                    try:
                        if len(candle) > 4:
                            self.price_history.append({
                                'price': Decimal(str(candle[4])),
                                'timestamp': candle[0]
                            })
                    except (IndexError, TypeError, ValueError):
                        continue
                #logging.critical(f"📈 MOMENTUM: Initial load of {len(self.price_history)} prices from OHLC")
        
        # Always add current price as newest point
        current_price = fresh_data.get('current_price')
        timestamp = fresh_data.get('timestamp', time.time())
        
        if current_price:
            # Check if we already have this price (avoid duplicates)
            if not self.price_history or abs(self.price_history[-1]['price'] - current_price) > Decimal('0.01'):
                self.price_history.append({
                    'price': current_price,
                    'timestamp': timestamp
                })
                #logging.critical(f"📈 MOMENTUM: Added current price {current_price}")
        
        # Keep history size manageable
        if len(self.price_history) > self.lookback_periods * 2:
            self.price_history = self.price_history[-(self.lookback_periods * 2):]
        
        #logging.critical(f"📈 MOMENTUM: Total history: {len(self.price_history)} entries")
    
    def _calculate_momentum(self) -> Decimal:
        """Calculate price momentum from history"""
        if len(self.price_history) < 5:
            return Decimal('0')
        
        recent_prices = [p['price'] for p in self.price_history[-self.lookback_periods:]]
        if len(recent_prices) < 2:
            return Decimal('0')
        
        # Simple momentum: (current - old) / old
        oldest = recent_prices[0]
        newest = recent_prices[-1]
        
        if oldest == Decimal('0'):
            return Decimal('0')
        
        return (newest - oldest) / oldest
    
    def _determine_trend(self, momentum: Decimal) -> str:
        """Determine market trend from momentum"""
        if momentum > self.min_momentum:
            return 'bullish'
        elif momentum < -self.min_momentum:
            return 'bearish'
        else:
            return 'neutral'
    
    def _calculate_confidence(self) -> float:
        """Calculate confidence in momentum reading"""
        # Simple confidence based on history length
        if len(self.price_history) < 10:
            return 0.3
        elif len(self.price_history) < 20:
            return 0.6
        else:
            return 0.8
    
    def _generate_recommendations(self,
                                 momentum: Decimal,
                                 trend: str,
                                 confidence: float,
                                 fresh_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate trading recommendations based on momentum"""
        
        managed_orders = fresh_data.get('managed_orders', {})
        current_price = fresh_data.get('current_price', Decimal('0'))
        
        if confidence < self.confidence_threshold:
            return {
                'action_required': False,
                'trend': trend,
                'momentum': float(momentum),
                'confidence': confidence,
                'reason': 'Low confidence in momentum reading'
            }
        
        recommendations = []
        
        if trend == 'bullish' and momentum > self.min_momentum * Decimal('2'):
            recommendations.append({
                'action': 'Bias grid toward sells',
                'reason': 'Strong upward momentum detected',
                'urgency': 'high',
                'details': f'Momentum: {momentum*100:.2f}%, Confidence: {confidence*100:.0f}%'
            })
        
        elif trend == 'bearish' and momentum < -self.min_momentum * Decimal('2'):
            recommendations.append({
                'action': 'Bias grid toward buys',
                'reason': 'Strong downward momentum detected',
                'urgency': 'high',
                'details': f'Momentum: {momentum*100:.2f}%, Confidence: {confidence*100:.0f}%'
            })
        
        if recommendations:
            return {
                'action_required': True,
                'action': recommendations[0]['action'],  # 🔥 ADD THIS
                'trend': trend,
                'momentum': float(momentum),
                'confidence': confidence,
                'recommendations': recommendations,
                'actions': recommendations,
                'reason': recommendations[0]['reason']
            }
        else:
            return {
                'action_required': False,
                'trend': trend,
                'momentum': float(momentum),
                'confidence': confidence,
                'reason': 'No strong momentum signal'
            }