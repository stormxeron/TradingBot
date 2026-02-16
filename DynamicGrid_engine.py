import time
import logging
import sys
import os
import json
import sys
import traceback
from decimal import Decimal, ROUND_DOWN, getcontext
from typing import Dict, Any, List, Optional, Tuple

# Set global precision higher for complex financial calculations
getcontext().prec = 60
# --- KRAKEN LIBRARY IMPORTS ---
try:
    from kraken.spot import Trade, Market, User
except ImportError:
    logging.critical("FATAL ERROR: The 'kraken-api' library is required. Please install it using: pip install kraken-api")
    sys.exit(1)
# --- CONFIGURATION & UTILITY IMPORTS ---

try:
    from config import (
        API_KEY, API_SECRET, TRADING_PAIR, CHECK_INTERVAL_SECONDS, 
        NUM_GRID_LEVELS, MONITOR_FILE, BUDGET_UTILIZATION_CAP, 
        TREND_FILTER_MA_WINDOW, TRAILING_ATR_THRESHOLD, CANCEL_ON_FATAL_ERROR, 
        ATR_MULTIPLIER, ATR_CHANGE_THRESHOLD, MIN_PROFIT_GAP_PERCENT, MIN_RECENTER_PERCENT,
        DYNAMIC_PROFIT_ADJUSTMENT_ENABLED, MIN_ADJUSTMENT_WAIT_MINUTES, MIN_ADJUSTMENT_SCORE,
        ATR_WINDOW, MAX_PROFIT_TAKER_WAIT_HOURS, MAX_PROFIT_TAKER_DISTANCE_PERCENT, 
        ACCUMULATED_PROFIT_BASE, ACCUMULATED_PROFIT_QUOTE, ENABLE_VIRTUAL_LEVERAGE, ENABLE_UPGRADES, 
        ENABLE_CAPITAL_EFFICIENCY, ENABLE_MOMENTUM_ANALYZER, VIRTUAL_LEVERAGE_MULTIPLIER, MAX_VIRTUAL_LEVERAGE,
        MAX_IDLE_CAPITAL_PERCENT, REBALANCE_TRIGGER_PERCENT, MIN_CAPITAL_UTILIZATION, MOMENTUM_LOOKBACK_PERIODS, MIN_MOMENTUM_STRENGTH,
        MOMENTUM_CONFIDENCE_THRESHOLD, MIN_VIRTUAL_LEVERAGE
    )
    from utils import (
        setup_logging, get_base_quote_assets, get_asset_precisions, 
        get_current_price, get_full_open_orders_from_kraken, _safe_api_call,
        get_atr_for_grid_gap, get_moving_average, log_account_balances, 
        check_order_closure_reason, clear_local_orders_file, 
        cancel_furthest_managed_order, get_current_fee_rate, get_ohlc_data_cached,
        count_price_approaches, calculate_recent_volatility, calculate_optimized_order_price, 
        calculate_atr, calculate_available_funds, add_profits_to_balance
    )
    from state_loaders import (
        save_managed_orders_to_file,
        load_managed_orders_from_file, 
        cleanup_managed_orders_file, load_bot_state, save_bot_state
    )
except ImportError as e:
    logging.critical(f"FATAL ERROR: Could not import configuration files. Ensure config.py and utils.py are present and correct. Error: {e}")
    sys.exit(1)

try:
    from upgrades.upgrade_manager import UpgradeManager
    UPGRADES_AVAILABLE = True
except ImportError as e:
    UPGRADES_AVAILABLE = False
    logging.warning(f"Upgrade system not available: {e}. Running in legacy mode.")


# --- Global State Variables ---
# This dictionary holds the orders the bot is actively managing. 
# Format: {txid: {'side': 'buy'/'sell', 'price': Decimal, 'volume': Decimal, 'level': int}}
managed_orders: Dict[str, Any] = {}
BASE_ASSET, QUOTE_ASSET, KRAKEN_SYMBOL = get_base_quote_assets(TRADING_PAIR)
VOLUME_PRECISION, PRICE_PRECISION = None, None # To be initialized in main()
# Global adjustment tracking
last_adjustment_time = 0
_last_ghost_time = 0

accumulated_profit_base = ACCUMULATED_PROFIT_BASE
accumulated_profit_quote = ACCUMULATED_PROFIT_QUOTE

upgrade_manager = None  # Global upgrade manager

# ==============================================================================
# 1. ORDER MANAGEMENT
# ==============================================================================

def load_managed_orders() -> Dict[str, Dict[str, Decimal]]:
    """Loads managed orders from the JSON file."""
    global managed_orders
    if os.path.exists(MONITOR_FILE):
        try:
            with open(MONITOR_FILE, 'r') as f:
                data = json.load(f)
                # Convert price and volume strings back to Decimal objects
                for txid, order_data in data.items():
                    order_data['price'] = Decimal(order_data['price'])
                    order_data['volume'] = Decimal(order_data['volume'])
                managed_orders = data
                logging.info(f"Loaded {len(managed_orders)} managed orders from {MONITOR_FILE}.")
                return managed_orders
        except Exception as e:
            logging.error(f"Error loading managed orders file {MONITOR_FILE}: {e}")
            # If the file is corrupt, better to start clean than use bad data
            managed_orders = {}
            return {}
    else:
        logging.info("Managed orders file not found. Starting fresh.")
        return {}

def cancel_all_open_orders(trade_client: Trade, user_client: User):
    """
    Fetches and cancels ALL open orders, strictly filtered to only the 
    TRADING_PAIR configured for this bot instance.
    """
    # CRITICAL: Specify the pair in the log for clarity
    logging.critical(f"🛑 CRITICAL - Attempting to cancel ALL open orders for {TRADING_PAIR} on Kraken.")
    
    # Use the User client to query open orders
    open_orders_response = _safe_api_call(user_client.get_open_orders)
    
    if open_orders_response and 'open' in open_orders_response:
        
        # --- CRITICAL FIX: Filter orders by the bot's configured TRADING_PAIR ---
        txids_to_cancel = []
        
        # open_orders_response['open'] is a dict where keys are txids and values are order details
        for txid, order_data in open_orders_response['open'].items():
            # Kraken's response includes the pair in the 'descr' dictionary
            # Use .get() for safety, though 'descr' should always be present
            order_pair = order_data.get('descr', {}).get('pair')
            
            # Check if the order pair matches the TRADING_PAIR for this bot instance
            if order_pair == TRADING_PAIR:
                txids_to_cancel.append(txid)
            else:
                 # Log the fact that a foreign order was ignored
                 logging.info(f"💡 INFO - Skipped foreign order {txid} for pair {order_pair}.")
        if not txids_to_cancel:
            logging.critical(f"💡 INFO - No open orders found to cancel for {TRADING_PAIR}.")
            return

        # Log the count of orders *after* filtering
        logging.critical(f"🛑 CRITICAL - Found {len(txids_to_cancel)} open orders for {TRADING_PAIR}. Canceling now.")
        
        # Kraken's cancel_orders can take a list of txids
        for txid in txids_to_cancel:
            try:
                # Use the trade client to perform the cancellation
                _safe_api_call(trade_client.cancel_order, txid=txid)
                logging.info(f"✅ SUCCESS: Cancelled order {txid}")
            except Exception as e:
                # Log non-fatal errors like order already cancelled
                logging.warning(f"⚠️ WARNING - Could not cancel order {txid}: {e}")

        logging.critical(f"✅ SUCCESS: Completed attempt to cancel all open orders for {TRADING_PAIR}.")
    else:
        logging.error("🔥 ERROR - Failed to fetch open orders for universal cancellation.")
        
def cancel_all_managed_orders(trade_client: Trade, user_client: User):

    """
    Cancels all orders currently listed in the managed_orders state, 
    **EXCEPT** protected profit-taker SELL orders.
    """
    global managed_orders
    logging.critical("🚨 CANCELLING MANAGED ORDERS (Protecting Profit-Takers)...")
    
    if not managed_orders:
        logging.info("No managed orders to cancel.")
        return

    # Work on a copy of the keys to safely modify managed_orders during iteration
    txids_to_cancel = list(managed_orders.keys())
    successfully_canceled_count = 0
    
    for txid in txids_to_cancel:
        order_data = managed_orders.get(txid)

        # --- PROFIT-TAKER PROTECTION LOGIC (CRITICAL FIX) ---
        # A profit-taker is identified by: 
        # 1. Being a SELL side order (currently only SELL profit-takers are implemented)
        # 2. Having a 'counterpart_id' key in its local data
        is_protected_sell = (
            order_data and 
            order_data.get('side') == 'sell' and 
            order_data.get('counter_id') is not None
        )
        
        if is_protected_sell:
            logging.info(f"🛡️ PROTECTED: Skipping cancellation for profit-taker SELL {txid}.")
            continue # Skip the try/except block and move to the next TXID
        # --- END PROTECTION LOGIC ---

        try:
            # 1. Attempt to cancel the order (Only reached if the order is NOT protected)
            cancel_response = _safe_api_call(trade_client.cancel_order, txid=txid)
            
            # Check if cancellation was successful or order was already gone
            if (cancel_response and 
                (cancel_response.get('count', 0) > 0 or 
                 cancel_response.get('result', {}).get('count', 0) > 0 or
                 # Check if the response indicates the order was already unknown (treated as success)
                 any("Unknown order" in err for err in cancel_response.get('error', [])))):
                
                logging.info(f"✅ SUCCESS: Canceled order {txid}")
                
                # Remove locally after confirmed successful cancellation
                managed_orders.pop(txid, None) 
                successfully_canceled_count += 1
            else:
                # Order might be already filled or canceled
                logging.info(f"Order {txid} appears to be already filled/canceled. Removing from local state.")
                managed_orders.pop(txid, None)
                
        except Exception as e:
            error_str = str(e)
            
            # Check for the 'Invalid order' error (order is no longer open)
            if 'Invalid order' in error_str or 'EOrder:Invalid order' in error_str or 'Unknown order' in error_str:
                
                bot_start_time = bot_state.get('bot_start_time', Decimal(time.time()))

                # Use the utility to check the exact reason the order is gone
                closure_reason = check_order_closure_reason(user_client, txid, bot_start_time)
                
                if closure_reason == 'FILLED':
                    logging.info(f"🎉 INFO: CANCELLATION SKIPPED - Order {txid} was already **FILLED**.")
                elif closure_reason == 'CANCELED':
                    logging.info(f"✅ INFO: CANCELLATION SKIPPED - Order {txid} was already **CANCELED** by external action.")
                else:
                    logging.warning(f"⚠️ WARNING: Order {txid} failed to cancel, but reason is {closure_reason}.")
                
                # Remove locally since it's confirmed closed/gone
                managed_orders.pop(txid, None)
                
            # 3. Handle rate limits by pausing and logging
            elif 'Rate limit exceeded' in error_str:
                logging.warning(f"⚠️ WARNING - Rate limit hit during cancellation of {txid}. Pausing 0.5s.")
                time.sleep(float(Decimal('0.5'))) 
                
            # 4. Handle other unhandled errors
            else:
                logging.error(f"🔥 ERROR - Failed to cancel order {txid} (Unhandled Error): {e}")
            
            # Pace API calls even on errors
            time.sleep(float(Decimal('0.1')))
            
    # Final step (not in loop): Save the state with protected orders remaining and grid orders removed
    save_managed_orders_to_file(managed_orders)

    logging.critical(f"✅ FINISHED CANCELLATION. {successfully_canceled_count} orders canceled. Protected orders remain in state.")

