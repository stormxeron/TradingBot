import logging
from logging.handlers import RotatingFileHandler
import time
import sys
import json
import os
import requests
import statistics # Needed for ATR calculation
from typing import Dict, Any, List, Optional, Tuple, Set
from decimal import Decimal, ROUND_DOWN, getcontext

# Set global precision higher for complex financial calculations (Matches the setting in engine)
getcontext().prec = 60

# --- KRAKEN LIBRARY IMPORTS (Required for utility functions) ---
try:
    from kraken.spot import Market, User, Trade
except ImportError:
    # Do not exit here, as the main script handles the library check, but log a warning.
    print("WARNING: 'kraken-api' library not found. Utility functions will not work.")

try:
    from config import (
        LOG_FILE, LOG_LEVEL, TRADING_PAIR, TRADE_HISTORY_FILE, 
        ATR_WINDOW, ATR_INTERVAL_MINUTES, ATR_MULTIPLIER, 
        MIN_PROFIT_GAP_PERCENT, MONITOR_FILE, LOW_VOLATILITY_THRESHOLD_PERCENT, 
        HIGH_VOLATILITY_THRESHOLD_PERCENT, ATR_HIGH_VOLATILITY_MULTIPLIER,
        MAX_PROFIT_REDUCTION, REINVEST_PERCENT
    )
except ImportError as e:
    print(f"FATAL ERROR: Could not import configuration from config.py. Error: {e}")
    sys.exit(1)

volatility_history = []
# Global adjustment tracking
last_adjustment_time = 0

# ==============================================================================
# API SAFETY & BACKOFF WRAPPER
# ==============================================================================
def _safe_api_call(api_call, *args, max_retries: int = 5, **kwargs) -> Dict[str, Any]:
    """
    Executes a Kraken API call with exponential backoff and error handling.
    This function handles network timeouts, transient errors, and API-reported errors 
    (with the exception of non-retryable fatal errors).
    """
    method_name = getattr(api_call, '__name__', str(api_call))
    
    for attempt in range(max_retries):
        try:
            # Execute the actual API call (e.g., client.request)
            response = api_call(*args, **kwargs) 
            
            if response is not None and isinstance(response, dict) and response.get('error'):
                error_list = response['error']
                
                # Check for fatal, non-retryable errors
                if any("Invalid" in err or 'EGeneral:Invalid arguments' in err for err in error_list):
                    logging.error(f"FATAL NON-RETRYABLE ERROR: {error_list}")
                    return {"error": error_list, "fatal": True}

                # --- CRITICAL FIX: Treat "Unknown order" as success ---
                if any("EOrder:Unknown order" in err or "Unknown order" in err for err in error_list):
                    logging.info(f"✅ Order already canceled/filled (Unknown order). Treating as success.")
                    return {"result": {"count": 1}}  # Return success response

                # Treat other API errors as transient for potential retry
                raise Exception(f"Kraken API reported an error: {error_list}")
            
            return response

        # CRITICAL FIXES FOR NETWORK AND PARSING ERRORS
        except requests.exceptions.ReadTimeout as e:
            logging.warning(f"Network Read Timeout on attempt {attempt + 1}/{max_retries}: {e}")
        except Exception as e:
            # Catches JSONDecodeError (parsing error), ConnectionError, and general exceptions
            logging.warning(f"Transient network/parsing error on attempt {attempt + 1}/{max_retries}: {e}")

        if attempt == max_retries - 1:
            logging.error(f"Max retries reached. Failed to execute API call.")
            return {"error": [f"Max retries reached: {method_name}"]}

        sleep_time = 2 ** attempt
        logging.info(f"Retrying in {sleep_time} seconds...")
        time.sleep(sleep_time)
        
    return {}

