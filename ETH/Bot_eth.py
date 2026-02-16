import logging
from kraken.spot import Trade, Market, User
from decimal import Decimal, ROUND_DOWN, getcontext
import os
import sys
import time 
from typing import Tuple

# Set precision for Decimal operations to handle small trade sizes
getcontext().prec = 10 

from config_eth import (
    API_KEY, API_SECRET, TRADING_PAIR, 
    NUM_GRID_LEVELS, MIN_PROFIT_GAP_PERCENT, ATR_WINDOW, 
    ATR_INTERVAL_MINUTES, ATR_MULTIPLIER, ESTIMATED_FEE_RATE, 
    BUDGET_UTILIZATION_CAP
)
from utils_eth import setup_logging, get_base_quote_assets, resolve_kraken_symbol, get_current_price

# File path where the currently managed TXIDs (orders) are stored.
MONITOR_FILE = 'txid.txt'

# Price precision for the Quote Asset (CAD in ETHCAD pair)
# This is derived from the Kraken error message ("up to 2 decimals")
CAD_PRICE_PRECISION = Decimal('0.01')


def get_account_balances(user_client: User, base_asset: str, quote_asset: str) -> Tuple[Decimal, Decimal]:
    """
    Fetches the available (free) balance for the required assets.
    """
    try:
        logging.info(f"Checking balances for {base_asset} and {quote_asset}...")
        
        # Use the User client method for account balance
        balances = user_client.get_account_balance() 
        
        base_balance = Decimal('0')
        quote_balance = Decimal('0')

        # Map common asset names to Kraken's format for reliable lookup
        kraken_asset_map = {
            'XBT': 'XXBT', 'BTC': 'XXBT', 
            'USD': 'ZUSD', 'EUR': 'ZEUR', 
            'ETH': 'XETH', 'USDT': 'USDT',
            'CAD': 'ZCAD' # Ensure CAD is mapped correctly
        }
        
        base_keys = {base_asset, kraken_asset_map.get(base_asset, base_asset), base_asset.replace('X', '').replace('Z', '')}
        quote_keys = {quote_asset, kraken_asset_map.get(quote_asset, quote_asset), quote_asset.replace('X', '').replace('Z', '')}
        
        for key, balance_str in balances.items():
            balance = Decimal(balance_str)
            clean_key = key.replace('X', '').replace('Z', '')
            
            if key in base_keys or clean_key in base_keys:
                base_balance = balance
            elif key in quote_keys or clean_key in quote_keys:
                quote_balance = balance

        logging.info(f"Available {base_asset} (Sell Budget): {base_balance:.8f}")
        logging.info(f"Available {quote_asset} (Buy Budget): {quote_balance:.2f}")

        return base_balance, quote_balance

    except Exception as e:
        logging.error(f"Error fetching account balances: {e}")
        return Decimal('0'), Decimal('0')