def execute_order(trade_client: Trade, side: str, price: Decimal, volume: Decimal,  is_profit_taker: bool = False, parent_txid: str = None) -> Optional[str]:
    """
    Places a limit order on Kraken, applying precision rounding.
    Returns the new order's TXID on success, or None/False on failure.
    False is a specific sentinel value for a fatal 'Cancel only' error.
    """

    # 🚨 ADD VOLUME DEBUGGING
    if 'leverage_applied' in globals():
        logging.critical(f"🔍 ORDER DEBUG - Placing {side.upper()} order:")
        logging.critical(f"   Volume: {volume} {BASE_ASSET}")
        logging.critical(f"   Price: {price} {QUOTE_ASSET}")
        logging.critical(f"   Value: {volume * price} {QUOTE_ASSET}" if side == 'buy' else f"   Value: {volume} {BASE_ASSET}")

    # 🚨 ADD DEBUG
    #logging.critical(f"🔍 DEBUG execute_order:")
    #logging.critical(f"   Side: {side}, Price: {price}, Volume: {volume}")
    #logging.critical(f"   Cost: {volume * price} {QUOTE_ASSET}" if side == 'buy' else f"   Value: {volume} {BASE_ASSET}")

    if VOLUME_PRECISION is None or PRICE_PRECISION is None:
        logging.error("🔥 ERROR - Precision values not initialized. Cannot place order.")
        return None

    # 1. Apply volume precision (must round down for base currency to avoid insufficient funds)
    rounded_volume = Decimal(volume).quantize(VOLUME_PRECISION, rounding=ROUND_DOWN)

    # 2. Apply price precision
    rounded_price = Decimal(price).quantize(PRICE_PRECISION)
    
    # 3. Check minimum order volume (Kraken minimum is typically 0.0001 XBT)
    if rounded_volume < VOLUME_PRECISION:
        logging.warning(f"⚠️ WARNING - Calculated volume {volume} rounds down to zero or minimum volume is too low after rounding. Skipping order.")
        return None

    logging.info(
        f"Attempting to place {side.upper()} order: "
        f"Pair={TRADING_PAIR}, Price={rounded_price}, Volume={rounded_volume}"
    )

    # Note: 'ordertype' is 'limit' by default for Trade.add_order
    response = _safe_api_call(
        trade_client.create_order, 
        pair=KRAKEN_SYMBOL,
        side=side,
        ordertype='limit',
        volume=str(rounded_volume),
        price=str(rounded_price),
        # Add 'post' flag to ensure it's a Maker order
        oflags='post'
    )

    if response.get('error'):
        error_list = response['error']
        logging.error(f"🔥 ERROR - Order placement failed: {error_list}")
        
        # Check for the fatal 'Cancel only' mode
        if any('Cancel only' in err for err in error_list):
            logging.critical("🛑 CRITICAL - Kraken is in 'Cancel only' mode. Shutting down trading.")
            # Return the False sentinel value
            return False

        # 1: Check for Insufficient Funds (This check was redundant, but kept for logic)
        if any('Insufficient funds' in err for err in error_list):
            # This is a fatal error for the current cycle's grid creation
            logging.error("🔥 ERROR - Order FAILED (Insufficient Funds). Grid creation aborted.")
            return None 

        #2 Check for the fatal 'Cancel only' mode (This check is a duplicate, but kept for clarity)
        if any('Cancel only' in err for err in error_list):
            logging.critical("🛑 CRITICAL - Kraken is in 'Cancel only' mode. Shutting down trading.")
            # Return the False sentinel value
            return False 

        # Other non-fatal errors (e.g., price too far)
        return None

    # Success: extract and return the TXID
    
    # 1. Check for the TXID list in the standard Kraken format (nested under 'result')
    txids = response.get('result', {}).get('txid')

    # 2. If not found, check for the TXID list directly at the top level
    if not txids:
        txids = response.get('txid')
        
    # Ensure txids is a list for safe access, otherwise set to an empty list.
    if not isinstance(txids, list):
        txids = []
        
    if txids and txids[0]:
        txid = txids[0]
        logging.critical(f"✅ SUCCESS: Placed {side.upper()} order {txid} at {rounded_price}.")

         # 🌟 CRITICAL: STORE THE ENHANCED ORDER DATA
        # This is where you add the profit-taker tracking

        order_data = {
            'side': side,
            'price': rounded_price, 
            'volume': rounded_volume,
            'is_profit_taker': is_profit_taker,  # Track if this is a profit-taking order
            'counter_id': parent_txid,           # Link to the filled order (using counter_id for consistency)
            'placement_time': time.time(),       # NEW: Track when placed
            'original_price': rounded_price,     # NEW: Remember original target
            'adjustment_count': 0                # NEW: Track adjustments
        }
        
        # Add to managed_orders
        managed_orders[txid] = order_data
        save_managed_orders_to_file(managed_orders)
        
        # 🌟 CRITICAL FIX: Add a sleep after successful placement to avoid rate limits
        # Using 200ms (0.2) as a robust throttle.
        time.sleep(float(Decimal('0.3')))
        
        return txid
    
    # CRITICAL: If extraction still fails, log the full response for final debugging.
    logging.error(f"🔥 ERROR - Order placed but failed to extract TXID. Full API Response: {response}")
    return None

# ==============================================================================
# 3. VOLUME CALCULATION & GRID INITIALIZATION
# ==============================================================================
def _calculate_grid_levels(current_price: Decimal, current_atr_gap_factor: Decimal, 
                           buy_volume: Decimal, sell_volume: Decimal,
                           num_levels: int = None) -> List[Dict[str, Any]]:
    """
    Calculates the full list of price levels and volumes for the grid.
    """
    orders_to_place = []
    
    # Use provided num_levels or default to NUM_GRID_LEVELS
    if num_levels is None:
        if isinstance(NUM_GRID_LEVELS, str):
            num_levels = int(NUM_GRID_LEVELS)
        else:
            num_levels = NUM_GRID_LEVELS
    
    logging.critical(f"🔍 GRID CREATION: {num_levels} levels at {current_atr_gap_factor*100:.2f}% gap")
    
    for i in range(1, num_levels + 1):
        deviation_percent = current_atr_gap_factor * Decimal(str(i))
        
        # BUY ORDER (Steps down from current price)
        buy_price = current_price * (Decimal('1') - deviation_percent)
        orders_to_place.append({
            'side': 'buy', 
            'price': buy_price, 
            'volume': buy_volume
        })
        
        # SELL ORDER (Steps up from current price)
        sell_price = current_price * (Decimal('1') + deviation_percent)
        orders_to_place.append({
            'side': 'sell', 
            'price': sell_price, 
            'volume': sell_volume
        })
    
    return orders_to_place

