import logging
from kraken.spot import Trade, Market, User
from decimal import Decimal, ROUND_DOWN, getcontext
import os
import sys
import time 
from typing import Tuple, Dict, Union

# Set precision for Decimal operations to handle small trade sizes
getcontext().prec = 10 

from config import (
    API_KEY, API_SECRET, TRADING_PAIR, 
    NUM_GRID_LEVELS, MIN_PROFIT_GAP_PERCENT, ATR_WINDOW, 
    ATR_INTERVAL_MINUTES, ATR_MULTIPLIER, ESTIMATED_FEE_RATE, 
    BUDGET_UTILIZATION_CAP
)
from utils import setup_logging, get_base_quote_assets, resolve_kraken_symbol, get_current_market_prices

# File path where the currently managed TXIDs (orders) are stored.
MONITOR_FILE = 'txid.txt'

# --- QUANTIZATION CONSTANTS ---

# PRICE PRECISION FIX: Changed from Decimal('0.01') to Decimal('0.1') based on the error 
# message: "BTC/USD price can only be specified up to 1 decimals."
# This is crucial to avoid "too many decimal points for prices" errors.
CAD_PRICE_PRECISION = Decimal('0.1') 

# Volume precision for the Base Asset (e.g., ETH/XBT: 8 decimal places)
# This is crucial to avoid "EOrder:Trading minimum not met" or precision errors.
BASE_ASSET_VOLUME_PRECISION = Decimal('1e-8') 

# --- Helper Functions for Managed Orders (Initialization Persistence) ---

def load_managed_orders() -> Dict[str, Dict[str, Union[str, Decimal]]]:
    """Loads active orders (txid, side, price, volume) from the monitor file."""
    # This is kept for consistency with the file format, although only used 
    # for clear_and_save_managed_orders in the initializer.
    managed_orders = {}
    if not os.path.exists(MONITOR_FILE):
        return managed_orders

    try:
        with open(MONITOR_FILE, 'r') as f:
            for line in f:
                parts = line.strip().split(',')
                # Expecting format: TXID, SIDE, PRICE, VOLUME
                if len(parts) == 4:
                    txid, side, price_str, volume_str = parts
                    managed_orders[txid] = {
                        'side': side,
                        # Load as Decimal objects for calculation consistency
                        'price': Decimal(price_str), 
                        'volume': Decimal(volume_str) 
                    }
        return managed_orders
    except Exception as e:
        logging.error(f"Error loading managed orders from {MONITOR_FILE}: {e}")
        return {}

def clear_and_save_managed_orders(managed_orders: dict):
    """Writes the current set of active orders to the MONITOR_FILE (for a separate monitor process)."""
    try:
        with open(MONITOR_FILE, 'w') as f:
            for txid, data in managed_orders.items():
                # Ensure the price and volume are saved with enough precision
                f.write(f"{txid},{data['side']},{data['price']:.8f},{data['volume']:.8f}\n") 
        logging.info(f"Managed order list saved. Total orders: {len(managed_orders)}")
    except Exception as e:
        logging.error(f"Error saving managed orders to {MONITOR_FILE}: {e}")

# --- Core Trading Functions (Strictly adhering to existing Kraken API calls) ---

def get_account_balances(user_client: User, base_asset: str, quote_asset: str) -> Tuple[Decimal, Decimal]:
    """Fetches the available (free) balance for the required assets using user_client.get_account_balance."""
    try:
        logging.info(f"Checking balances for {base_asset} and {quote_asset}...")
        balances = user_client.get_account_balance() 
        base_balance = Decimal('0')
        quote_balance = Decimal('0')

        # Map common asset names to Kraken's format for reliable lookup
        kraken_asset_map = {
            'XBT': 'XXBT', 'BTC': 'XXBT', 
            'USD': 'ZUSD', 'EUR': 'ZEUR', 
            'ETH': 'XETH', 'USDT': 'USDT',
            'CAD': 'ZCAD'
        }
        
        # Check against the raw name, the Kraken-mapped name, and the cleaned name
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