def calculate_dynamic_order_volumes(
    user_client: User, 
    current_price: Decimal
) -> Tuple[Decimal, Decimal]:
    """
    Calculates separate, proportional order volumes for Buy and Sell orders 
    based on the current asset imbalance.
    
    Returns: (buy_volume_per_order, sell_volume_per_order) in terms of Base Asset (ETH)
    """
    base_asset, quote_asset = get_base_quote_assets(TRADING_PAIR)
    
    # Base balance is for SELL orders (Base Asset), Quote balance is for BUY orders (Quote Asset)
    base_balance, quote_balance = get_account_balances(user_client, base_asset, quote_asset)

    DEC_NUM_GRID_LEVELS = Decimal(NUM_GRID_LEVELS)
    
    # Use the configured utilization cap (e.g., 99%)
    BUDGET_MULTIPLIER = BUDGET_UTILIZATION_CAP 

    # 1. Calculate the working budget in terms of USD-value, applying the cap
    working_quote_usd = quote_balance * BUDGET_MULTIPLIER
    working_base_usd = (base_balance * current_price) * BUDGET_MULTIPLIER
    
    total_portfolio_usd = working_quote_usd + working_base_usd
    
    if total_portfolio_usd <= Decimal('0'):
        logging.warning("Total portfolio value is zero. Cannot place orders.")
        return Decimal('0'), Decimal('0')

    # 2. Calculate the USD value per order for each side
    usd_per_buy_order = working_quote_usd / DEC_NUM_GRID_LEVELS
    usd_per_sell_order = working_base_usd / DEC_NUM_GRID_LEVELS
    
    # 3. Convert USD value per order back to Base Asset (ETH) Volume
    # BUY volume: USD budget / current price
    buy_volume_per_order = (usd_per_buy_order / current_price).quantize(Decimal('1e-8'), rounding=ROUND_DOWN)
    
    # SELL volume: USD budget / current price
    sell_volume_per_order = (usd_per_sell_order / current_price).quantize(Decimal('1e-8'), rounding=ROUND_DOWN)
    
    
    # Logging the result for transparency
    buy_bias = working_quote_usd / total_portfolio_usd
    sell_bias = working_base_usd / total_portfolio_usd
    
    logging.critical(f"--- PROPORTIONAL VOLUME CALCULATION ---")
    logging.critical(f"Total Working Portfolio Value (USD): {total_portfolio_usd:.2f}")
    logging.critical(f"Quote Asset % (Buy Side Budget): {buy_bias * Decimal('100'):.2f}%")
    logging.critical(f"Base Asset % (Sell Side Budget): {sell_bias * Decimal('100'):.2f}%")
    
    base_asset, _ = get_base_quote_assets(TRADING_PAIR)
    logging.critical(f"FINAL BUY volume per order: {buy_volume_per_order:.8f} {base_asset}")
    logging.critical(f"FINAL SELL volume per order: {sell_volume_per_order:.8f} {base_asset}")

    return buy_volume_per_order, sell_volume_per_order


def calculate_dynamic_gap_percent(market_client: Market, current_price: Decimal) -> Decimal:
    """
    Calculates the dynamic grid gap based on ATR percentage using OHLC data.
    """
    
    DEC_ATR_WINDOW = Decimal(ATR_WINDOW)
    DEC_ATR_MULTIPLIER = Decimal(ATR_MULTIPLIER)
    DEC_MIN_PROFIT_GAP_PERCENT = Decimal(MIN_PROFIT_GAP_PERCENT)
    
    try:
        interval_str = str(ATR_INTERVAL_MINUTES)
        ohlc_response = market_client.get_ohlc(pair=TRADING_PAIR, interval=interval_str)
        
        # Resolve the pair key
        pair_key = resolve_kraken_symbol(ohlc_response, TRADING_PAIR)
        if not pair_key:
            logging.warning("Failed to resolve trading pair for OHLC data. Falling back to MIN_PROFIT_GAP_PERCENT.")
            return DEC_MIN_PROFIT_GAP_PERCENT

        candles = ohlc_response.get(pair_key, [])
        
        required_candles = int(DEC_ATR_WINDOW) + 1
        
        if len(candles) < required_candles:
            logging.warning(f"Not enough OHLC data ({len(candles)}/{required_candles} required). Falling back to MIN_PROFIT_GAP_PERCENT.")
            return DEC_MIN_PROFIT_GAP_PERCENT

        # Only use the most recent, relevant candles for the ATR calculation
        relevant_candles = candles[-required_candles:]
        true_ranges = []
        
        for i in range(1, len(relevant_candles)):
            # OHLC format: [time, open, high, low, close, vwap, volume, count]
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
        
        # Apply the user-defined multiplier
        dynamic_gap = atr_percent * DEC_ATR_MULTIPLIER 
        
        # Use the higher of the calculated gap or the configured minimum gap
        final_gap = max(dynamic_gap, DEC_MIN_PROFIT_GAP_PERCENT)
        
        logging.info(f"ATR ({ATR_INTERVAL_MINUTES}m, {ATR_WINDOW} period): ${atr_value:.2f} ({atr_percent * Decimal('100'):.4f}%)")
        logging.info(f"Dynamic Gap (ATR * Multiplier): {dynamic_gap * Decimal('100'):.4f}%")
        logging.critical(f"Final Grid Gap: {final_gap * Decimal('100'):.4f}% (Min: {DEC_MIN_PROFIT_GAP_PERCENT * Decimal('100'):.4f}%)")
        
        return final_gap

    except Exception as e:
        logging.error(f"Error during ATR calculation: {e}. Falling back to MIN_PROFIT_GAP_PERCENT.")
        return DEC_MIN_PROFIT_GAP_PERCENT