def get_proportional_volume_per_order(user_client: User, total_levels: int, trading_pair:str, 
                                     base_balance: Decimal, quote_balance: Decimal, 
                                     current_atr_gap_factor: Decimal = None,
                                     leverage_multiplier: Decimal = Decimal('1.0'),
                                     current_price: Decimal = None) -> Tuple[Optional[Decimal], Optional[Decimal], Optional[int]]:
    """
    ULTRA-SAFE volatility adjustment with budget validation
    Returns: (buy_volume, sell_volume, affordable_levels)
    """
    global accumulated_profit_base, accumulated_profit_quote

    # Reinvest profits into balances
    if trading_pair:
        base_balance, quote_balance = add_profits_to_balance(
            base_balance, quote_balance,
            accumulated_profit_base, accumulated_profit_quote,
            trading_pair
        )
    
    if isinstance(total_levels, str):
        total_levels = int(total_levels)
    
    DEC_TOTAL_LEVELS = Decimal(str(total_levels))
    
    logging.info(f"💰 Available Balances: {BASE_ASSET}: {base_balance}, {QUOTE_ASSET}: {quote_balance}")

    # 1. Get current price if not provided
    if current_price is None:
        current_price = get_current_price(Market())
        if not current_price:
            logging.error("🔥 ERROR - Cannot get current price for budget calculation.")
            return None, None, None
    
    # 2. Calculate what we can ACTUALLY afford
    total_portfolio_value = base_balance * current_price + quote_balance
    logging.critical(f"📊 TOTAL PORTFOLIO VALUE: ${total_portfolio_value:.2f}")
    
    # 3. Calculate cost to cover FULL requested range
    # Buy side cost: Need quote for N buy orders spread across range
    avg_buy_distance = current_atr_gap_factor * Decimal(str((total_levels + 1) / 2))  # Average distance
    avg_buy_price = current_price * (Decimal('1') - avg_buy_distance)
    
    # Conservative estimate: Each buy order needs volume to be meaningful
    min_meaningful_volume = VOLUME_PRECISION * Decimal('10')  # 10x minimum volume
    
    # Calculate required quote for minimal coverage
    required_quote_for_buys = min_meaningful_volume * avg_buy_price * DEC_TOTAL_LEVELS
    
    # Sell side: Need base for N sell orders
    required_base_for_sells = min_meaningful_volume * DEC_TOTAL_LEVELS
    
    total_required_value = required_quote_for_buys + (required_base_for_sells * current_price)
    
    # 4. Check if we can afford the FULL range
    budget_cap = Decimal(str(BUDGET_UTILIZATION_CAP)) * Decimal('0.95')  # With safety margin
    available_value = total_portfolio_value * budget_cap
    
    logging.critical(f"🔍 BUDGET ANALYSIS:")
    logging.critical(f"   Available (with cap): ${available_value:.2f}")
    logging.critical(f"   Required for full {total_levels} levels: ${total_required_value:.2f}")
    logging.critical(f"   Coverage: {(available_value / total_required_value * 100):.1f}%")
    
    # 5. Calculate AFFORDABLE levels if we can't cover full range
    affordable_levels = total_levels
    
    if available_value < total_required_value:
        # Reduce levels until we can afford it
        for test_levels in range(total_levels, 0, -1):
            test_DEC_LEVELS = Decimal(str(test_levels))
            test_avg_distance = current_atr_gap_factor * Decimal(str((test_levels + 1) / 2))
            test_avg_price = current_price * (Decimal('1') - test_avg_distance)
            
            test_required_quote = min_meaningful_volume * test_avg_price * test_DEC_LEVELS
            test_required_base = min_meaningful_volume * test_DEC_LEVELS
            test_required_total = test_required_quote + (test_required_base * current_price)
            
            if available_value >= test_required_total * Decimal('0.8'):  # 80% threshold
                affordable_levels = test_levels
                break
        
        if affordable_levels < total_levels:
            logging.warning(f"⚠️ BUDGET CONSTRAINT: Can only afford {affordable_levels}/{total_levels} levels")
            logging.warning(f"   (Available: ${available_value:.2f} vs Required: ${total_required_value:.2f})")
    
    # 6. Now calculate volumes based on AFFORDABLE levels
    DEC_AFFORDABLE_LEVELS = Decimal(str(affordable_levels))
    
    # Apply budget cap to balances
    BUDGET_CAP_DECIMAL = Decimal(str(BUDGET_UTILIZATION_CAP))
    raw_base_budget = base_balance * BUDGET_CAP_DECIMAL
    raw_quote_budget = quote_balance * BUDGET_CAP_DECIMAL
    
    # Calculate base volumes WITHOUT leverage first
    try:
        base_buy_volume_per_order = (raw_quote_budget / (DEC_AFFORDABLE_LEVELS * current_price)).quantize(VOLUME_PRECISION, rounding=ROUND_DOWN)
    except Exception as e:
        logging.error(f"🔥 ERROR - Failed to calculate buy volume: {e}")
        return None, None, None

    try:
        base_sell_volume_per_order = (raw_base_budget / DEC_AFFORDABLE_LEVELS).quantize(VOLUME_PRECISION, rounding=ROUND_DOWN)
    except Exception as e:
        logging.error(f"🔥 ERROR - Failed to calculate sell volume: {e}")
        return None, None, None
    
    # Apply leverage
    proposed_buy_volume = (base_buy_volume_per_order * leverage_multiplier).quantize(VOLUME_PRECISION, rounding=ROUND_DOWN)
    proposed_sell_volume = (base_sell_volume_per_order * leverage_multiplier).quantize(VOLUME_PRECISION, rounding=ROUND_DOWN)
    
    # Budget check with leverage
    total_buy_cost = proposed_buy_volume * current_price * DEC_AFFORDABLE_LEVELS
    total_sell_value = proposed_sell_volume * DEC_AFFORDABLE_LEVELS
    
    budget_cap = Decimal('0.95')  # 5% safety margin
    available_quote = raw_quote_budget * budget_cap
    available_base = raw_base_budget * budget_cap
    
    # Calculate maximum safe leverage
    max_leverage_for_buys = available_quote / (base_buy_volume_per_order * current_price * DEC_AFFORDABLE_LEVELS)
    max_leverage_for_sells = available_base / (base_sell_volume_per_order * DEC_AFFORDABLE_LEVELS)
    safe_leverage = min(max_leverage_for_buys, max_leverage_for_sells, Decimal('3.0'))
    
    if total_buy_cost > available_quote or total_sell_value > available_base:
        logging.warning(f"🚨 LEVERAGE {leverage_multiplier}x EXCEEDS BUDGET!")
        logging.warning(f"   Max safe leverage: {safe_leverage:.2f}x")
        
        # Apply safe leverage
        buy_volume_per_order = (base_buy_volume_per_order * safe_leverage).quantize(VOLUME_PRECISION, rounding=ROUND_DOWN)
        sell_volume_per_order = (base_sell_volume_per_order * safe_leverage).quantize(VOLUME_PRECISION, rounding=ROUND_DOWN)
        actual_leverage = safe_leverage
    else:
        buy_volume_per_order = proposed_buy_volume
        sell_volume_per_order = proposed_sell_volume
        actual_leverage = leverage_multiplier
    
    # Final logging
    logging.critical(
        f"💰 FINAL: {affordable_levels}/{total_levels} levels at {actual_leverage:.2f}x leverage | "
        f"Buy: {buy_volume_per_order.quantize(VOLUME_PRECISION)} {BASE_ASSET} | "
        f"Sell: {sell_volume_per_order.quantize(VOLUME_PRECISION)} {BASE_ASSET}"
    )
    
    return buy_volume_per_order, sell_volume_per_order, affordable_levels

def full_grid_reset(trade_client, user_client, current_price, current_atr_gap_factor, 
                   base_balance: Decimal, quote_balance: Decimal, 
                   leverage_multiplier: Decimal = Decimal('1.0'), 
                   optimization_goal: str = 'standard'):
    global managed_orders
    logging.critical(f"🔄 Starting full grid reset ({optimization_goal})...")
    
    # STRATEGY DECISION:
    # - For capital efficiency: cancel all profit-takers to free up capital
    # - For standard reset: keep viable profit-takers, adjust balances
    
    if optimization_goal == 'capital_efficiency':
        # CANCEL all profit-takers for maximum capital efficiency
        for txid, order in list(managed_orders.items()):
            if order.get('is_profit_taker', False):
                logging.warning(f"💰 Capital efficiency: Cancelling profit-taker {order['side'].upper()} @ {order['price']}")
                try:
                    _safe_api_call(trade_client.cancel_order, txid=txid)
                except:
                    pass
                managed_orders.pop(txid, None)
        
        # Now cancel everything else
        cancel_all_managed_orders(trade_client, user_client)
        managed_orders = {}
        
    else:
        # STANDARD reset: Keep viable profit-takers
        protected_txids = []
        
        for txid, order in list(managed_orders.items()):
            if order.get('is_profit_taker', False):
                price_distance = abs(current_price - order['price']) / current_price
                hours_old = (time.time() - order.get('placement_time', time.time())) / 3600
                
                if (price_distance < MAX_PROFIT_TAKER_DISTANCE_PERCENT and 
                    hours_old < MAX_PROFIT_TAKER_WAIT_HOURS):
                    protected_txids.append(txid)
                    logging.critical(f"💰 Protecting profit-taker {order['side'].upper()} @ {order['price']}")
                else:
                    # Cancel non-viable
                    try:
                        _safe_api_call(trade_client.cancel_order, txid=txid)
                    except:
                        pass
                    managed_orders.pop(txid, None)
        
        # MANUALLY cancel all NON-protected orders
        for txid in list(managed_orders.keys()):
            if txid not in protected_txids:
                try:
                    _safe_api_call(trade_client.cancel_order, txid=txid)
                    logging.info(f"✅ Cancelled non-profit-taker order {txid}")
                except Exception as e:
                    error_str = str(e)
                    if 'Invalid order' in error_str or 'Unknown order' in error_str:
                        logging.info(f"Order {txid} already gone")
                    else:
                        logging.error(f"Failed to cancel order {txid}: {e}")
                managed_orders.pop(txid, None)
    
    clear_local_orders_file()
    
    # 🚨 OPTIMIZATION GOAL: Adjust budget utilization
    if optimization_goal == 'capital_efficiency':
        efficiency_multiplier = Decimal('1.10')
        target_utilization = Decimal(str(BUDGET_UTILIZATION_CAP)) * efficiency_multiplier
        target_utilization = min(target_utilization, Decimal('0.98'))
        
        logging.critical(f"💰 CAPITAL EFFICIENCY: Using {target_utilization*100:.1f}% of capital")
        
        adjusted_base = base_balance * target_utilization
        adjusted_quote = quote_balance * target_utilization
    else:
        adjusted_base = base_balance * Decimal(str(BUDGET_UTILIZATION_CAP))
        adjusted_quote = quote_balance * Decimal(str(BUDGET_UTILIZATION_CAP))

    # Calculate volumes for new grid WITH ADAPTIVE LEVELS
    buy_volume, sell_volume, affordable_levels = get_proportional_volume_per_order(
        user_client, NUM_GRID_LEVELS, TRADING_PAIR, 
        adjusted_base, adjusted_quote,
        current_atr_gap_factor, 
        leverage_multiplier=leverage_multiplier,
        current_price=current_price  # Pass current price for budget calculation
    )
    
    if buy_volume is None or sell_volume is None:
        logging.error("Failed to calculate grid volumes")
        return None
    
    # Use AFFORDABLE levels instead of requested levels
    actual_levels = affordable_levels if affordable_levels else NUM_GRID_LEVELS
    
    logging.critical(f"📊 Using {actual_levels}/{NUM_GRID_LEVELS} affordable grid levels")
    
    # Create and place new orders with actual_levels
    target_orders = _calculate_grid_levels(current_price, current_atr_gap_factor, buy_volume, sell_volume, actual_levels)
    new_managed_orders = {}
    
    for order in target_orders:
        txid = execute_order(trade_client, order['side'], order['price'], order['volume'])
        
        if txid is False:  # Fatal 'Cancel Only' error
            return None
             
        if txid:
            new_managed_orders[txid] = {
                'side': order['side'], 
                'price': order['price'], 
                'volume': order['volume']
            }

    # Update state
    save_managed_orders_to_file(new_managed_orders)
    managed_orders = new_managed_orders
    
    logging.critical(f"✅ Grid reset complete. {len(new_managed_orders)} orders placed.")
    return current_price