# ==============================================================================
# 2. ASSET & SYMBOL HELPERS
# ==============================================================================
def get_base_quote_assets(trading_pair: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Determines the Kraken asset codes for the base and quote assets from the trading pair.
    XBTUSD -> (XXBT, ZUSD, XBTUSD)
    ETHCAD -> (XETH, ZCAD, ETHCAD)
    """
    if len(trading_pair) < 4:
        return None, None, None

    # Common pairs have 6 characters (e.g., 'XBTUSD', 'ETHCAD')
    # Extract assets based on the Kraken naming convention (e.g., XBT, USD)
    # This is a simplification and might need adjustment for exotic pairs
    base_asset = trading_pair[:-3]
    quote_asset = trading_pair[-3:]
    
    # Kraken often uses 'X' prefix for crypto and 'Z' for fiat (though not always)
    # We resolve the actual asset names used in the balance endpoint
    
    # NOTE: For XBTUSD, Kraken uses 'XBT' in balances and 'XXBT' in pairs.
    # For common pairs like XBTUSD, this resolution is crucial.
    
    if trading_pair == 'XBTUSDC':
        return 'XBT', 'USDC', 'XBTUSDC' # Base, Quote, Kraken Pair Symbol
    if trading_pair == 'ETHCAD':
        return 'XETH', 'ZCAD', 'XETHZCAD'
    
    # Generic attempt for other pairs
    kraken_pair_symbol = base_asset.upper() + quote_asset.upper()
    return base_asset.upper(), quote_asset.upper(), kraken_pair_symbol

def calculate_available_funds(user_client: User, trading_pair: str) -> Tuple[Decimal, Decimal]:
    """
    Calculate AVAILABLE balances = Total balance - Funds allocated to open orders.
    Returns: (available_base, available_quote)
    """
    
    try:

        #logging.critical(f"🔍 DEBUG: calculate_available_funds() called for {trading_pair}")

        # 1. Get total balances
        total_base, total_quote = log_account_balances(user_client)

        #logging.critical(f"🔍 DEBUG: Total balances: {total_base} ETH, {total_quote} CAD")
        
        # 2. Get all open orders
        orders_response = _safe_api_call(user_client.get_open_orders)
        
        allocated_base = Decimal('0')
        allocated_quote = Decimal('0')
        
        if orders_response and 'open' in orders_response:
            open_orders = orders_response['open']
            
            for txid, order in open_orders.items():
                # Filter by trading pair
                order_pair = order.get('descr', {}).get('pair', '')
                if order_pair == trading_pair:
                    volume = Decimal(order.get('vol', '0'))
                    price_str = order.get('descr', {}).get('price', '0')
                    
                    try:
                        price = Decimal(price_str)
                    except:
                        price = Decimal('0')
                    
                    order_type = order.get('descr', {}).get('type', '')
                    
                    if order_type == 'sell':
                        allocated_base += volume
                    elif order_type == 'buy':
                        allocated_quote += volume * price
        
        # 3. Calculate available
        available_base = total_base - allocated_base
        available_quote = total_quote - allocated_quote
        
        #logging.critical(
        #    f"💰 BALANCE BREAKDOWN for {trading_pair}:\n"
        #    f"   Total:      {total_base:.6f} ETH, {total_quote:.2f} CAD\n"
        #    f"   Allocated:  {allocated_base:.6f} ETH, {allocated_quote:.2f} CAD\n"
        #    f"   Available:  {available_base:.6f} ETH, {available_quote:.2f} CAD"
        #)
        
        return available_base, available_quote
        
    except Exception as e:
        logging.error(f"Error calculating available funds: {e}")
        # Fallback to total balance
        return log_account_balances(user_client)

def resolve_kraken_symbol(trading_pair: str) -> Optional[str]:
    """
    Provides the official Kraken API symbol for the trading pair.
    Used for fetching ticker/OHLC data.
    """
    if trading_pair == 'XBTUSDC':
        return 'XBTUSDC'
    if trading_pair == 'ETHCAD':
        return 'XETHZCAD'
    # Generic resolution (may not work for all pairs)
    base, quote, kraken_symbol = get_base_quote_assets(trading_pair)
    return kraken_symbol if kraken_symbol else trading_pair

def get_asset_precisions(market_client: Market, trading_pair: str) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    """
    Fetches the necessary volume and price decimal precisions from Kraken's API 
    for the given trading pair.
    """
    # 1. Resolve to the Kraken symbol
    kraken_symbol = resolve_kraken_symbol(trading_pair)
    if not kraken_symbol:
        logging.error(f"🔥 ERROR - Could not resolve Kraken symbol for {trading_pair}.")
        return None, None

    #logging.debug(f"Attempting to fetch asset pair info for {kraken_symbol}")
    
    # 2. Call the API using the safe wrapper
    asset_info_response = _safe_api_call(market_client.get_asset_pairs, pair=kraken_symbol)

    # 3. Robust Error Checking
    # Check for None response (e.g., if _safe_api_call failed silently)
    if asset_info_response is None:
        logging.error(f"🔥 ERROR - API call for asset info returned None response for {kraken_symbol}.")
        return None, None
    
    # --- THIS IS THE CRITICAL CHANGE ---
    # The kraken-api client strips the 'result' wrapper. 
    # We look for the pair data directly using the kraken_symbol key.
    pair_data = asset_info_response.get(kraken_symbol)

    if pair_data is None:
        # Log unexpected response structure if the key is missing
        logging.error(f"🔥 ERROR - API call succeeded (HTTP 200) but pair data for {kraken_symbol} is missing or unexpected. Received keys: {list(asset_info_response.keys())}")
        return None, None

    # 4. Extract precisions
    volume_decimals = pair_data.get('lot_decimals')
    price_decimals = pair_data.get('pair_decimals')

    if volume_decimals is None or price_decimals is None:
        logging.error(f"🔥 ERROR - Missing 'lot_decimals' or 'pair_decimals' for {kraken_symbol}.")
        return None, None

    # Calculate Decimal objects for rounding
    VOLUME_PRECISION = Decimal('1e-' + str(volume_decimals))
    PRICE_PRECISION = Decimal('1e-' + str(price_decimals))

    logging.info(
        f"Precisions fetched: Volume: {volume_decimals} ({VOLUME_PRECISION}), "
        f"Price: {price_decimals} ({PRICE_PRECISION})"
    )

    return VOLUME_PRECISION, PRICE_PRECISION

# ==============================================================================
# MARKET DATA (PRICE & VOLATILITY)
# ==============================================================================
def get_current_price(market_client: Market) -> Optional[Decimal]:
    """
    Fetches the current mid-price of the trading pair.
    """
    kraken_symbol = resolve_kraken_symbol(TRADING_PAIR)
    if not kraken_symbol:
        logging.error(f"🔥 ERROR - Could not resolve Kraken symbol for {TRADING_PAIR}.")
        return None

    ticker_response = _safe_api_call(market_client.get_ticker, pair=kraken_symbol)

    # 1. Check for errors from the API wrapper itself
    if ticker_response.get('error'):
        logging.error(f"FATAL: Error fetching current price: {ticker_response.get('error')}")
        return None
        
    # 2. **CRITICAL FIX**: Get the pair data.
    # We look for the pair data directly, assuming the kraken-api client stripped the 'result' wrapper.
    data = ticker_response.get(kraken_symbol) 

    # 3. Fallback: Check if the client *didn't* strip the wrapper 
    # (i.e., if 'result' key exists and contains the pair data)
    if not data and ticker_response.get('result'):
        data = ticker_response['result'].get(kraken_symbol)

    # 4. Final check for required fields
    if not data or 'a' not in data or 'b' not in data:
        logging.error(f"FATAL: Ticker data is missing or malformed for {kraken_symbol}. Received keys: {list(ticker_response.keys())}")
        return None

    try:
        # Ask price (a[0]) and Bid price (b[0])
        ask = Decimal(data['a'][0])
        bid = Decimal(data['b'][0])
        
        # Calculate the mid-price
        mid_price = (ask + bid) / Decimal('2')
        logging.info(f"Price: {mid_price.quantize(Decimal('0.00001'))} (Ask: {ask} | Bid: {bid})")
        return mid_price

    except Exception as e:
        logging.error(f"🔥 ERROR - Failed to parse ticker data for {kraken_symbol}: {e}")
        return None

def get_ohlc_data(market_client: Market, interval_minutes: int) -> Optional[List[List[str]]]:    
    # --- CRITICAL FIX: Validate Kraken interval ---
    valid_intervals = [1, 5, 15, 30, 60, 240, 1440, 10080, 21600]
    if interval_minutes not in valid_intervals:
        logging.error(f"❌ INVALID INTERVAL: {interval_minutes}min. Kraken supports: {valid_intervals}. Using 15min default.")
        interval_minutes = 15  # Fallback to valid interval
    
    # Use existing utility function to get the correct Kraken symbol (e.g., XXBTZUSD)
    _, _, kraken_symbol = get_base_quote_assets(TRADING_PAIR)
    
    if not kraken_symbol:
        logging.error(f"🔥 ERROR - Could not resolve Kraken symbol for {TRADING_PAIR}")
        return None
    
    # Calculate 'since' parameter
    buffer_candles = 5
    total_candles_needed = ATR_WINDOW + buffer_candles
    seconds_back = total_candles_needed * interval_minutes * 60
    since_timestamp = int(time.time() - seconds_back)

    current_time = int(time.time())
    logging.debug(f"DEBUG: Current time: {current_time}")
    logging.debug(f"DEBUG: Since timestamp: {since_timestamp}")
    logging.debug(f"DEBUG: Looking back {seconds_back/3600:.2f} hours")
    
    ohlc_response = _safe_api_call(
        api_call=market_client.get_ohlc, 
        pair=kraken_symbol, 
        interval=interval_minutes,
        since=since_timestamp
    )

    logging.debug(f"DEBUG: API Response keys: {list(ohlc_response.keys()) if ohlc_response else 'None'}")
    logging.debug(f"DEBUG: Last value in response: {ohlc_response.get('last') if ohlc_response else 'None'}")

    if not ohlc_response:
        logging.error("🔥 ERROR - Failed to fetch OHLC data: Empty response from API call.")
        return None

    # Handle explicit API errors 
    if ohlc_response.get('error'):
        logging.error(f"🔥 ERROR - Failed to fetch OHLC data: {ohlc_response.get('error')}")
        return None
        
    # CRITICAL FIX: Initialize candles properly
    candles = None
    
    # Try different response formats
    candles = ohlc_response.get(kraken_symbol, [])
    
    # Fallback to the standard Kraken wrapper structure 
    if not candles and ohlc_response.get('result'):
        candles = ohlc_response['result'].get(kraken_symbol, [])

    # CRITICAL FIX: Check if candles is properly defined and not empty
    if not candles:
        logging.warning(f"Failed to find OHLC data list for {kraken_symbol}. Returning None.")
        return None
        
    # 🚨 CRITICAL FIX: Now candles is guaranteed to be defined
    if candles:  # This check is now safe
        # Log the actual timestamps to see what we're getting
        timestamps = [int(candle[0]) for candle in candles[-5:]]  # Last 5 candles
        logging.debug(f"DEBUG: Recent candle timestamps: {timestamps}")
        logging.debug(f"DEBUG: Corresponding times: {[time.ctime(ts) for ts in timestamps]}")
        newest_candle_timestamp = int(candles[-1][0])
        current_time = int(time.time())
        hours_old = (current_time - newest_candle_timestamp) / 3600
        logging.info(f"📊 OHLC DATA: {len(candles)} candles, newest {hours_old:.1f} hours old")
    
    #logging.info(f"💡 INFO - Successfully retrieved {len(candles)} raw OHLC data points.")
    
    return candles

def get_ohlc_data_cached(market_client: Market, interval_minutes: int, cache_seconds: int = 300):
    """Cached version of get_ohlc_data to avoid duplicate API calls"""
    cache_key = f"{interval_minutes}"
    
    if not hasattr(get_ohlc_data_cached, 'cache'):
        get_ohlc_data_cached.cache = {}
        get_ohlc_data_cached.cache_times = {}
    
    current_time = time.time()
    cached_data = get_ohlc_data_cached.cache.get(cache_key)
    cached_time = get_ohlc_data_cached.cache_times.get(cache_key, 0)
    
    if cached_data and (current_time - cached_time) < cache_seconds:
        logging.debug(f"📊 Using cached OHLC data ({interval_minutes}min)")
        return cached_data
    
    # Fetch fresh data
    fresh_data = get_ohlc_data(market_client, interval_minutes)
    if fresh_data:
        get_ohlc_data_cached.cache[cache_key] = fresh_data
        get_ohlc_data_cached.cache_times[cache_key] = current_time
    
    return fresh_data

def calculate_atr(ohlc_data: List[Dict[str, Decimal]], window: int) -> Optional[Decimal]:

    """
    Calculates the Average True Range (ATR) over a given window.
    Requires at least (window + 1) data points for the initial true range calculation.
    """
    if not ohlc_data or len(ohlc_data) < window + 1:
        logging.error(f"🔥 ERROR - ATR calculation failed: Not enough data points ({len(ohlc_data)} < {window + 1}).")
        return None

    true_ranges = []
    
    # Start True Range calculation from the second candle (index 1)
    for i in range(1, len(ohlc_data)):
        current = ohlc_data[i]
        previous = ohlc_data[i-1]

        # 1. Current High - Current Low
        range1 = current['high'] - current['low']
        
        # 2. Absolute value of Current High - Previous Close
        range2 = abs(current['high'] - previous['close'])
        
        # 3. Absolute value of Current Low - Previous Close
        range3 = abs(current['low'] - previous['close'])
        
        # True Range is the maximum of the three
        tr = max(range1, range2, range3)
        true_ranges.append(tr)

    # The True Ranges list now contains (N) True Ranges, where N = len(ohlc_data) - 1.
    # We want the ATR over the last 'window' periods.
    # The first 'window' True Ranges are used to calculate the initial average (SMA).
    
    # Ensure we have at least 'window' true ranges to calculate the ATR.
    if len(true_ranges) < window:
        logging.error(f"🔥 ERROR - ATR calculation failed: Not enough True Ranges ({len(true_ranges)} < {window}).")
        return None
        
    # Calculate the Simple Moving Average of the first 'window' True Ranges
    initial_sma = sum(true_ranges[:window]) / Decimal(str(window))
    
    # Start the standard Wilder's smoothing/averaging from this initial SMA
    atr = initial_sma
    
    # Use the rest of the True Ranges for smoothing (if any exist)
    for tr in true_ranges[window:]:
        # ATR = ((Previous ATR * (window - 1)) + Current TR) / window
        atr = ((atr * Decimal(str(window - 1))) + tr) / Decimal(str(window))

    return atr

def calculate_dynamic_volatility_multiplier(current_atr_percent: Decimal, historical_atr_data: List[Decimal]) -> Decimal:
    """
    Calculates a dynamic multiplier based on current volatility relative to historical norms.
    Returns a multiplier between 0.8 (extreme low vol) and 2.0 (extreme high vol).
    """

    logging.info(f"📊 VOLATILITY HISTORY: {len(historical_atr_data)} periods available")

    if historical_atr_data:
        # Show recent volatility range
        recent_vol = historical_atr_data[-5:] if len(historical_atr_data) >= 5 else historical_atr_data
        recent_str = [f"{v*100:.3f}%" for v in recent_vol]
        logging.info(f"📊 RECENT VOLATILITY: {recent_str}")

    if not historical_atr_data or len(historical_atr_data) < 20:
        needed = 20 - len(historical_atr_data)
        logging.info(f"📊 VOLATILITY: Building history ({len(historical_atr_data)}/20). Need {needed} more cycles. Using default: 1.2x")
        return Decimal('1.2')  # Default slightly higher
    
    # Calculate volatility z-score
    vol_mean = sum(historical_atr_data) / Decimal(str(len(historical_atr_data)))
    vol_std = (sum((x - vol_mean) ** 2 for x in historical_atr_data) / Decimal(str(len(historical_atr_data)))).sqrt()
    
    if vol_std == Decimal('0'):
        logging.info(f"📊 VOLATILITY: Zero standard deviation. Using default multiplier: 1.2x")
        return Decimal('1.2')
    
    LONG_TERM_NORMAL = Decimal('0.006')  # 0.6% - crypto's typical quiet day
    ABSOLUTE_FLOOR = Decimal('0.004')    # 0.4% - minimum reasonable "normal"
    # HYBRID FIX: Use the higher of recent mean vs long-term normal
    effective_mean = max(vol_mean, LONG_TERM_NORMAL, ABSOLUTE_FLOOR)
    
    # Calculate z-score using the sensible baseline
    z_score = (current_atr_percent - effective_mean) / vol_std
    
    # Log the hybrid analysis
    logging.info(f"📊 HYBRID VOLATILITY ANALYSIS:")
    logging.info(f"   Current ATR: {current_atr_percent*100:.4f}%")
    logging.info(f"   Recent Mean: {vol_mean*100:.4f}%")
    logging.info(f"   Long-Term Normal: {LONG_TERM_NORMAL*100:.2f}%")
    logging.info(f"   Effective Mean: {effective_mean*100:.4f}%")
    logging.info(f"   Historical Std: {vol_std*100:.4f}%")
    logging.info(f"   Z-Score: {z_score:.2f}")
    
    # Determine multiplier based on z-score
   # BOTH EXTREMES GET WIDER GRIDS
    if z_score > 2.5:    # Extreme high volatility → WIDER grids (capture big swings)
        multiplier = Decimal('2.0')
        level = "EXTREME HIGH"
    elif z_score > 1.5:  # High volatility → Normal grids
        multiplier = Decimal('1.5')
        level = "HIGH"
    elif z_score > 0.5:  # Moderately high → Slightly tighter
        multiplier = Decimal('1.3')
        level = "MODERATE HIGH"
    elif z_score < -2.5: # Extreme low volatility → WIDER grids (ensure profitability)
        multiplier = Decimal('1.8')
        level = "EXTREME LOW"
    elif z_score < -1.5: # Very low volatility → Wider grids
        multiplier = Decimal('1.6')
        level = "VERY LOW"
    elif z_score < -0.5: # Moderately low → Slightly wider
        multiplier = Decimal('1.3')
        level = "MODERATE LOW"
    else:                # Normal volatility → Tighter grids (optimal frequency)
        multiplier = Decimal('1.1')
        level = "NORMAL"
    
    logging.info(f"📊 VOLATILITY: {level} → Using {multiplier}x multiplier")
    logging.info(f"   Raw Gap: {current_atr_percent*100:.4f}% × {multiplier} = {(current_atr_percent * multiplier)*100:.4f}%")
    return multiplier

def get_moving_average(ohlc_data: List[List[str]], window: int) -> Optional[Decimal]:
    """
    Calculates the Simple Moving Average (SMA) of the closing prices.
    Assumes ohlc_data is a list of lists of strings, where close price is at index 4.
    """
    DEC_WINDOW = Decimal(str(window))
    
    if len(ohlc_data) < window:
        logging.warning(f"Not enough OHLC data ({len(ohlc_data)} found) to calculate MA window {window}.")
        return None
    
    # Only need the last 'window' number of candles
    relevant_candles = ohlc_data[-window:]
    
    close_prices_sum = Decimal('0')
    
    try:
        # Loop through the candles and extract the closing price (index 4)
        for candle in relevant_candles:
            # CRITICAL FIX: Convert string to Decimal and access via integer index [4]
            # OHLC format: [time, open, high, low, close, vwap, volume, count]
            close_prices_sum += Decimal(candle[4])
            
    except (IndexError, TypeError, ValueError) as e:
        logging.error(f"🔥 ERROR - Data corruption/indexing error in get_moving_average: {e}. Check OHLC structure.")
        return None
    
    # Calculate the average
    if close_prices_sum:
        ma = close_prices_sum / DEC_WINDOW
        return ma
        
    return None

def get_atr_for_grid_gap(market_client: Market, current_price: Decimal, maker_fee: Decimal, user_client: User = None, bot_state: Dict[str, Any] = None) -> Decimal:
    """
    Calculates the dynamic grid gap based on ATR percentage, dynamically adjusting 
    the multiplier based on market volatility thresholds defined in config.py.
    """
    # Convert ALL constants to Decimal at the start
    DEC_ATR_WINDOW = Decimal(str(ATR_WINDOW))
    DEC_ATR_MULTIPLIER = Decimal(str(ATR_MULTIPLIER))
    DEC_MIN_PROFIT_GAP_PERCENT = Decimal(str(MIN_PROFIT_GAP_PERCENT))
    
    # NEW Dynamic Thresholds from config.py - ensure they're Decimals
    DEC_LOW_THRESHOLD = Decimal(str(LOW_VOLATILITY_THRESHOLD_PERCENT))
    DEC_HIGH_THRESHOLD = Decimal(str(HIGH_VOLATILITY_THRESHOLD_PERCENT))
    DEC_HIGH_MULTIPLIER = Decimal(str(ATR_HIGH_VOLATILITY_MULTIPLIER))

     # ⚠️ ADD DEBUG LOGGING:
    logging.debug(f"🔍 DEBUG: get_atr_for_grid_gap called with user_client: {user_client}")
    if user_client is None:
        logging.warning("🚨 user_client is None at START of get_atr_for_grid_gap!")

    try:
        # CRITICAL CHANGE: Use the fixed get_ohlc_data function to get raw candles
        candles = get_ohlc_data_cached(market_client, ATR_INTERVAL_MINUTES)
        
        if not candles:
            # get_ohlc_data already logged the error, so just fall back
            logging.warning("⚠️ WARNING - Falling back to MIN_PROFIT_GAP_PERCENT due to missing OHLC data.")
            return DEC_MIN_PROFIT_GAP_PERCENT

        # --- ATR Calculation Logic (Your existing logic) ---
        required_candles = int(DEC_ATR_WINDOW) + 1
        
        if len(candles) < required_candles:
            logging.warning(f"Not enough OHLC data ({len(candles)}/{required_candles} required). Falling back to MIN_PROFIT_GAP_PERCENT.")
            return DEC_MIN_PROFIT_GAP_PERCENT

        # Only use the most recent, relevant candles for the ATR calculation
        relevant_candles = candles[-required_candles:]
        true_ranges = []
        
        # We start at index 1 because the first candle is only needed for the previous close
        for i in range(1, len(relevant_candles)):
            # OHLC format: [time, open, high, low, close, vwap, volume, count]
            # Indices: 0-time, 1-open, 2-high, 3-low, 4-close
            high = Decimal(relevant_candles[i][2])
            low = Decimal(relevant_candles[i][3])
            prev_close = Decimal(relevant_candles[i-1][4])
            
            # True Range calculation: max(H - L, |H - C_prev|, |L - C_prev|)
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            
            true_ranges.append(max(tr1, tr2, tr3))

        # Calculate Average True Range (SMATR for simplicity)
        atr_value = sum(true_ranges) / DEC_ATR_WINDOW 
        atr_percent = (atr_value / current_price)

        if bot_state is not None:
            volatility_history = bot_state.get('volatility_history', [])
        else:
            # Fallback to function attribute
            if not hasattr(get_atr_for_grid_gap, 'volatility_history'):
                get_atr_for_grid_gap.volatility_history = []
                volatility_history = get_atr_for_grid_gap.volatility_history
                logging.info(f"📊 Using function attribute history: {len(volatility_history)} periods")
        
        # Calculate dynamic multiplier
        dynamic_multiplier = calculate_dynamic_volatility_multiplier(atr_percent, volatility_history)

         # Update history (keep last 100 periods)
        volatility_history.append(atr_percent)
        if len(volatility_history) > 700:
            volatility_history.pop(0)
        
        # NEW: Save updated history back to bot_state
        if bot_state is not None:
            bot_state['volatility_history'] = volatility_history
            #logging.info(f"📊 Updated bot_state volatility history: {len(volatility_history)} periods")
                # DELETE the entire if/elif/else block that overrides it!
        # Remove these lines completely:
        # if atr_percent >= DEC_HIGH_THRESHOLD:
        #     dynamic_multiplier = DEC_HIGH_MULTIPLIER
        # elif atr_percent <= DEC_LOW_THRESHOLD:  
        #     dynamic_multiplier = DEC_ATR_MULTIPLIER * Decimal('0.8')
        # else:
        #     dynamic_multiplier = DEC_ATR_MULTIPLIER
        # In get_atr_for_grid_gap():
        dynamic_gap = atr_percent * dynamic_multiplier  # e.g., 0.5751% × 1.10 = 0.6326%
        # FIX: Use fee-based minimum instead of hardcoded 1.00%
        # Calculate fee-based minimum gap (cover 2x maker fees + 30% safety margin)
        # Calculate minimum profitable gap
        min_fees_gap = (maker_fee * Decimal('2')) * Decimal('1.3')
        min_profitable_gap = max(min_fees_gap, MIN_PROFIT_GAP_PERCENT)

        logging.info(f"📊 FINAL GAP CALCULATION:")
        logging.info(f"   ATR: {atr_percent*100:.4f}%")
        logging.info(f"   Dynamic Multiplier: {dynamic_multiplier}x")
        logging.info(f"   Dynamic Gap: {dynamic_gap*100:.4f}%")
        logging.info(f"   Minimum Profit: {min_profitable_gap*100:.4f}%")

        # Apply minimum floor only
        final_gap = max(dynamic_gap, min_profitable_gap)

        # Clean logging
        if dynamic_gap < min_profitable_gap:
            logging.warning(
                f"🔄 Gap boosted {dynamic_gap*100:.2f}% → {final_gap*100:.2f}% "
                f"(below profit minimum {min_profitable_gap*100:.2f}%)"
            )
        else:
            logging.info(
                f"✅ Volatility gap {final_gap*100:.2f}% " 
                f"(+{(final_gap - min_profitable_gap)*100:.2f}% above minimum)"
            )

        logging.critical(f"📊 Final grid gap: {final_gap*100:.2f}%")
        
        return final_gap

    except Exception as e:
        logging.error(f"Error during ATR calculation: {e}. Falling back to MIN_PROFIT_GAP_PERCENT.")
    return DEC_MIN_PROFIT_GAP_PERCENT

def calculate_recent_volatility(price_data: List[Decimal]) -> Decimal:
    """Calculate recent price volatility as standard deviation of returns"""
    if len(price_data) < 2:
        return Decimal('0')
        
    # Calculate daily returns
    returns = []
    for i in range(1, len(price_data)):
        daily_return = (price_data[i] - price_data[i-1]) / price_data[i-1]
        returns.append(float(daily_return))  # Convert to float for statistics
    
    if not returns:
        return Decimal('0')
    
    # Calculate standard deviation of returns (volatility)
    try:
        volatility = statistics.stdev(returns)
        return Decimal(str(volatility))
    except statistics.StatisticsError:
        # Fallback if not enough data for stdev
        avg_return = sum(returns) / len(returns)
        return Decimal(str(abs(avg_return)))

# ==============================================================================
# ORDER & BALANCE MANAGEMENT
# ==============================================================================
def get_full_open_orders_from_kraken(user_client, trading_pair: str) -> dict:

    # ... (Keep existing imports and setup) ...
    full_order_details = {}

    try:
        logging.info("Attempting to fetch raw open orders for file rebuild...")
        
        # 1. Fetch the raw response (Assuming _safe_api_call is defined)
        # Note: We must call without _safe_api_call temporarily if that wrapper is causing issues
        try:
            response = user_client.get_open_orders()
        except Exception as e:
            logging.error(f"Direct API call failed: {e}")
            return {}

        logging.debug(f"Raw Open Orders API Response for Rebuild: {response}") 
        
        # Check for API errors reported by Kraken
        if response is None or response.get('error'):
            error_details = response.get('error', ['Unknown API error']) if response else ['API call failed']
            logging.error(f"Failed to fetch open orders for file rebuild: {error_details}")
            return {}
        
        # 2. Extract the orders, using resilience
        # THIS IS THE LINE WE MUST TRUST, since the structure varies between Kraken libraries
        open_orders = response.get('result', response).get('open', {})
        
        #logging.info(f"API returned {len(open_orders)} total open orders before pair filter.") # NEW LOG LINE
        
        for txid, details in open_orders.items():
            
            # --- FILTERING LOGIC ---
            order_pair = details.get('descr', {}).get('pair')
            
            # The pair filter is confirmed correct now
            if order_pair != trading_pair: 
                logging.debug(f"Skipping order {txid}: Pair '{order_pair}' does not match target '{trading_pair}'.")
                continue
            
            # Extract order data
            description = details.get('descr', {})
            
            order_side = description.get('type') 
            order_price = description.get('price') 
            order_volume = details.get('vol') 
            
            # --- NEW: Extract the status field ---
            order_status = details.get('status')
            
            # CRITICAL CHECK: Ensure data integrity before converting to Decimal
            if order_side and order_price and order_volume and order_price != '0' and order_volume != '0':
                full_order_details[txid] = {
                    'side': order_side,
                    'price': Decimal(order_price),
                    'volume': Decimal(order_volume),
                    'status': order_status # <--- ADD THIS LINE
                }
            else:
                # NEW LOG LINE TO CATCH THE FAILURE POINT
                logging.warning(f"SKIPPED {txid}: Invalid details (Pair:{order_pair}, Side:{order_side}, Price:{order_price}, Vol:{order_volume}).")
                
        #logging.info(f"Successfully filtered {len(full_order_details)} open orders for '{trading_pair}' for file rebuild.")
        return full_order_details

    except Exception as e:
        logging.critical(f"FATAL EXCEPTION during get_full_open_orders_from_kraken: {e}")
        return {}

def get_order_creation_time(user_client: User, txid: str) -> float:
    """Get the actual order creation time from Kraken"""
    try:
        # Use get_open_orders or get_closed_orders to get order details
        # Try open orders first
        open_orders_response = _safe_api_call(user_client.get_open_orders)
        
        if open_orders_response and 'open' in open_orders_response:
            open_orders = open_orders_response['open']
            if txid in open_orders:
                order_data = open_orders[txid]
                # Kraken returns opentm (open timestamp)
                opentm = order_data.get('opentm')
                if opentm:
                    creation_time = float(opentm)
                    logging.debug(f"🕒 Order {txid} was created at {creation_time}")
                    return creation_time
        
        # If not found in open orders, try querying orders endpoint directly
        # Some Kraken libraries use different method names
        try:
            # Try different possible method names
            if hasattr(user_client, 'query_orders'):
                order_response = _safe_api_call(user_client.query_orders, txid=txid, trades=False)
            elif hasattr(user_client, 'get_orders'):
                order_response = _safe_api_call(user_client.get_orders, txid=txid, trades=False)
            else:
                # Fallback to current time
                logging.warning(f"⚠️ No order query method available, using current time for {txid}")
                return time.time()
                
            if order_response and txid in order_response:
                order_data = order_response[txid]
                opentm = order_data.get('opentm')
                if opentm:
                    return float(opentm)
        except Exception as e:
            logging.debug(f"Order query failed for {txid}: {e}")
        
        logging.warning(f"⚠️ Could not get creation time for {txid}, using current time")
        return time.time()
        
    except Exception as e:
        logging.error(f"Error getting order time for {txid}: {e}")
        return time.time()

def calculate_optimized_order_price(order_data: Dict, current_price: Decimal, atr: Decimal) -> Decimal:
    order_side = order_data['side']
    current_target = order_data['price']
    current_distance = abs(current_price - current_target) / current_target
    
    # DYNAMIC ADJUSTMENT SIZE BASED ON DISTANCE
    if current_distance < Decimal('0.003'):  # Very close (<0.30%)
        # MICRO-ADJUSTMENT for shy orders
        adjustment_size = MAX_PROFIT_REDUCTION  # 0.05%
    else:
        # LARGER ADJUSTMENT for far orders  
        adjustment_size = MAX_PROFIT_REDUCTION * Decimal('3')  # 0.15%
    
    if order_side == 'sell':
        new_price = current_target * (1 - adjustment_size)
    else:  # buy
        new_price = current_target * (1 + adjustment_size)
    
    logging.info(f"🔧 {'MICRO' if adjustment_size == MAX_PROFIT_REDUCTION else 'MACRO'} ADJUSTMENT: {current_target:.2f} → {new_price:.2f}")
    
    return new_price

def count_price_approaches(price_history: List[Decimal], target_price: Decimal) -> int:
    """Count how many times price approached within 0.05% of target"""
    approaches = 0
    threshold = Decimal('0.0005')  # 0.05%
    
    for price in price_history[-20:]:  # Check last 20 periods (more conservative)
        gap = abs(price - target_price) / target_price
        if gap <= threshold:
            approaches += 1
    
    return approaches

def assess_trend_weakening(price_history: List[Decimal], order_side: str) -> int:
    """Assess if trend is weakening (0-3 points)"""
    if len(price_history) < 10:
        return 0
    
    # Calculate recent momentum (last 5 periods vs previous 5)
    if len(price_history) >= 10:
        recent = price_history[-5:]
        older = price_history[-10:-5]
        
        recent_trend = (recent[-1] - recent[0]) / recent[0]
        older_trend = (older[-1] - older[0]) / older[0]
        
        # For sell orders (profit-taking), we want upward trend weakening
        if order_side == 'sell':
            if recent_trend < Decimal('0') and older_trend > Decimal('0'):  # Reversal
                return 3
            elif recent_trend < older_trend * Decimal('0.3'):  # Momentum dropped 70%
                return 2
            elif recent_trend < older_trend * Decimal('0.6'):  # Momentum dropped 40%
                return 1
    
    return 0

def cancel_furthest_managed_order(trade_client: object, managed_orders: Dict[str, Any], current_price: Decimal, side_to_free: str, save_func: callable) -> Optional[str]:
    """
    Finds and cancels the managed order furthest from the current price on the 
    specified side (buy/sell) to free up budget.
    """
    
    # 1. Filter orders by the required side (Logic remains robust)
    target_orders = {
        txid: order_data 
        for txid, order_data in managed_orders.items() 
        if order_data['side'] == side_to_free
    }
    
    if not target_orders:
        logging.warning(f"No {side_to_free.upper()} orders found to cancel for budget clearance.")
        return None
        
    # 2. Find the 'furthest' order (Logic remains robust)
    def calculate_distance(order_price):
        if side_to_free == 'buy':
            return current_price - order_price
        else: # side_to_free == 'sell'
            return order_price - current_price
            
    furthest_txid = max(
        target_orders.keys(), 
        key=lambda txid: calculate_distance(target_orders[txid]['price'])
    )
    furthest_order = target_orders[furthest_txid]
    
    logging.critical(
        f"🚨 EMERGENCY CANCEL: Canceling furthest {side_to_free.upper()} order "
        f"{furthest_txid} @ {furthest_order['price'].quantize(Decimal('0.01'))} "
        "to free up budget for new order placement."
    )
    
    # 3. Cancel the order via API (using the resilient wrapper)
    cancel_response = _safe_api_call(trade_client.cancel_order, txid=furthest_txid)
    
    # 4. CRITICAL FIX: Robustly check API response and update state
    is_canceled = False

    if cancel_response:
        # Case 1: Successful cancellation (API returns count > 0, regardless of wrapping)
        if (cancel_response.get('count', 0) > 0 or 
            (cancel_response.get('result') and cancel_response['result'].get('count', 0) > 0)):
            is_canceled = True

        # Case 2: Order was already gone (API reports an error that implies success)
        elif 'error' in cancel_response:
            error_list = cancel_response['error']
            if any("EOrder:Unknown order" in err for err in error_list):
                logging.warning(
                    f"Order {furthest_txid} was already gone (canceled/filled). "
                    "Treating as successful budget clearance."
                )
                is_canceled = True
            else:
                # Log any other non-budget-freeing error
                logging.error(f"❌ FAILED: API cancellation for {furthest_txid} failed. Error: {error_list}")

    # Final Action
    if is_canceled:
        # Remove from local list
        managed_orders.pop(furthest_txid, None)
        # Save the new state
        save_func(managed_orders) 
        
        logging.critical(f"✅ SUCCESS: Order {furthest_txid} canceled. Budget freed.")
        return furthest_txid
    else:
        # Error was already logged in step 4
        logging.error(f"❌ FAILED: Final cancellation check for {furthest_txid} failed. Response: {cancel_response}")
        return None
    
def log_account_balances(user_client: User) -> Tuple[Decimal, Decimal]:
    """
    Fetches the available (free) balance for the required assets using the safe API wrapper
    and logs them. Returns the balances for use in the main loop.
    
    This function's signature is designed to match the call in DynamicGrid_engine.py.
    """
    # Base asset and quote asset are derived from the global TRADING_PAIR
    base_asset, quote_asset, _ = get_base_quote_assets(TRADING_PAIR)
    
    try:        
        # Use the safe wrapper for the User client method
        balance_response = _safe_api_call(user_client.get_account_balance)
        
        # --- API ERROR CHECK (E.g., Permission Denied) ---
        if balance_response.get('error'):
            error_msg = balance_response.get('error')
            logging.error(f"FATAL: API error fetching balances: {error_msg}. Check 'Query Funds' permission!")
            return Decimal('0'), Decimal('0')

        # The kraken-api client typically strips the wrapper, returning the balance dict directly.
        balances = balance_response 

        base_balance = Decimal('0')
        quote_balance = Decimal('0')

        # Map common asset names to Kraken's format for reliable lookup
        kraken_asset_map = {
            'XBT': 'XXBT', 'BTC': 'XXBT', 
            'USD': 'ZUSD', 'EUR': 'ZEUR', 
            'ETH': 'XETH', 'USDT': 'USDT',
            'CAD': 'ZCAD', 'USDC': 'USDC'
        }
        
        # Create robust key sets for lookup (user's smart logic)
        base_keys = {base_asset, kraken_asset_map.get(base_asset, base_asset), base_asset.replace('X', '').replace('Z', '')}
        quote_keys = {quote_asset, kraken_asset_map.get(quote_asset, quote_asset), quote_asset.replace('X', '').replace('Z', '')}
        
        for key, balance_str in balances.items():
            # Use Decimal for high precision
            balance = Decimal(balance_str)
            clean_key = key.replace('X', '').replace('Z', '')
            
            # Check for exact key match or cleaned key match
            if key in base_keys or clean_key in base_keys:
                base_balance = balance
            elif key in quote_keys or clean_key in quote_keys:
                quote_balance = balance

        # Apply formatting for critical log (matching your bot's standard)
        base_balance_str = base_balance.quantize(Decimal('1E-8'), rounding=ROUND_DOWN)
        quote_balance_str = quote_balance.quantize(Decimal('1E-8'), rounding=ROUND_DOWN)

        logging.critical(f"✅ BALANCE: {base_asset}: {base_balance_str} | {quote_asset}: {quote_balance_str}")

        return base_balance, quote_balance

    except Exception as e:
        logging.error(f"Error fetching account balances: {e}. Returning zero balances.")
        return Decimal('0'), Decimal('0')

def get_current_fee_rate(user_client: User) -> Tuple[Decimal, Decimal]:
    """
    Fetches the current maker and taker fee rates from Kraken's get_trade_volume endpoint.
    """

     # ⚠️ ADD NULL CHECK WITH DEBUGGING:
    if user_client is None:
        logging.error("🚨 CRITICAL: user_client is None in get_current_fee_rate!")
        logging.error("🚨 This means the client wasn't properly passed through the call chain")
        return Decimal('0.0016'), Decimal('0.0026')
    
    # Conservative default fees (Kraken Tier 0)
    DEFAULT_MAKER = Decimal('0.0016')  # 0.16%
    DEFAULT_TAKER = Decimal('0.0026')  # 0.26%

    try:
        # Get trade volume data
        response = _safe_api_call(user_client.get_trade_volume)
        
        # --- DEBUG: Log the full response to see what we're getting ---
        logging.debug(f"Fee API Response: {response}")
        
        # --- CRITICAL FIX: Handle various response formats ---
        if response is None:
            logging.warning("⚠️ Fee API returned None. Using default fees.")
            return DEFAULT_MAKER, DEFAULT_TAKER
            
        # Check if we got an error response
        if 'error' in response and response['error']:
            logging.warning(f"⚠️ Fee API returned errors: {response['error']}. Using default fees.")
            return DEFAULT_MAKER, DEFAULT_TAKER
        
        # Check for fees in different possible locations
        fees_data = None
        
        # Try different response formats
        if 'fees' in response and response['fees'] is not None:
            fees_data = response['fees']
        elif 'result' in response and 'fees' in response['result']:
            fees_data = response['result']['fees']
        else:
            return DEFAULT_MAKER, DEFAULT_TAKER
        
        # --- CRITICAL FIX: Handle empty fees data ---
        if not fees_data or len(fees_data) == 0:
            logging.warning("⚠️ Fees data is empty. Using default fees.")
            return DEFAULT_MAKER, DEFAULT_TAKER
        
        fee_data = None
        
        # Try to find fee data for our trading pair
        if TRADING_PAIR in fees_data:
            fee_data = fees_data[TRADING_PAIR]
            logging.info(f"✅ Found fee data for {TRADING_PAIR}")
        else:
            # Fallback to any available pair
            available_pairs = list(fees_data.keys())
            if available_pairs:
                first_pair = available_pairs[0]
                fee_data = fees_data[first_pair]
                logging.info(f"⚠️ Using fee data from {first_pair} as fallback for {TRADING_PAIR}")
        
        # Validate fee data structure
        if (fee_data and isinstance(fee_data, dict) and 
            'maker' in fee_data and 'taker' in fee_data):
            
            maker_rate_percent = Decimal(str(fee_data['maker']))
            taker_rate_percent = Decimal(str(fee_data['taker']))
            
            # Convert percentages to decimals
            maker_rate_decimal = maker_rate_percent / Decimal('100')
            taker_rate_decimal = taker_rate_percent / Decimal('100')
            
            logging.critical(f"✅ FEE RATES: Maker={maker_rate_percent}% ({maker_rate_decimal}), Taker={taker_rate_percent}% ({taker_rate_decimal})")
            
            return maker_rate_decimal, taker_rate_decimal
        else:
            logging.warning("⚠️ Invalid fee data structure. Using default fees.")
            return DEFAULT_MAKER, DEFAULT_TAKER
        
    except Exception as e:
        logging.error(f"🔥 ERROR in fee rate retrieval: {e}. Using default fees.")
        return DEFAULT_MAKER, DEFAULT_TAKER

def retrieve_unique_traded_ids(client: object, start_time_str: str) -> Set[str]:
    """
    Calls the TradesHistory endpoint using the resilient wrapper and returns 
    a set containing all unique Order IDs that have trades recorded against them.
    
    Args:
        client: The initialized Kraken SpotClient object.
        start_time_str: The Unix timestamp string to search trades 'since'.
        
    Returns:
        A set of unique trade order IDs (e.g., {'O12345-ABCD-EFGH'}).
    """
    endpoint = "/0/private/TradesHistory"
    params = {'start': start_time_str, 'trades': True}
    
    # 1. Use the resilient wrapper
    trade_response = _safe_api_call(client.request, method="POST", uri=endpoint, params=params) 
    
    # 2. CRITICAL FIX: Check for the 'trades' key directly (verified structure)
    if trade_response and 'trades' in trade_response: 
        trades = trade_response['trades'] 
        # Restructure to a set for quick O(1) lookups
        unique_order_ids = {trade.get('ordertxid') for trade in trades.values() if trade.get('ordertxid')}
        return unique_order_ids
    
    logging.warning(f"Failed to retrieve structured trades or API error: {trade_response.get('error', 'N/A')}")
    return set()

def check_order_closure_reason(raw_client: object, txid: str, bot_start_time: Decimal) -> str:
    """
    Checks if a TXID was FILLED by searching the Trade History using the 
    confirmed reliable low-level API request (TradesHistory).
    
    Returns: 'FILLED', 'CANCELED' (Requires separate check), or 'UNKNOWN' (if data is delayed).
    """
    try:
        logging.info(f"Checking closure reason for TXID {txid}...")
        
        # Use the bot start time as the search 'start' to catch all relevant trades
        start_time_str = str(int(bot_start_time.quantize(Decimal('0'))))
        
        # ----------------------------------------------------
        # 1. Use the new helper function to get the set of filled Order IDs
        # ----------------------------------------------------
        unique_traded_ids = retrieve_unique_traded_ids(raw_client, start_time_str)
        
        # ----------------------------------------------------
        # 2. Check for the ID in the set
        # ----------------------------------------------------
        if txid in unique_traded_ids:
            logging.critical(f"🎉 SUCCESS: Order {txid} CONFIRMED FILLED via Trade History.")
            return 'FILLED'
        
        # ----------------------------------------------------
        # 3. Final Fallback: The Safety Net 
        # ----------------------------------------------------
        # If not found in trades, the status is inconclusive, enabling 'Wait and Retry'.
        logging.warning(f"Order {txid} not found in trade history. Status is INCONCLUSIVE/UNKNOWN.")
        
        return 'UNKNOWN' 
    
    except Exception as e:
        logging.error(f"🔥 FATAL UNHANDLED ERROR checking closure reason for {txid}: {e}")
        return 'UNKNOWN'

# ==============================================================================
# GRID RECONCILIATION & MANAGEMENT UTILITIES
# ==============================================================================
def clear_local_orders_file(file_path: str = MONITOR_FILE) -> None:
    """Clears the local orders file (txid.txt) to prepare for a new grid."""
    try:
        # Writes an empty list to the file
        with open(file_path, 'w') as f:
            json.dump([], f)
        logging.info("✅ Local orders file cleared.")
    except Exception as e:
        logging.error(f"Failed to clear local orders file ({file_path}): {e}")

def add_profits_to_balance(
    base_balance: Decimal, 
    quote_balance: Decimal, 
    accumulated_profit_base: Decimal, 
    accumulated_profit_quote: Decimal,
    trading_pair: str = None
) -> Tuple[Decimal, Decimal]:
    """
    Universal profit reinvestment for ANY trading pair.
    Returns: (adjusted_base_balance, adjusted_quote_balance)
    """
    
    adjusted_base = base_balance
    adjusted_quote = quote_balance
    
    # Get asset symbols for logging
    if trading_pair:
        base_asset, quote_asset, _ = get_base_quote_assets(trading_pair)
    else:
        base_asset, quote_asset = "BASE", "QUOTE"
    
    # Reinvest BASE currency profits (e.g., ETH, BTC)
    if accumulated_profit_base > Decimal('0'):
        base_to_reinvest = accumulated_profit_base * REINVEST_PERCENT
        adjusted_base += base_to_reinvest
        logging.info(f"💰 REINVESTING {base_asset}: "
                    f"Adding {base_to_reinvest:.8f} {base_asset} to sell balance "
                    f"(from {accumulated_profit_base:.8f} {base_asset} profits)")
    
    # Reinvest QUOTE currency profits (e.g., CAD, USD, USDC)
    if accumulated_profit_quote > Decimal('0'):
        quote_to_reinvest = accumulated_profit_quote * REINVEST_PERCENT
        adjusted_quote += quote_to_reinvest
        logging.info(f"💰 REINVESTING {quote_asset}: "
                    f"Adding {quote_to_reinvest:.2f} {quote_asset} to buy balance "
                    f"(from {accumulated_profit_quote:.2f} {quote_asset} profits)")
    
    return adjusted_base, adjusted_quote

def save_txids_to_file(txids: List[str], file_path: str = MONITOR_FILE) -> None:
    """Overwrites the local file with a given list of transaction IDs."""
    try:
        with open(file_path, 'w') as f:
            json.dump(txids, f)
    except Exception as e:
        logging.error(f"Failed to write txids to {file_path}: {e}")
    
def append_txids_to_file(new_txids: List[str], file_path: str = MONITOR_FILE) -> None:
    """Appends new transaction IDs to the existing local file."""
    try:
        with open(file_path, 'r') as f:
            existing_txids = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing_txids = []
        
    all_txids = list(set(existing_txids + new_txids))
    
    try:
        with open(file_path, 'w') as f:
            json.dump(all_txids, f)
        logging.info(f"Appended {len(new_txids)} new order IDs to local file.")
    except Exception as e:
        logging.error(f"Failed to append txids to {file_path}: {e}")

def is_price_level_covered(desired_price: Decimal, desired_side: str, live_open_orders: Dict[str, Any], tolerance: Decimal) -> bool:
    """
    Checks if a proposed order (price/side) is already covered by an open order 
    in the live orders dictionary, allowing for a small price tolerance.
    """
    live_open_dict = live_open_orders.get('result', {}).get('open', {})
    
    for order_data in live_open_dict.values():
        try:
            # Kraken stores the actual price in descr['price']
            order_price = Decimal(order_data.get('descr', {}).get('price'))
            order_type = order_data.get('descr', {}).get('type') # 'buy' or 'sell'
            
            # Check if the side matches and the price is within the tolerance
            if order_type == desired_side:
                diff = abs(order_price - desired_price)
                if diff <= tolerance:
                    return True
        except Exception:
            # Skip any malformed orders
            continue
            
    return False

# ==============================================================================
# LOGGING
# ==============================================================================
def setup_logging():
    """
    Configures logging to handle console, main file, and trade history file output.
    """
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG) # Catch everything at the root level

    # Check if handlers are already set up to prevent duplicates
    if logger.hasHandlers():
        # Clear existing handlers to allow for fresh setup (e.g., on re-run)
        logger.handlers.clear()

    # --- Handler 1: Main Log File (Always DEBUG level) ---
    try:
        # Use RotatingFileHandler to manage log file size
        file_handler = RotatingFileHandler(
            LOG_FILE, 
            maxBytes=10*1024*1024, # 10 MB limit
            backupCount=5,         # Keep up to 5 old logs
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'))
        logger.addHandler(file_handler)
    except Exception as e:
        # Print to console if the file creation fails
        print(f"ERROR: Could not set up file logging to {LOG_FILE}: {e}")

    # --- Handler 2: Trade History File (Only CRITICAL level for fills) ---
    try:
        # Dedicated file for fill history (set to CRITICAL to only capture log.critical calls for fills)
        trade_history_handler = logging.FileHandler(TRADE_HISTORY_FILE, mode='a', encoding='utf-8')
        trade_history_handler.setLevel(logging.CRITICAL)
        # Use a simple, non-verbose format for the trade history file
        trade_history_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        logger.addHandler(trade_history_handler)
    except Exception as e:
        # Print to console if the file creation fails
        print(f"ERROR: Could not set up trade history file logging to {TRADE_HISTORY_FILE}: {e}")

    # --- Handler 3: Console Handler (Only LOG_LEVEL or higher) ---
    console_handler = logging.StreamHandler()
    
    # Set console to only log important messages (based on user's LOG_LEVEL)
    console_handler.setLevel(LOG_LEVEL) 
    
    # Define a custom formatter for the console for better readability
    class CustomConsoleFormatter(logging.Formatter):
        """Custom formatter to add emojis and critical highlighting."""
        
        LEVEL_FORMATS = {
            logging.INFO: "💡 INFO - %(message)s",
            logging.CRITICAL: "🛑 CRITICAL - %(message)s",
            logging.ERROR: "🔥 ERROR - %(message)s",
            logging.WARNING: "⚠️ WARNING - %(message)s",
            logging.DEBUG: "🐛 DEBUG - %(message)s"
        }

        def format(self, record):
            # Use the custom format dictionary to look up the format string
            log_fmt = self.LEVEL_FORMATS.get(record.levelno, "%(levelname)s - %(message)s")
            formatter = logging.Formatter(log_fmt)
            return formatter.format(record)

    console_handler.setFormatter(CustomConsoleFormatter())
    logger.addHandler(console_handler)

    # Log successful initialization (will appear in both console and file)
    logging.info(f"Logging initialized. Console logging set to {logging.getLevelName(LOG_LEVEL)}. File logging to '{LOG_FILE}' set to DEBUG.")