def calculate_dynamic_gap_percent(market_client: Market, current_price: Decimal) -> Decimal:
    """Calculates the dynamic grid gap based on ATR percentage using market_client.get_ohlc."""
    
    DEC_ATR_WINDOW = Decimal(ATR_WINDOW)
    DEC_ATR_MULTIPLIER = Decimal(ATR_MULTIPLIER)
    DEC_MIN_PROFIT_GAP_PERCENT = Decimal(MIN_PROFIT_GAP_PERCENT)
    
    try:
        interval_str = str(ATR_INTERVAL_MINUTES)
        ohlc_response = market_client.get_ohlc(pair=TRADING_PAIR, interval=interval_str)
        
        pair_key = resolve_kraken_symbol(ohlc_response, TRADING_PAIR)
        if not pair_key:
            return DEC_MIN_PROFIT_GAP_PERCENT

        candles = ohlc_response.get(pair_key, [])
        required_candles = int(DEC_ATR_WINDOW) + 1
        
        if len(candles) < required_candles:
            logging.warning(f"Not enough OHLC data ({len(candles)}/{required_candles} needed) for ATR. Using MIN_PROFIT_GAP_PERCENT.")
            return DEC_MIN_PROFIT_GAP_PERCENT

        relevant_candles = candles[-required_candles:]
        true_ranges = []
        
        # Calculate True Range for the last N bars
        for i in range(1, len(relevant_candles)):
            high = Decimal(relevant_candles[i][2])
            low = Decimal(relevant_candles[i][3])
            prev_close = Decimal(relevant_candles[i-1][4])
            
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            
            true_ranges.append(max(tr1, tr2, tr3))

        # Calculate ATR (Average True Range)
        atr_value = sum(true_ranges) / DEC_ATR_WINDOW 
        # Convert ATR to a percentage of the current price
        atr_percent = (atr_value / current_price)
        # Apply the user-defined multiplier
        dynamic_gap = atr_percent * DEC_ATR_MULTIPLIER 
        # Enforce the minimum profit gap
        final_gap = max(dynamic_gap, DEC_MIN_PROFIT_GAP_PERCENT)
        
        logging.critical(f"Dynamic Grid Gap: {dynamic_gap * Decimal('100'):.4f}% | Final Grid Gap: {final_gap * Decimal('100'):.4f}%")
        
        return final_gap

    except Exception as e:
        logging.error(f"Error during ATR calculation: {e}. Falling back to MIN_PROFIT_GAP_PERCENT.")
        return DEC_MIN_PROFIT_GAP_PERCENT


def execute_initial_order(trade_client: Trade, side: str, price: Decimal, volume: Decimal) -> str | bool | None:
    """
    Places a single limit order using trade_client.create_order.
    Returns: txid (str) on success, False on fatal error, None on non-fatal error.
    """
    
    # 1. Quantize Volume (Base Asset)
    MIN_VOLUME = BASE_ASSET_VOLUME_PRECISION # Assume minimum is the quantization unit
    volume_to_trade = volume.quantize(BASE_ASSET_VOLUME_PRECISION, rounding=ROUND_DOWN)

    if volume_to_trade < MIN_VOLUME:
        logging.warning(f"Skipping {side.upper()} order at ${price:.8f}: Calculated volume {volume_to_trade:.8f} is below minimum ({MIN_VOLUME:.8f}).")
        return None
        
    try:
        # 2. Quantize Price (Quote Asset)
        # The price is quantized here using the dynamically determined precision (now Decimal('0.1'))
        quantized_price = price.quantize(CAD_PRICE_PRECISION, rounding=ROUND_DOWN)
        
        # Convert to string format for the API call
        price_str = f"{quantized_price}"
        volume_str = f"{volume_to_trade}" # Use the quantized Decimal string representation
        
        logging.info(f"Placing {side.upper()} order @ ${price_str} (Vol: {volume_str} Base Asset)...")
        
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
            if any('EService:Market in cancel_only mode' in err for err in error_details):
                 logging.critical(f"🛑 CRITICAL: Failed to place order. Market in CANCEL-ONLY MODE. Terminating.")
                 return False
            if any('Insufficient funds' in err for err in error_details):
                 logging.error(f"🔥 Error on order placement: Insufficient funds. Details: {error_details}")
                 return None 
            
            # Catch the specific precision error for debugging purposes
            if any('EOrder:Invalid price' in err or 'EOrder:Invalid volume' in err for err in error_details):
                logging.error(f"🔥 PRICE/VOLUME PRECISION ERROR: Ensure CAD_PRICE_PRECISION and BASE_ASSET_VOLUME_PRECISION in Bot.py are correct for {TRADING_PAIR}. Details: {error_details}")
            
            logging.error(f"Failed to place {side} order. Details: {error_details}")
            return None

    except Exception as e:
        logging.error(f"Error placing order: {e}")
        return None