def grid_replenishment(trade_client, user_client, market_client, current_price, current_atr_gap_factor, base_balance: Decimal, quote_balance: Decimal, bot_state: Dict[str, Any], leverage_multiplier: Decimal = Decimal('1.0')) -> bool:
    """
    Places missing grid orders using available balance.
    """
    global managed_orders
    
    # Load and reconcile state
    managed_orders = load_managed_orders_from_file()
    live_orders = get_full_open_orders_from_kraken(user_client, TRADING_PAIR)
    
    # Remove orders not on exchange
    for txid in [txid for txid in managed_orders if txid not in live_orders]:
        managed_orders.pop(txid, None)
    
    current_count = len(managed_orders)
    
    # Calculate affordable levels first
    buy_volume, sell_volume, affordable_levels = get_proportional_volume_per_order(
        user_client, NUM_GRID_LEVELS, TRADING_PAIR,
        base_balance, quote_balance,
        current_atr_gap_factor,
        leverage_multiplier=leverage_multiplier,
        current_price=current_price
    )
    
    if not buy_volume or not sell_volume:
        return False
    
    target_count = affordable_levels * 2
    needed = target_count - current_count
    
    if needed <= 0:
        save_managed_orders_to_file(managed_orders)
        return False
    
    # Determine missing orders
    buys_needed = NUM_GRID_LEVELS - len([o for o in managed_orders.values() if o['side'] == 'buy'])
    sells_needed = NUM_GRID_LEVELS - len([o for o in managed_orders.values() if o['side'] == 'sell'])
    
    # Create missing orders
    orders_to_place = []
    
    # Buy orders (below current)
    if buys_needed > 0:
        existing_buys = sorted([o['price'] for o in managed_orders.values() if o['side'] == 'buy'])
        start_price = min(existing_buys) if existing_buys else current_price
        
        for i in range(buys_needed):
            price = start_price * (Decimal('1') - current_atr_gap_factor * Decimal(str(i + 1)))
            orders_to_place.append({
                'side': 'buy', 
                'price': price.quantize(PRICE_PRECISION), 
                'volume': buy_volume
            })
    
    # Sell orders (above current)
    if sells_needed > 0:
        existing_sells = sorted([o['price'] for o in managed_orders.values() if o['side'] == 'sell'])
        start_price = max(existing_sells) if existing_sells else current_price
        
        for i in range(sells_needed):
            price = start_price * (Decimal('1') + current_atr_gap_factor * Decimal(str(i + 1)))
            orders_to_place.append({
                'side': 'sell', 
                'price': price.quantize(PRICE_PRECISION), 
                'volume': sell_volume
            })
    
    # Place orders
    placed = 0
    for order in orders_to_place[:needed]:
        txid = execute_order(trade_client, order['side'], order['price'], order['volume'])
        
        if txid:
            managed_orders[txid] = {
                'side': order['side'],
                'price': order['price'],
                'volume': order['volume']
            }
            placed += 1
            time.sleep(0.3)
    
    # Save state
    save_managed_orders_to_file(managed_orders)
    
    if placed > 0:
        logging.critical(f"✅ Placed {placed}/{needed} orders")
    else:
        logging.warning("No orders placed")
    
    return False

def adjust_order_price(trade_client: Trade, txid: str, order_data: Dict, new_price: Decimal) -> bool:
    """Actually adjust any order's price - returns success status"""
    try:
        logging.info(f"🔄 Attempting to adjust order {txid} from {order_data['price']} to {new_price}")
        
        # Cancel existing order
        cancel_response = _safe_api_call(trade_client.cancel_order, txid=txid)
        
        if cancel_response and (cancel_response.get('count', 0) > 0 or 
                               any("Unknown order" in err for err in cancel_response.get('error', []))):
            
            # Place new order at adjusted price
            new_txid = execute_order(
                trade_client, 
                order_data['side'], 
                new_price, 
                order_data['volume'],
                is_profit_taker=order_data.get('is_profit_taker', False),
                parent_txid=order_data.get('counter_id')
            )
            
            if new_txid:
                # Update managed_orders with new order
                managed_orders[new_txid] = {
                    **order_data,
                    'price': new_price,
                    'placement_time': time.time(),
                    'adjustment_count': order_data.get('adjustment_count', 0) + 1,
                    'original_price': order_data.get('original_price', order_data['price'])  # Keep original reference
                }
                # Remove old order
                managed_orders.pop(txid, None)
                
                logging.critical(f"✅ ORDER ADJUSTED: {txid} → {new_txid} at {new_price}")
                save_managed_orders_to_file(managed_orders)
                return True
            else:
                logging.error(f"❌ Failed to place adjusted order for {txid}")
                return False
                
        else:
            logging.error(f"❌ Failed to cancel order {txid} for adjustment")
            return False
            
    except Exception as e:
        logging.error(f"❌ Error adjusting order {txid}: {e}")
        return False

def should_adjust_any_order(order_data: Dict, price_history: List[Decimal], 
                          current_price: Decimal, atr: Decimal) -> Tuple[bool, Decimal]:
    """
    Determines if ANY order (grid or profit-taker) should be adjusted
    Returns: (should_adjust, new_price)
    """
    if not DYNAMIC_PROFIT_ADJUSTMENT_ENABLED:
        return False, order_data['price']
    
    order_side = order_data['side']
    current_target = order_data['price']
    adjustment_count = order_data.get('adjustment_count', 0)
    placement_time = order_data.get('placement_time', time.time())
    
    # 1. Check maximum adjustment limit
    MAX_TOTAL_ADJUSTMENTS = 5  # Higher limit for grid orders
    if adjustment_count >= MAX_TOTAL_ADJUSTMENTS:
        return False, current_target
    
    # 2. Check minimum time between adjustments
    current_time = time.time()
    time_since_last_adjustment = current_time - placement_time
    min_wait_seconds = MIN_ADJUSTMENT_WAIT_MINUTES * 60
    
    if time_since_last_adjustment < min_wait_seconds:
        return False, current_target
    
    # Initialize scoring system - FIX: Initialize total_score to 0
    total_score = 0
    
    # 3. Price Distance Score (0-4 points)
    # How far is this order from current price relative to ideal grid spacing?
    ideal_grid_spacing = atr * Decimal('2.0')  # 2x ATR for grid spacing
    current_distance = abs(current_price - current_target) / current_price
    
    # Price Distance Score - MUCH MORE SENSITIVE FOR SHY ORDERS
    # Price Distance Score - PROPER SHY THRESHOLDS
    if current_distance > ideal_grid_spacing * Decimal('2.0'):  # Too far (>0.60%)
        total_score += 1
    elif current_distance > ideal_grid_spacing * Decimal('1.5'):  # Quite far (>0.45%)
        total_score += 1  
    elif current_distance < Decimal('0.0005'):  # EXTREMELY close (<0.05%)
        total_score += 6  # MAX points
    elif current_distance < Decimal('0.0010'):  # Very close (<0.10%)
        total_score += 5  
    elif current_distance < Decimal('0.0015'):  # Quite close (<0.15%)
        total_score += 4
    elif current_distance < Decimal('0.0020'):  # Somewhat close (<0.20%)
        total_score += 3
    
    # 4. Price Approach Score (0-3 points)
    approaches = count_price_approaches(price_history, current_target)
    approach_score = min(3, approaches)
    total_score += approach_score

    # 5. TIME-BASED SCORING (NEW) - Add this
    hours_open = (current_time - placement_time) / 3600
    if hours_open > 4:  # More than 4 hours old
        total_score += 3
    elif hours_open > 2:  # More than 2 hours old
        total_score += 2  
    elif hours_open > 1:  # More than 1 hour old
        total_score += 1
    logging.debug(f"⏰ Time score: {3 if hours_open > 4 else 2 if hours_open > 2 else 1 if hours_open > 1 else 0} (open for {hours_open:.1f}h)")

    # 6. GRID POSITION SCORING (High volatility insurance) - Add this after time scoring
    # Only activate when volatility is high enough to make extreme orders problematic
    if atr > Decimal('0.015'):  # Only when ATR > 1.5% (high volatility)
        farthest_buy = min([o['price'] for o in managed_orders.values() if o['side'] == 'buy'], default=current_target)
        farthest_sell = max([o['price'] for o in managed_orders.values() if o['side'] == 'sell'], default=current_target)

        if order_side == 'buy' and current_target == farthest_buy:
            total_score += 1  # Bonus for furthest buy order in high vol
            logging.debug(f"🎯 Furthest BUY order bonus (+2)")
        elif order_side == 'sell' and current_target == farthest_sell:  
            total_score += 1  # Bonus for furthest sell order in high vol
            logging.debug(f"🎯 Furthest SELL order bonus (+2)")
        elif abs(current_price - current_target) > ideal_grid_spacing * Decimal('2.5'):
            total_score += 1  # Bonus for any order extremely far in high vol
            logging.debug(f"🎯 Extreme distance bonus (+1)")
    else:
        logging.debug("📊 Grid position scoring inactive (low volatility)")

    # 7. Volatility Change Score (0-3 points)
    # If volatility changed significantly since order placement
    recent_volatility = calculate_recent_volatility(price_history[-10:])
    if 'placement_volatility' in order_data:  # FIX: Use dict key check, not hasattr
        vol_change = abs(recent_volatility - order_data['placement_volatility']) / order_data['placement_volatility']
        if vol_change > Decimal('0.5'):  # 50% volatility change
            total_score += 1
        elif vol_change > Decimal('0.3'):  # 30% volatility change
            total_score += 1
        elif vol_change > Decimal('0.1'):  # 10% volatility change
            total_score += 1
    
    logging.info(f"📊 ORDER ADJUSTMENT SCORE: {total_score}/10 for {order_side.upper()} @ {current_target:.2f}")
    
    if atr < Decimal('0.003'):  # When volatility is very low (<0.3%)
        total_score += 1  # Small nudge to push scores over threshold
        logging.debug("📊 Low volatility bonus (+1)")

    if total_score >= MIN_ADJUSTMENT_SCORE:
        new_price = calculate_optimized_order_price(order_data, current_price, atr)
        return True, new_price
    
    return False, current_target