def cancel_all_open_orders(trade_client: Trade, user_client: User, pair: str) -> bool:
    """
    Cancels all currently open orders ONLY for the specified trading pair.
    """
    try:
        logging.info(f"Attempting to cancel open orders for PAIR: {pair}...")
        
        # 1. Fetch ALL open orders using the USER CLIENT
        response = user_client.get_open_orders() 
        
        if response.get('error'):
            logging.error(f"Kraken API error when fetching open orders: {response['error']}")
            return False
        
        cancellation_count = 0
        orders_to_process = response.get('open', {}) 
        
        # 2. Iterate and cancel ONLY the matching pair
        for txid, order_info in orders_to_process.items():
            descr = order_info.get('descr')
            
            if descr:
                order_pair = descr.get('pair')
            else:
                continue 
            
            if order_pair == pair:
                # Use the trade client for cancellation
                trade_client.cancel_order(txid) 
                cancellation_count += 1
                logging.info(f"Canceled Order {txid} for pair {pair}.")
        
        logging.critical(f"Successfully canceled {cancellation_count} orders for {pair}.")
        return True

    except Exception as e:
        logging.error(f"Error during pair-specific order cancellation: {e}")
        return False


def execute_initial_order(trade_client: Trade, side: str, price: Decimal, volume: Decimal) -> str | bool | None:
    """
    Places a single limit order using the confirmed 'create_order' method.
    Returns: 
    - txid (str) on success.
    - False (bool) if the exchange is in cancel-only mode (fatal).
    - None for all other failures (non-fatal, like insufficient funds or invalid price).
    """
    
    # Kraken's minimum trade volume is typically 0.0001 ETH
    MIN_VOLUME = Decimal('0.0001') 
    volume_to_trade = volume.quantize(Decimal('1e-8'), rounding=ROUND_DOWN)

    if volume_to_trade < MIN_VOLUME:
        logging.warning(f"Skipping {side.upper()} order at ${price:.8f}: Calculated volume {volume_to_trade:.8f} is below minimum ({MIN_VOLUME:.8f}).")
        return None
        
    try:
        # --- FIX 1: Quantize price to 2 decimal places for CAD/Quote Asset ---
        quantized_price = price.quantize(CAD_PRICE_PRECISION, rounding=ROUND_DOWN)
        price_str = f"{quantized_price}"
        volume_str = f"{volume_to_trade:.8f}"
        
        logging.info(f"Placing initial {side.upper()} order @ ${price_str} (Vol: {volume_str} ETH)...")
        
        order = trade_client.create_order( 
            pair=TRADING_PAIR, 
            side=side, 
            ordertype='limit', 
            volume=volume_str, 
            price=price_str
        )

        txid = order.get('txid', [None])[0]
        if txid:
            logging.critical(f"Order successful. TXID: {txid}")
            return txid
        else:
            error_details = order.get('error', ['Unknown error'])
            
            # Specific error handling for market restrictions (Fatal error)
            if any('EService:Market in cancel_only mode' in err for err in error_details):
                 logging.critical(f"🛑 CRITICAL: Failed to place order. The market for {TRADING_PAIR} is currently in **CANCEL-ONLY MODE** on Kraken. New orders are temporarily blocked by the exchange.")
                 return False # Sentinel value for fatal error
            
            # Specific error handling for insufficient funds (Non-fatal, just skip)
            if any('Insufficient funds' in err for err in error_details):
                 logging.error(f"🔥 Error on order placement: Client does not have the necessary funds. Details: {error_details}")
                 return None 
            
            # Handle the invalid price error or any other general API error
            logging.error(f"Failed to place {side} order. Details: {error_details}")
            return None

    except Exception as e:
        logging.error(f"Error placing initial order: {e}")
        return None