# --- Initializer Helper Logic (Volume Calculation) ---

def calculate_dynamic_order_volumes(
    user_client: User, 
    mid_price: Decimal 
) -> Tuple[Decimal, Decimal]:
    """
    Calculates separate, proportional order volumes for Buy and Sell orders 
    based on the available base and quote asset balances. Uses get_account_balances.
    """
    base_asset, quote_asset = get_base_quote_assets(TRADING_PAIR)
    base_balance, quote_balance = get_account_balances(user_client, base_asset, quote_asset)

    DEC_NUM_GRID_LEVELS = Decimal(NUM_GRID_LEVELS)
    BUDGET_MULTIPLIER = BUDGET_UTILIZATION_CAP 

    # Calculate the working budget in USD (Quote Asset)
    working_quote_usd = quote_balance * BUDGET_MULTIPLIER
    # Calculate the working budget in USD equivalent (Base Asset)
    working_base_usd = (base_balance * mid_price) * BUDGET_MULTIPLIER
    total_portfolio_usd = working_quote_usd + working_base_usd
    
    if total_portfolio_usd <= Decimal('0'):
        logging.warning("Total portfolio value is zero. Cannot place orders.")
        return Decimal('0'), Decimal('0')

    # Calculate the USD value per order for each side
    usd_per_buy_order = working_quote_usd / DEC_NUM_GRID_LEVELS
    usd_per_sell_order = working_base_usd / DEC_NUM_GRID_LEVELS
    
    # Convert USD value per order back to Base Asset (ETH/XBT) Volume
    # Quantize immediately after division to base asset precision
    buy_volume_per_order = (usd_per_buy_order / mid_price).quantize(BASE_ASSET_VOLUME_PRECISION, rounding=ROUND_DOWN)
    sell_volume_per_order = (usd_per_sell_order / mid_price).quantize(BASE_ASSET_VOLUME_PRECISION, rounding=ROUND_DOWN)
    
    buy_bias = working_quote_usd / total_portfolio_usd
    sell_bias = working_base_usd / total_portfolio_usd
    
    logging.critical(f"--- PROPORTIONAL VOLUME CALCULATION ---")
    logging.critical(f"Quote Asset % (Buy Side Budget): {buy_bias * Decimal('100'):.2f}%")
    logging.critical(f"Base Asset % (Sell Side Budget): {sell_bias * Decimal('100'):.2f}%")
    
    base_asset, _ = get_base_quote_assets(TRADING_PAIR)
    logging.critical(f"FINAL BUY volume per order: {buy_volume_per_order} {base_asset}")
    logging.critical(f"FINAL SELL volume per order: {sell_volume_per_order} {base_asset}")

    return buy_volume_per_order, sell_volume_per_order

# --- Utility Function (Strictly Adhering to Kraken API calls) ---

def cancel_all_open_orders(trade_client: Trade, user_client: User, pair: str) -> bool:
    """Cancels all currently open orders ONLY for the specified trading pair. Uses user_client.get_open_orders and trade_client.cancel_order."""
    try:
        logging.info(f"Attempting to cancel open orders for PAIR: {pair}...")
        response = user_client.get_open_orders() 
        if response.get('error'):
            logging.error(f"Kraken API error when fetching open orders: {response['error']}")
            return False
        
        cancellation_count = 0
        orders_to_process = response.get('open', {}) 
        
        for txid, order_info in orders_to_process.items():
            descr = order_info.get('descr')
            
            if descr:
                order_pair = descr.get('pair')
            else:
                continue 
            
            # Match the order pair exactly to the TRADING_PAIR
            if order_pair == pair:
                trade_client.cancel_order(txid) 
                cancellation_count += 1
                logging.info(f"Canceled Order {txid} for pair {pair}.")
        
        logging.critical(f"Successfully canceled {cancellation_count} orders for {pair}.")
        return True

    except Exception as e:
        logging.error(f"Error during pair-specific order cancellation: {e}")
        return False