def check_profit_target_adjustments(trade_client: Trade, user_client: User, current_price: Decimal, atr_gap_factor: Decimal):
    """Check if any orders need adjustment with global 30-minute cooldown"""
    global last_adjustment_time
    
    try:
        if not DYNAMIC_PROFIT_ADJUSTMENT_ENABLED:
            logging.debug("🔍 Profit adjustment disabled")
            return
            
        # GLOBAL 30-MINUTE COOLDOWN CHECK
        current_time = time.time()
        time_since_last_adjustment = current_time - last_adjustment_time
        min_wait_seconds = MIN_ADJUSTMENT_WAIT_MINUTES * 60
        
        if time_since_last_adjustment < min_wait_seconds:
            minutes_remaining = (min_wait_seconds - time_since_last_adjustment) / 60
            logging.info(f"⏰ Global cooldown: {minutes_remaining:.1f}m remaining")
            return
            
        #logging.info("🔍 Checking orders for adjustments...")
            
        market_client = Market()
        ohlc_data = get_ohlc_data_cached(market_client, 5)
        if not ohlc_data:
            logging.debug("📊 No OHLC data for analysis")
            return
            
        # Calculate ATR
        ohlc_dict_list = []
        for candle in ohlc_data:
            try:
                ohlc_dict_list.append({
                    'high': Decimal(candle[2]),
                    'low': Decimal(candle[3]), 
                    'close': Decimal(candle[4])
                })
            except (IndexError, TypeError, ValueError):
                continue
                
        atr_value = calculate_atr(ohlc_dict_list, ATR_WINDOW)
        if not atr_value:
            logging.debug("📊 No ATR calculated")
            return
            
        atr_percent = atr_value / current_price
            
        # Get price history
        price_history = [Decimal(candle[4]) for candle in ohlc_data[-30:] if len(candle) > 4]
        
        if len(price_history) < 10:
            logging.debug("📊 Not enough price history")
            return
        
        #logging.info(f"🔍 Analyzing {len(managed_orders)} orders...")
        
        adjusted_count = 0
        qualified_orders = 0
        
        for txid, order_data in managed_orders.items():
            should_adjust, new_price = should_adjust_any_order(
                order_data, price_history, current_price, atr_percent
            )
            
            if should_adjust:
                qualified_orders += 1
                logging.info(f"🎯 QUALIFIED: {order_data['side'].upper()} @ {order_data['price']:.2f} → {new_price:.2f}")
                
                if adjust_order_price(trade_client, txid, order_data, new_price):
                    adjusted_count += 1
                    last_adjustment_time = time.time()
                    logging.info(f"✅ ADJUSTED: {order_data['side'].upper()} order")
                    break  # Only one per cycle
            else:
                logging.debug(f"📊 Skipped: {order_data['side'].upper()} @ {order_data['price']:.2f}")
                        
        # Summary log
        if adjusted_count > 0:
            logging.critical(f"✅ Adjusted {adjusted_count} order(s)")
        elif qualified_orders > 0:
            logging.info(f"⏳ {qualified_orders} orders qualified (rate limit)")
        else:
            logging.debug("📊 No orders needed adjustment")
                            
    except Exception as e:
        logging.error(f"❌ Error in profit adjustment: {e}")
    
# ==============================================================================
# 4. FILL CHECKING & REPLACEMENT LOGIC
# ==============================================================================
def check_for_fills_and_replace(trade_client: Trade, user_client: User, current_atr_gap_factor: Decimal, current_price: Decimal, trend_bias: str, bot_state: Dict[str, Any]) -> bool:
    """
    Checks if any managed orders have been filled/canceled and takes appropriate action.
    Returns True if a full grid reset is needed (due to fatal error), otherwise False.
    
    NOTE: Added current_price to the function signature for budget clearance logic.
    """
    
    #logging.info("--> Checking Kraken for order fills.")

    global _last_ghost_time
    
    # ----------------------------------------------------------------------
    # FIX: Define a consistent start time for the Trade History check. 
    # This ensures we look back far enough to catch all relevant trades.
    # We will set a 24-hour lookback window here. You can make this a global
    # constant or pass the bot's true start time if preferred.
    # ----------------------------------------------------------------------
    LOOKBACK_HOURS = 24
    check_start_time = Decimal(time.time()) - Decimal(str(LOOKBACK_HOURS * 3600))
    
    # Fetch all open orders managed by the bot from Kraken
    kraken_open_orders_dict = get_full_open_orders_from_kraken(user_client, TRADING_PAIR)
    
    # Identify orders that are locally tracked but NOT on Kraken
    orders_missing_from_kraken = [txid for txid in managed_orders if txid not in kraken_open_orders_dict]

    phantom_orders_to_cancel = [txid for txid in kraken_open_orders_dict if txid not in managed_orders]

    if phantom_orders_to_cancel:
        logging.critical(f"❌ PHANTOM DETECTED: Found {len(phantom_orders_to_cancel)} order(s) on Kraken not present in local state. Canceling for cleanup.")
        
        for txid in phantom_orders_to_cancel:
            # Retrieve order details safely using .get()
            phantom_order_details = kraken_open_orders_dict.get(txid, {})
            # Use safe dictionary access for logging details
            side = phantom_order_details.get('descr', {}).get('type', 'N/A')
            price = phantom_order_details.get('descr', {}).get('price', 'N/A')
            
            logging.error(f"⚠️ CLEANUP: Order {txid} ({side} @ {price}) is a phantom. Attempting cancellation.")
            
            # Call the API to cancel the specific order using the safe wrapper
            try:
                # Using the user-specified _safe_api_call for cancellation
                _safe_api_call(trade_client.cancel_order, txid=txid)
                logging.critical(f"✅ CLEANUP SUCCESS: Phantom order {txid} successfully canceled.")
            except Exception as e:
                # Log if cancellation ultimately fails after retries
                logging.error(f"❌ CLEANUP FAILED: Could not cancel phantom order {txid} after retries. Error: {e}")
    else:
        if not orders_missing_from_kraken:
            logging.debug("📊 Order sync: No changes")

        # --- NEW LOGIC: VERIFY CLOSURE REASON ---
        verified_filled_txids = []
        
        for txid in orders_missing_from_kraken:
            
            bot_start_time = bot_state.get('bot_start_time', Decimal(time.time()))
            closure_reason = check_order_closure_reason(user_client, txid, bot_start_time) 

            if closure_reason == 'FILLED':
                verified_filled_txids.append(txid)
                logging.critical(f"✅ VERIFIED FILL: Order {txid} was legitimately filled.")
            
            elif closure_reason == 'CANCELED':
                # This handles manual deletion/cancellation/expiration
                logging.warning(
                    f"Order {txid} was CANCELED/expired, NOT filled. "
                    "Removing from local list without placing counter-order."
                )
                managed_orders.pop(txid, None)
                
            else: # UNKNOWN closure reason (Delay or Canceled)
                logging.error(f"Could not determine closure reason for {txid}. Removing locally to avoid loop.")
                managed_orders.pop(txid, None)
                # Update the last ghost removal time
                _last_ghost_time = time.time()
                
        # Save state immediately to record all manual cancellations
        save_managed_orders_to_file(managed_orders) 
        
        if not verified_filled_txids:
            #logging.info("Only cancellations found. No counter-orders required.")
            # CRITICAL: Check profit adjustments BEFORE returning
            if DYNAMIC_PROFIT_ADJUSTMENT_ENABLED:
                check_profit_target_adjustments(trade_client, user_client, current_price, current_atr_gap_factor)
            return False
            
        # --- PROCEED WITH VERIFIED FILLED ORDERS ---
        
        # --- CRITICAL SINGLE-FILL ENFORCEMENT ---
        txids_to_process = [verified_filled_txids[0]]
        if len(verified_filled_txids) > 1:
            logging.critical(f"⚠️ WARNING - Found {len(verified_filled_txids)} fills. Only processing the first fill to immediately initiate budget-clearing grid re-center.")

        # The loop now only runs ONCE for the first VERIFIED filled order
        for filled_txid in txids_to_process:
            filled_order = managed_orders.get(filled_txid)
            if not filled_order:
                logging.error(f"🔥 ERROR - Filled order {filled_txid} not found in managed_orders. Skipping.")
                continue

            # --- CRITICAL FIX: Ensure all values are proper Decimals ---
            fill_side = filled_order['side']
            counter_side = 'sell' if fill_side == 'buy' else 'buy'
            
            # Convert to Decimal to ensure proper type
            filled_price = Decimal(str(filled_order['price']))  # <-- CRITICAL FIX
            filled_volume = Decimal(str(filled_order['volume']))  # <-- CRITICAL FIX

            global accumulated_profit_base, accumulated_profit_quote
            base_asset, quote_asset, _ = get_base_quote_assets(TRADING_PAIR)

            if fill_side == 'sell':
                # Sell filled = made quote currency profit (CAD, USD, USDC)
                profit_percent = current_atr_gap_factor * Decimal('0.5')  # 50% of gap as profit
                profit_quote = filled_price * filled_volume * profit_percent
                accumulated_profit_quote += profit_quote
                logging.critical(f"💰 {quote_asset} PROFIT: +{profit_quote:.2f} {quote_asset} "
                                f"(Total: {accumulated_profit_quote:.2f} {quote_asset})")
                
            else:  # buy filled
                # Buy filled = will make base currency profit (ETH, BTC)
                profit_percent = current_atr_gap_factor * Decimal('0.5')
                profit_base = filled_volume * profit_percent
                accumulated_profit_base += profit_base
                logging.critical(f"💰 {base_asset} PROFIT: +{profit_base:.6f} {base_asset} "
                                f"(Total: {accumulated_profit_base:.6f} {base_asset})")

            # Calculate new price: profit target based on the filled price.
            if counter_side == 'sell':
                new_price = filled_price * (Decimal('1') + current_atr_gap_factor)
            else: # counter_side == 'buy'
                new_price = filled_price * (Decimal('1') - current_atr_gap_factor)

            logging.critical(
                f"🎉 ORDER FILLED: {fill_side.upper()} @ {filled_price.quantize(PRICE_PRECISION)}. "
                f"Placing profit-taker {counter_side.upper()} @ {new_price.quantize(PRICE_PRECISION)} "
                f"using volume {filled_volume.quantize(VOLUME_PRECISION)}."
            )
            
            # --- 1. Place the mandatory counter-order first (The profit-taker) ---
            MAX_EMERGENCY_CANCELS = 3
            counter_txid = False

            for attempt in range(1 + MAX_EMERGENCY_CANCELS):
                counter_txid = execute_order(trade_client, counter_side, new_price, filled_volume, is_profit_taker=True, parent_txid=filled_txid)
                
                if counter_txid:
                    break # Success!

                if attempt < MAX_EMERGENCY_CANCELS:
                    side_to_cancel = counter_side 
                    logging.critical(f"🔄 EMERGENCY: Canceling furthest {side_to_cancel.upper()} order to free budget for profit-taker.")
                    
                    if cancel_furthest_managed_order(trade_client, managed_orders, current_price, side_to_cancel, save_managed_orders_to_file):
                        time.sleep(1) 
                        continue
                    else:
                        logging.error("Failed to cancel emergency order. Aborting placement.")
                        break 

            # If order placement ultimately failed after all retries and cancellations:
            if counter_txid is False: 
                if CANCEL_ON_FATAL_ERROR:
                    cancel_all_managed_orders(trade_client, user_client)
                return True # Trigger full reset/exit

            # Record the successful counter-order
            if counter_txid:
                order_data = {
                    'side': counter_side, 
                    'price': new_price, 
                    'volume': filled_volume,
                    'is_profit_taker': True,  # This IS a profit-taking order
                    'counter_id': filled_txid,  # Link to the original filled order
                    'placement_time': time.time(),  # Track when placed
                    'original_price': new_price,  # Remember original target price
                    'adjustment_count': 0  # Track adjustments
                }
                # Add to managed_orders for the new profit-taker
                managed_orders[counter_txid] = order_data 
            
            # 3. Remove the filled order from the local dictionary
            managed_orders.pop(filled_txid, None)

            # 4. Save the state with the new profit-taker and the removed filled order(s).
            save_managed_orders_to_file(managed_orders)
            
            # --- IMPORTANT: Pace the API Calls here to avoid rate limits ---
            time.sleep(float(Decimal('0.3')))

    # CRITICAL FIX: Move this OUTSIDE all conditionals - run on EVERY cycle
    if DYNAMIC_PROFIT_ADJUSTMENT_ENABLED:
        check_profit_target_adjustments(trade_client, user_client, current_price, current_atr_gap_factor) 

    # Signal main loop to continue monitoring
    return False

