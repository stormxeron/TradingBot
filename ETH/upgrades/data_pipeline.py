"""
Data Pipeline - Provides fresh, consistent data to all upgrades
"""
import logging
import time
from typing import Dict, Any, List, Optional
from decimal import Decimal

class DataPipeline:
    """Collects and provides fresh market data to all upgrades"""
    
    def __init__(self, base_asset: str, quote_asset: str, trading_pair: str):
        self.base_asset = base_asset
        self.quote_asset = quote_asset
        self.trading_pair = trading_pair
        
        # Data caching
        self._last_update = 0
        self._cache_ttl = 5  # 5 seconds cache
        self._cached_data = None
        
        # Track OHLC data availability
        self._ohlc_cache_time = 0
        self._ohlc_data = []
        
        # Initialize utils_module to None
        self._utils_module = None  # <-- MAKE SURE THIS LINE EXISTS
        
        logging.debug("📊 Data Pipeline initialized")
    
    def set_utils_module(self, utils_module):
        """Set the utils module from main bot"""
        self._utils_module = utils_module
        logging.debug("✅ Utils module set for DataPipeline")
    
    def collect(self,
                trade_client,
                user_client,
                market_client,
                current_price: Decimal,
                current_atr_gap_factor: Decimal,
                managed_orders: Dict,
                base_balance: Decimal,
                quote_balance: Decimal) -> Dict[str, Any]:
        """
        Collect fresh data from all sources
        """
        current_time = time.time()
        
        # Use cache if recent
        if (self._cached_data and 
            current_time - self._last_update < self._cache_ttl):
            return self._cached_data
        
        try:
            # Collect market data using existing utility functions
            market_data = self._collect_market_data(market_client)

            data = {
                # Core trading data
                'timestamp': current_time,
                'current_price': current_price,
                'current_atr_gap_factor': current_atr_gap_factor,
                'base_balance': base_balance,
                'quote_balance': quote_balance,
                
                # Order data
                'managed_orders': managed_orders,
                'total_orders': len(managed_orders),
                'buy_orders': len([o for o in managed_orders.values() 
                                   if o.get('side') == 'buy']),
                'sell_orders': len([o for o in managed_orders.values() 
                                    if o.get('side') == 'sell']),
                
                # Market data
                'market_data': market_data,
                'ohlc_data': market_data.get('ohlc', []) if isinstance(market_data, dict) else [],
                
                # Performance metrics
                'cycle_count': 0,
                'time_since_last_fill': self._calculate_time_since_last_fill(managed_orders),
                
                # Metadata
                'base_asset': self.base_asset,
                'quote_asset': self.quote_asset,
                'trading_pair': self.trading_pair
            }
            
            # Cache the data
            self._cached_data = data
            self._last_update = current_time
            
            return data
            
        except Exception as e:
            logging.error(f"🔥 Data collection failed: {e}")
            # Return minimal data structure with empty market_data
            return {
                'market_data': {'ohlc': []},
                'current_price': current_price,
                'base_balance': base_balance,
                'quote_balance': quote_balance,
                'managed_orders': managed_orders,
                'total_orders': len(managed_orders)
            }
    
    def _collect_market_data(self, market_client) -> Dict[str, Any]:
        """Collect OHLC data for volatility calculations using existing utilities"""
        try:
            current_time = time.time()
            
            # Refresh OHLC data every 30 seconds
            if current_time - self._ohlc_cache_time > 30 or not self._ohlc_data:
                if self._utils_module and hasattr(self._utils_module, 'get_ohlc_data_cached'):
                    # Use the utils module passed from main bot
                    ohlc_data = self._utils_module.get_ohlc_data_cached(market_client, 5)
                else:
                    logging.warning("⚠️ Utils module not available for get_ohlc_data_cached")
                    ohlc_data = []
                
                self._ohlc_data = ohlc_data or []
                self._ohlc_cache_time = current_time
            
            if self._ohlc_data:
                # Process the candles into the format your main bot uses
                processed_candles = []
                for candle in self._ohlc_data[-20:]:  # Last 20 candles
                    try:
                        processed_candles.append([
                            str(candle[0]),  # time as string
                            str(candle[1]),  # open as string
                            str(candle[2]),  # high as string
                            str(candle[3]),  # low as string
                            str(candle[4]),  # close as string
                            str(candle[5]) if len(candle) > 5 else '0',  # vwap
                            str(candle[6]) if len(candle) > 6 else '0',  # volume
                            str(candle[7]) if len(candle) > 7 else '0'   # count
                        ])
                    except (IndexError, ValueError, TypeError) as e:
                        continue
                
                return {
                    'ohlc': processed_candles,
                    'interval': 5,
                    'candle_count': len(processed_candles)
                }
            
            return {'ohlc': []}
            
        except Exception as e:
            logging.warning(f"⚠️ Failed to collect market data: {e}")
            return {'ohlc': []}
    
    def _calculate_time_since_last_fill(self, managed_orders: Dict) -> float:
        """Calculate time since last order was filled"""
        try:
            # Look for orders with fill timestamps
            fill_times = []
            for order_data in managed_orders.values():
                # Check if this is a profit-taker (indicates recent fill)
                if order_data.get('is_profit_taker', False) and 'placement_time' in order_data:
                    # Profit-taker placed after fill, so fill happened shortly before
                    fill_times.append(order_data['placement_time'] - 60)  # Approximate
            
            if fill_times:
                last_fill = max(fill_times)
                return time.time() - last_fill
            
            return float('inf')  # No fills yet
        except Exception:
            return float('inf')
    
    def get_cached(self) -> Optional[Dict[str, Any]]:
        """Get cached data without refreshing"""
        return self._cached_data