def clear_and_save_managed_orders(managed_orders: dict):
    """Writes the current set of active orders to the MONITOR_FILE."""
    try:
        with open(MONITOR_FILE, 'w') as f:
            for txid, data in managed_orders.items():
                f.write(f"{txid},{data['side']},{data['price']:.8f},{data['volume']:.8f}\n")
        logging.critical(f"Initial grid saved to {MONITOR_FILE}. Total orders: {len(managed_orders)}")
    except Exception as e:
        logging.error(f"Error saving managed orders to {MONITOR_FILE}: {e}")

# --- MAIN LOGIC ---

def run_initializer():
    """Initializes the grid bot by placing the first set of orders, utilizing proportional funds."""
    
    # Initialize clients
    trade_client = Trade(key=API_KEY, secret=API_SECRET)
    market_client = Market()
    user_client = User(key=API_KEY, secret=API_SECRET) 
    
   # 1. CANCEL EXISTING ORDERS and wait
    cancel_all_open_orders(trade_client, user_client, TRADING_PAIR) 
    logging.info("Pausing for 5 seconds to ensure cancellation is processed by exchange...")
    time.sleep(5) 
    
    # 2. Get current price
    current_price = get_current_price(market_client)
    if not current_price:
        logging.critical("Cannot initialize bot without current price. Exiting.")
        return

    # 3. Determine base and quote assets
    base_asset, quote_asset = get_base_quote_assets(TRADING_PAIR)

    # 4. Get available balances (for logging and initial fund check)
    base_balance, quote_balance = get_account_balances(user_client, base_asset, quote_asset)

    if base_balance == Decimal('0') and quote_balance == Decimal('0'):
        logging.critical("No available funds in either base or quote currency. Cannot place grid orders. Exiting.")
        return
        
    # 5. Calculate the dynamic gap
    grid_gap_percent = calculate_dynamic_gap_percent(market_client, current_price)
    
   # 6. Calculate Volume per Order using the PROPORTIONAL BUDGET ALLOCATION logic
    buy_volume_per_order, sell_volume_per_order = calculate_dynamic_order_volumes(
        user_client, current_price
    )
        
    if buy_volume_per_order <= Decimal('0') and sell_volume_per_order <= Decimal('0'):
        logging.critical("Calculated volume is zero for both sides. Check proportional calculation logic. Exiting.")
        return
        
    # 7. Place new grid orders
    managed_orders = {}
    
    logging.critical(f"Attempting to place {NUM_GRID_LEVELS * 2} orders...")

    for i in range(1, NUM_GRID_LEVELS + 1):
        deviation_percent = grid_gap_percent * Decimal(i)
        
        # BUY ORDER (Steps down from current price) - Uses proportional buy_volume
        buy_price = current_price * (Decimal('1') - deviation_percent)
        # buy_txid will be a string (success), False (fatal error), or None (other failure)
        buy_txid = execute_initial_order(trade_client, 'buy', buy_price, buy_volume_per_order)
        
        # --- FIX 2: Check for fatal cancel-only mode error using the sentinel value ---
        if buy_txid is False: 
             return
             
        if buy_txid:
            managed_orders[buy_txid] = {'side': 'buy', 'price': buy_price, 'volume': buy_volume_per_order}

        # SELL ORDER (Steps up from current price) - Uses proportional sell_volume
        sell_price = current_price * (Decimal('1') + deviation_percent)
        sell_txid = execute_initial_order(trade_client, 'sell', sell_price, sell_volume_per_order)
        
        # Check for fatal cancel-only mode error using the sentinel value
        if sell_txid is False:
             return

        if sell_txid:
            managed_orders[sell_txid] = {'side': 'sell', 'price': sell_price, 'volume': sell_volume_per_order}
    
    # 8. Save the active orders to the monitor file
    clear_and_save_managed_orders(managed_orders)


if __name__ == "__main__":
    # Minimal logging setup for quick feedback
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
    run_initializer()