# ==============================================================================
# 5. CORE MONITORING CYCLE
# ==============================================================================
def determine_trend_bias(market_client: Market, current_price: Decimal) -> str:
    """
    Calculates the Moving Average and determines the short-term trend bias.
    'buy' if MA is below current price (price is trending up), 'sell' if above (price is trending down).
    """
    ohlc_data = get_ohlc_data_cached(market_client, 60) # Use 60-minute interval for trend filter
    
    if not ohlc_data:
        logging.warning("⚠️ WARNING - Cannot determine trend bias due to missing OHLC data. Assuming 'neutral'.")
        return 'neutral'

    ma = get_moving_average(ohlc_data, TREND_FILTER_MA_WINDOW)

    if not ma:
        logging.warning("⚠️ WARNING - Cannot determine trend bias due to MA calculation error. Assuming 'neutral'.")
        return 'neutral'

    # If current price is above MA, the trend is up (favor SELL-side profit taking, and BUY replenishment)
    if current_price > ma:
        logging.info(f"📈 TREND UP: Price ({current_price.quantize(PRICE_PRECISION)}) > MA ({ma.quantize(PRICE_PRECISION)}). Bias: SELL (profit taking/grid loading).")
        return 'sell' # This means we favor placing more SELL orders (profit side)
    
    # If current price is below MA, the trend is down (favor BUY-side profit taking, and SELL replenishment)
    elif current_price < ma:
        logging.info(f"📉 TREND DOWN: Price ({current_price.quantize(PRICE_PRECISION)}) < MA ({ma.quantize(PRICE_PRECISION)}). Bias: BUY (profit taking/grid loading).")
        return 'buy' # This means we favor placing more BUY orders (profit side)
    
    else:
        logging.info("⚖️ TREND NEUTRAL: Price == MA.")
        return 'neutral'

def check_for_recentering(market_client: Market, current_price: Decimal, user_client: User, current_atr_gap_factor: Decimal, trade_client: Trade = None) -> bool:
    """
    Determines if the price has moved outside a significant range 
    (relative to ATR) to justify recentering the entire grid.
    """
     # --- SMART PROFIT-TAKER PROTECTION ---
    MAX_WAIT = MAX_PROFIT_TAKER_WAIT_HOURS * 3600
    MAX_VIABLE_DISTANCE = Decimal('0.02')  # 2.0% maximum viable distance
    
    profit_takers_found = False
    
    for txid, order in list(managed_orders.items()):  # Use list() for safe removal
        if order.get('is_profit_taker', False):
            profit_takers_found = True
            placement_time = order.get('placement_time', time.time())
            time_open = time.time() - placement_time
            
            # Calculate how far profit-taker is from current price
            price_distance = abs(current_price - order['price']) / current_price
            
            formatted_price = order['price'].quantize(PRICE_PRECISION)
            
            # Decision logic:
            if time_open < MAX_WAIT and price_distance < MAX_VIABLE_DISTANCE:
                # Still viable - protect it
                hours_left = (MAX_WAIT - time_open) / 3600
                logging.info(
                    f"⏰ Protecting {order['side'].upper()} @ {formatted_price} "
                    f"({price_distance:.2%} away, {hours_left:.1f}h left)"
                )
                return False  # Block recentering
            else:
                # Not viable - mark for cleanup
                if price_distance >= MAX_VIABLE_DISTANCE:
                    logging.warning(
                        f"⚠️ Profit-taker {order['side'].upper()} @ {formatted_price} "
                        f"is TOO FAR ({price_distance:.2%} > {MAX_VIABLE_DISTANCE:.2%}) - abandoning"
                    )
                else:  # time_open >= MAX_WAIT
                    logging.warning(
                        f"⚠️ Profit-taker {order['side'].upper()} @ {formatted_price} "
                        f"EXPIRED ({time_open/3600:.1f}h > {MAX_WAIT/3600}h)"
                    )
                
                # Cancel this specific order
                try:
                    _safe_api_call(trade_client.cancel_order, txid=txid)
                    managed_orders.pop(txid, None)
                    logging.info(f"✅ Canceled abandoned profit-taker {txid}")
                except Exception as e:
                    logging.error(f"Failed to cancel profit-taker {txid}: {e}")
    
    # If we had profit-takers but they were all removed, log it
    if profit_takers_found:
        logging.info("💡 All profit-takers cleared - recentering check continues")
    
    atr_gap_percent = current_atr_gap_factor
    
    # 2. Find the current center of the managed grid (if orders exist)
    if not managed_orders:
        logging.info("Grid is empty. Recentering is not necessary.")
        return False
 
    # 3. Check grid spacing mismatch (with more reasonable threshold)
    # 3. Check grid spacing mismatch 
    if managed_orders:
        # Sort ALL prices in ASCENDING order for consistent calculation
        all_prices = sorted([o['price'] for o in managed_orders.values()])
        
        if len(all_prices) >= 6:  # Need at least 2 spacings
            # Calculate ALL spacings (simpler and more accurate)
            spacings = []
            for i in range(1, len(all_prices)):
                spacing = (all_prices[i] - all_prices[i-1]) / all_prices[i-1]
                spacings.append(spacing)
            
            avg_spacing = sum(spacings) / len(spacings)
            intended_spacing = current_atr_gap_factor
            
            spacing_ratio = avg_spacing / intended_spacing
            
            logging.debug(f"📊 Spacing: {avg_spacing*100:.2f}% vs {intended_spacing*100:.2f}%")
            
            spacing_mismatch_threshold = Decimal('0.3')  # 30% difference
            
            if abs(spacing_ratio - Decimal('1.0')) > spacing_mismatch_threshold:
                logging.critical(f"🛑 GRID SPACING MISMATCH: Ratio {spacing_ratio:.3f}x exceeds threshold")
                return True

    # Extract prices from managed orders
    prices = []
    for order_data in managed_orders.values():
        price = order_data.get('price')
        if isinstance(price, str):
            prices.append(Decimal(price))
        elif isinstance(price, (int, float)):
            prices.append(Decimal(str(price)))
        elif isinstance(price, Decimal):
            prices.append(price)
        else:
            logging.warning(f"⚠️ WARNING - Skipping invalid price type: {type(price)}")
            continue
    
    if not prices:
        logging.warning("No valid prices found in managed_orders.")
        return False
        
    # Calculate grid center
    grid_center = sum(prices) / Decimal(str(len(prices)))
    
    # 3. Calculate recentering threshold (percentage)
    trailing_threshold_decimal = Decimal(str(TRAILING_ATR_THRESHOLD))
    recenter_threshold_percent = atr_gap_percent * trailing_threshold_decimal

    recenter_threshold_percent = max(recenter_threshold_percent, MIN_RECENTER_PERCENT)
        
    # 4. Calculate current price deviation (percentage)
    price_deviation_percent = abs(current_price - grid_center) / grid_center
    
    # 5. Log the check (for debugging)
    logging.info(f"Recenter: {price_deviation_percent:.4%} deviation (threshold: {recenter_threshold_percent:.4%})")
    
    # 6. Trigger recentering if deviation exceeds threshold
    if price_deviation_percent >= recenter_threshold_percent:
        logging.critical(f"🛑 RECENTER TRIGGERED! Price moved {price_deviation_percent:.4%} which is >= {recenter_threshold_percent:.4%} threshold.")
        return True
    
    return False