# --- PRIMARY INITIALIZER FUNCTION ---

def run_initializer():
    """Initializes the grid bot by placing the first set of orders, anchoring on Bid/Ask."""
    
    trade_client = Trade(key=API_KEY, secret=API_SECRET)
    market_client = Market()
    user_client = User(key=API_KEY, secret=API_SECRET) 
    
    # 1. Clean Slate: Cancel all existing open orders for the trading pair
    cancel_all_open_orders(trade_client, user_client, TRADING_PAIR) 
    time.sleep(5) 
    
    prices = get_current_market_prices(market_client, TRADING_PAIR)
    if not prices:
        logging.critical("Cannot initialize bot without current market prices. Exiting.")
        return
    
    mid_price = prices['mid']
    bid_price = prices['bid'] # Anchor for Buy orders
    ask_price = prices['ask'] # Anchor for Sell orders

    base_asset, quote_asset = get_base_quote_assets(TRADING_PAIR)
    base_balance, quote_balance = get_account_balances(user_client, base_asset, quote_asset)

    if base_balance == Decimal('0') and quote_balance == Decimal('0'):
        logging.critical("No available funds. Cannot place grid orders. Exiting.")
        return
        
    # 2. Dynamic Gap Calculation
    grid_gap_percent = calculate_dynamic_gap_percent(market_client, mid_price)
    
    # 3. Proportional Volume Calculation
    buy_volume_per_order, sell_volume_per_order = calculate_dynamic_order_volumes(
        user_client, mid_price
    )
        
    if buy_volume_per_order <= Decimal('0') and sell_volume_per_order <= Decimal('0'):
        logging.critical("Calculated volume is zero for both sides. Exiting.")
        return
        
    managed_orders = {}
    
    logging.critical(f"Attempting to place {NUM_GRID_LEVELS * 2} orders...")

    # 4. Grid Placement Loop
    for i in range(1, NUM_GRID_LEVELS + 1):
        deviation_percent = grid_gap_percent * Decimal(i)
        
        # BUY ORDER: Steps down from the current BID price (Anchor for buy grid)
        buy_price = bid_price * (Decimal('1') - deviation_percent)
        buy_txid = execute_initial_order(trade_client, 'buy', buy_price, buy_volume_per_order)
        
        if buy_txid is False: return # Stop on fatal error
             
        if buy_txid:
            # Note: We save the *unquantized* price here for calculation fidelity, 
            # but the order placed used the quantized price.
            managed_orders[buy_txid] = {'side': 'buy', 'price': buy_price, 'volume': buy_volume_per_order}

        # SELL ORDER: Steps up from the current ASK price (Anchor for sell grid)
        sell_price = ask_price * (Decimal('1') + deviation_percent)
        sell_txid = execute_initial_order(trade_client, 'sell', sell_price, sell_volume_per_order)
        
        if sell_txid is False: return # Stop on fatal error

        if sell_txid:
            # Note: We save the *unquantized* price here for calculation fidelity, 
            # but the order placed used the quantized price.
            managed_orders[sell_txid] = {'side': 'sell', 'price': sell_price, 'volume': sell_volume_per_order}
    
    # 5. Save the initial grid orders to the monitor file
    clear_and_save_managed_orders(managed_orders)
    logging.critical("✅ Initialization complete. Grid is placed and saved to txid.txt.")


if __name__ == "__main__":
    # Minimal logging setup for quick feedback
    setup_logging() 
    
    # This file is strictly for initialization. It clears any previous grid file 
    # if it exists, and always runs the initializer.
    if os.path.exists(MONITOR_FILE):
        logging.warning(f"Existing grid file '{MONITOR_FILE}' found. It will be overwritten by the new grid.")
        
    logging.critical("Running Initializer to place the first grid.")
    run_initializer()