def run_monitor_cycle(trade_client: Trade, user_client: User, market_client: Market, 
                     current_atr_gap_factor: Decimal, last_atr_gap_factor: Decimal, 
                     current_price: Decimal, base_balance: Decimal, quote_balance: Decimal,
                     bot_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Main monitoring cycle - checks triggers and manages grid.
    """
    global upgrade_manager

    try:
        # Get current balances
        real_base, real_quote = log_account_balances(user_client)

        # Apply formatting for critical log (matching your bot's standard)
        base_balance_str = real_base.quantize(Decimal('1E-8'), rounding=ROUND_DOWN)
        quote_balance_str = real_quote.quantize(Decimal('1E-8'), rounding=ROUND_DOWN)

        logging.critical(f"✅ BALANCE: {BASE_ASSET}: {base_balance_str} | {QUOTE_ASSET}: {quote_balance_str}")

        # Check for fills
        check_for_fills_and_replace(trade_client, user_client, current_atr_gap_factor, 
                                   current_price, 'neutral', bot_state)
        
        # In run_monitor_cycle, before the check:
        logging.critical(f"🔍 DEBUG: current_atr_gap_factor = {current_atr_gap_factor*100:.4f}%")
        logging.critical(f"🔍 DEBUG: MIN_PROFIT_GAP_PERCENT = {MIN_PROFIT_GAP_PERCENT*100:.4f}%")
        
        # Check for triggers
        recenter_triggered = check_for_recentering(market_client, current_price, 
                                                  user_client, current_atr_gap_factor, 
                                                  trade_client)
        
        # Check ATR change
        atr_change_triggered = False
        if last_atr_gap_factor != Decimal('0.0'):
            atr_change_ratio = current_atr_gap_factor / last_atr_gap_factor
            threshold = Decimal(str(ATR_CHANGE_THRESHOLD))
            if abs(atr_change_ratio - Decimal('1.0')) >= threshold:
                atr_change_triggered = True
                logging.critical(f"ATR changed by {abs(atr_change_ratio - Decimal('1.0')):.2%}")
        
        # Process upgrades
        # Process upgrades
        leverage = Decimal('1.0')
        if UPGRADES_AVAILABLE and upgrade_manager:
            try:
                # Get historical data
                market_data = get_ohlc_data_cached(market_client, 5, 100)  # 5-min candles, last 100
                newest_candle_timestamp = int(market_data[-1][0])
                current_time = int(time.time())
                hours_old = (current_time - newest_candle_timestamp) / 3600
                logging.debug(f"📊 OHLC: {len(market_data)} candles, {hours_old:.1f}h old")  # Changed to DEBUG

                decisions = upgrade_manager.process_cycle(
                    trade_client=trade_client,
                    user_client=user_client,
                    market_client=market_client,
                    current_price=current_price,
                    current_atr_gap_factor=current_atr_gap_factor,
                    managed_orders=managed_orders,
                    base_balance=real_base,
                    quote_balance=real_quote,
                    market_data=market_data
                )

                # Get ALL values from decisions (now they're all there!)
                momentum = decisions.get('momentum', Decimal('0'))
                trend = decisions.get('trend', 'neutral')
                confidence = decisions.get('confidence', Decimal('0.6'))
                idle_percent = decisions.get('idle_percent', Decimal('0'))
                safety_buffer = decisions.get('safety_buffer', Decimal('0.03'))
                original_leverage = decisions.get('original_leverage', Decimal('1.0'))  # UNSAFE: 1.38x
                net_leverage = decisions.get('net_leverage', Decimal('1.0'))            # SAFE: 1.34x
                final_leverage = decisions.get('final_leverage', Decimal('1.0'))        # CAPPED: 1.30x
                portfolio_value = decisions.get('portfolio_value', Decimal('0'))
                utilization = decisions.get('utilization', Decimal('0'))
                idle_base = decisions.get('idle_base', Decimal('0'))
                idle_quote = decisions.get('idle_quote', Decimal('0'))

                # Also get the other decision values
                force_reset = decisions.get('force_grid_reset', False)
                reset_reason = decisions.get('reset_reason', '')

                # Determine trend symbol
                if trend == 'bullish':
                    trend_symbol = "📈"
                elif trend == 'bearish':
                    trend_symbol = "📉"
                else:
                    trend_symbol = "➡️"

                # Calculate usable idle
                usable_idle = max(Decimal('0'), idle_percent - safety_buffer)

                # Right before your consolidated log line, add:
                logging.info(f"🔧 DEBUG: momentum={momentum}, confidence={confidence}, min_threshold={0.001}")
                logging.info(f"🔧 DEBUG: momentum > threshold? {abs(momentum) > Decimal('0.001')}")
                logging.info(f"🔧 DEBUG: confidence > 0.7? {confidence > Decimal('0.7')}")
                logging.info(f"🔧 DEBUG: Both true? {abs(momentum) > Decimal('0.001') and confidence > Decimal('0.7')}")

                # SINGLE CONSOLIDATED LOG - REPLACES ALL THE INDIVIDUAL LOGS
                if force_reset:
                    logging.critical(f"🔄 {reset_reason} | 📈 {trend_symbol} {momentum*100:+.3f}% ({confidence*100:.0f}% conf) | 🛡️ {idle_percent*100:.1f}% idle → {usable_idle*100:.1f}% usable | 🎯 RAW:{original_leverage:.2f}x → SAFE:{net_leverage:.2f}x → FINAL:{final_leverage:.2f}x")
                elif final_leverage != Decimal('1.0'):
                    logging.info(f"📊 📈 {trend_symbol} {momentum*100:+.3f}% ({confidence*100:.0f}% conf) | 🛡️ {idle_percent*100:.1f}% idle → {usable_idle*100:.1f}% usable | 🎯 RAW:{original_leverage:.2f}x → SAFE:{net_leverage:.2f}x → FINAL:{final_leverage:.2f}x")
                else:
                    logging.info(f"📊 📈 {trend_symbol} {momentum*100:+.3f}% ({confidence*100:.0f}% conf) | 🛡️ {idle_percent*100:.1f}% idle | 🎯 No leverage")
                                    
                # Also update your portfolio log line to use values from decisions
                # Instead of:
                # logging.info(f"💰 ${portfolio_value:.2f} ({utilization:.1f}% util, {idle_percent*100:.1f}% idle) | 🟢{BASE_ASSET}:{idle_base:.4f} 🔵{QUOTE_ASSET}:{idle_quote:.1f}")
                # Use:
                logging.info(f"💰 ${portfolio_value:.2f} ({utilization:.1f}% util, {idle_percent*100:.1f}% idle) | 🟢{BASE_ASSET}:{idle_base:.4f} 🔵{QUOTE_ASSET}:{idle_quote:.1f}")

                leverage = final_leverage  # Use final_leverage from decisions

                # 🚨 ACT ON CAPITAL EFFICIENCY!
                if force_reset:
                    # Determine optimization goal based on reason
                    if 'capital' in reset_reason.lower() or 'idle' in reset_reason.lower():
                        optimization = 'capital_efficiency'
                    else:
                        optimization = 'standard'
                    
                    new_center = full_grid_reset(
                        trade_client, user_client, current_price, 
                        current_atr_gap_factor, real_base, real_quote, 
                        leverage_multiplier=leverage,
                        optimization_goal=optimization
                    )
                    
                    if new_center:
                        bot_state['last_atr_gap_factor'] = current_atr_gap_factor
                        bot_state['last_center_price'] = new_center
                        bot_state['last_reset_reason'] = reset_reason
                        bot_state['last_optimization'] = optimization
                        return bot_state
                
                if decisions.get('emergency_shutdown', False):
                    logging.critical("🛑 Emergency shutdown")
                    cancel_all_managed_orders(trade_client, user_client)
                    return None
                    
            except Exception as e:
                logging.error(f"Upgrades failed: {e}")
        
        # Decision logic
        if recenter_triggered or atr_change_triggered:
            # Grid reset
            new_center = full_grid_reset(
                trade_client, user_client, current_price, 
                current_atr_gap_factor, real_base, real_quote, 
                leverage_multiplier=leverage
            )
            
            if new_center:
                bot_state['last_atr_gap_factor'] = current_atr_gap_factor
                bot_state['last_center_price'] = new_center
                return bot_state
                
        else:
            # Grid replenishment
            current_count = len(managed_orders)
            target_count = NUM_GRID_LEVELS * 2
            
            if current_count < target_count:
                grid_replenishment(
                    trade_client, user_client, market_client, 
                    current_price, current_atr_gap_factor,
                    real_base, real_quote, bot_state,
                    leverage_multiplier=leverage
                )
        
        # Log status
        if managed_orders:
            buy_count = sum(1 for o in managed_orders.values() if o['side'] == 'buy')
            sell_count = sum(1 for o in managed_orders.values() if o['side'] == 'sell')
            logging.critical(f"📊 Grid: {len(managed_orders)} orders ({buy_count}B/{sell_count}S)")
        
        return None
        
    except Exception as e:
        logging.error(f"Cycle error: {e}")
        raise

# ==============================================================================
# 6. MAIN EXECUTION
# ==============================================================================
def main():
    """
    Initializes the bot and runs the infinite trading loop.
    """
    global managed_orders
    global bot_state
    global VOLUME_PRECISION, PRICE_PRECISION, BASE_ASSET, QUOTE_ASSET, KRAKEN_SYMBOL
    global upgrade_manager

    # 1. Setup logging
    setup_logging()

    # 2. Initialize API Clients
    try:
        trade_client = Trade(key=API_KEY, secret=API_SECRET)
        user_client = User(key=API_KEY, secret=API_SECRET)
        market_client = Market(key=API_KEY, secret=API_SECRET)
        logging.info("API clients initialized.")
    except Exception as e:
        logging.critical(f"Failed to initialize Kraken API clients: {e}")
        sys.exit(1)

    # 3. Load managed orders AND persistent state from file (ADJUSTED)

    cleanup_managed_orders_file()
    loaded_orders = load_managed_orders_from_file()
    managed_orders = loaded_orders

    logging.info(f"Successfully loaded {len(managed_orders)} managed orders.")
    
    # 4. Determine asset details and precisions (Crucial initialization step)
    temp_base_asset, temp_quote_asset, temp_kraken_symbol = get_base_quote_assets(TRADING_PAIR)
    BASE_ASSET, QUOTE_ASSET, KRAKEN_SYMBOL = temp_base_asset, temp_quote_asset, temp_kraken_symbol
    if not KRAKEN_SYMBOL:
        logging.critical(f"FATAL: Could not resolve Kraken symbol for {TRADING_PAIR}.")
        sys.exit(1)

    VOLUME_PRECISION, PRICE_PRECISION = get_asset_precisions(market_client, TRADING_PAIR)
    if not all([VOLUME_PRECISION, PRICE_PRECISION]):
        logging.critical("FATAL: Could not retrieve necessary asset precisions. Exiting.")
        sys.exit(1)

    # --- Start Main Loop -

    # Initialize upgrade system if available
    upgrade_manager = None
    if UPGRADES_AVAILABLE and ENABLE_UPGRADES:
        try:
            config_dict = {
                'ENABLE_VIRTUAL_LEVERAGE': ENABLE_VIRTUAL_LEVERAGE,
                'ENABLE_CAPITAL_EFFICIENCY': ENABLE_CAPITAL_EFFICIENCY,
                'ENABLE_MOMENTUM_ANALYZER': ENABLE_MOMENTUM_ANALYZER,
                'VIRTUAL_LEVERAGE_MULTIPLIER': VIRTUAL_LEVERAGE_MULTIPLIER,
                'MAX_VIRTUAL_LEVERAGE': MAX_VIRTUAL_LEVERAGE,
                'MIN_VIRTUAL_LEVERAGE': MIN_VIRTUAL_LEVERAGE,
                'MAX_IDLE_CAPITAL_PERCENT': MAX_IDLE_CAPITAL_PERCENT,
                'REBALANCE_TRIGGER_PERCENT': REBALANCE_TRIGGER_PERCENT,
                'MIN_CAPITAL_UTILIZATION': MIN_CAPITAL_UTILIZATION,
                'MOMENTUM_LOOKBACK_PERIODS': MOMENTUM_LOOKBACK_PERIODS,
                'MIN_MOMENTUM_STRENGTH': MIN_MOMENTUM_STRENGTH,
                'MOMENTUM_CONFIDENCE_THRESHOLD': MOMENTUM_CONFIDENCE_THRESHOLD,
                'BUDGET_UTILIZATION_CAP': BUDGET_UTILIZATION_CAP,
                'NUM_GRID_LEVELS': NUM_GRID_LEVELS,
                'TRADING_PAIR': TRADING_PAIR,
                'BASE_ASSET': BASE_ASSET,
                'QUOTE_ASSET': QUOTE_ASSET,
                'ATR_MULTIPLIER': ATR_MULTIPLIER,
                'MIN_PROFIT_GAP_PERCENT': MIN_PROFIT_GAP_PERCENT
            }
            
            upgrade_manager = UpgradeManager(config=config_dict)

            try:
                import utils_eth
                utils_module = utils_eth
            except ImportError as e1:
                try:
                    import utils
                    utils_module = utils
                except ImportError as e2:
                    logging.error(f"❌ Could not import utils module at all!")
                    logging.error(f"   utils_eth error: {e1}")
                    logging.error(f"   utils error: {e2}")
                    return False
            
            #logging.critical(f"🔍 Utils module has get_ohlc_data_cached: {hasattr(utils_module, 'get_ohlc_data_cached')}")

            # Initialize upgrades with required parameters
            success = upgrade_manager.initialize(
                volume_precision=VOLUME_PRECISION,
                price_precision=PRICE_PRECISION,
                base_asset=BASE_ASSET,
                quote_asset=QUOTE_ASSET,
                trading_pair=TRADING_PAIR,
                user_client=user_client,
                market_client=market_client,
                utils_module=utils_module # <-- Pass the already imported utils
            )
            
            if success:
                logging.critical("🚀 UPGRADE SYSTEM INITIALIZED SUCCESSFULLY")
            else:
                logging.error("🔥 Upgrade system initialization failed")
                upgrade_manager = None
                
        except Exception as e:
            logging.error(f"🔥 Failed to initialize upgrades: {e}")
            upgrade_manager = None

    default_atr_factor = Decimal(str(ATR_MULTIPLIER))  # Ensure Decimal
    bot_state = load_bot_state(default_atr_factor)

    # EXTRACT AND VALIDATE:
    last_atr_gap_factor = bot_state.get('last_atr_gap_factor', default_atr_factor)
    if isinstance(last_atr_gap_factor, str):
        last_atr_gap_factor = Decimal(last_atr_gap_factor)
        bot_state['last_atr_gap_factor'] = last_atr_gap_factor
        save_bot_state(bot_state, accumulated_profit_base, accumulated_profit_quote)

    cycle_counter = 0

    while True:
        try:
            cycle_counter += 1

            logging.critical(f"\n==========================================================")
            logging.critical(f"|           STARTING MONITOR CYCLE #{cycle_counter}           |")
            logging.critical(f"==========================================================\n")

            # 1. Check Balances
            base_balance, quote_balance = log_account_balances(user_client)

            # 2. Get current price
            current_price = get_current_price(market_client)
            if not current_price:
                logging.error("🔥 ERROR - Could not retrieve current price. Skipping cycle.")
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue
            maker_fee, taker_fee = get_current_fee_rate(user_client)
            # 3. Calculate ATR-based gap
            current_atr_gap_factor = get_atr_for_grid_gap(market_client, current_price, maker_fee, user_client, bot_state)

            # In your main loop, simplify the fee check:
            try:
                # Use a more conservative calculation
                min_profitable_gap = (maker_fee * Decimal('2')) * Decimal('1.3')  # 30% margin
                
                logging.critical(f"💰 FEE CHECK: Current Gap={current_atr_gap_factor:.4%}, Min Profitable={min_profitable_gap:.4%}")
                
                if current_atr_gap_factor < min_profitable_gap:
                    logging.critical(f"🛑 GAP TOO SMALL: Using minimum profitable gap {min_profitable_gap:.4%}")
                    current_atr_gap_factor = max(min_profitable_gap, Decimal(str(MIN_PROFIT_GAP_PERCENT)))
                    
            except Exception as e:
                logging.warning(f"⚠️ Fee check skipped: {e}. Using configured gap: {current_atr_gap_factor:.4%}")
                # Continue with current ATR gap
                #Fallback to your configured minimum
                current_atr_gap_factor = Decimal(str(MIN_PROFIT_GAP_PERCENT))
            
            if not current_atr_gap_factor:
                 logging.error("Failed to calculate ATR factor. Skipping cycle.")
                 time.sleep(10)
                 continue

            # 4. Run the core monitoring, rebalancing, and replacement logic
            # Pass the previous ATR factor to allow run_monitor_cycle to check for required changes

            new_bot_state = run_monitor_cycle(trade_client, user_client, market_client, current_atr_gap_factor, last_atr_gap_factor, current_price, base_balance, quote_balance, bot_state)
            
            # 5. Update the state and save for the next cycle
            if new_bot_state:
                bot_state = new_bot_state
                save_bot_state(bot_state, accumulated_profit_base, accumulated_profit_quote)
                # Update last_atr_gap_factor FROM the new state
                last_atr_gap_factor = bot_state.get('last_atr_gap_factor', current_atr_gap_factor)
            else:
                # If no state change, still update for next cycle comparison
                last_atr_gap_factor = current_atr_gap_factor
                save_bot_state(bot_state, accumulated_profit_base, accumulated_profit_quote)
                
            current_gap = current_atr_gap_factor
            # Use the HIGHER of fee-based minimum OR a very low safety floor
            SAFETY_FLOOR = Decimal('0.0020')  # 0.20% absolute minimum
            min_profitable = max(min_profitable_gap, SAFETY_FLOOR)

            logging.critical(f"\n--- CYCLE #{cycle_counter} FINISHED. Waiting {CHECK_INTERVAL_SECONDS} seconds ---\n")
            time.sleep(float(CHECK_INTERVAL_SECONDS))

        except Exception as e:
            logging.critical(f"🛑 CRITICAL - Main loop error: {e}")
            logging.error(traceback.format_exc())  # Add full traceback
            
            if CANCEL_ON_FATAL_ERROR:
                try:
                    cancel_all_managed_orders(trade_client, user_client)
                except Exception as cancel_error:
                    logging.error(f"Cancel failed: {cancel_error}")
            
            time.sleep(60)
            # Consider reloading state after error
            bot_state = load_bot_state(default_atr_factor)
            managed_orders = load_managed_orders_from_file()
                    
            time.sleep(60)

if __name__ == "__main__":
    main()