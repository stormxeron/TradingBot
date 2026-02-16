import logging
import os
from kraken.spot import Market, User
from decimal import Decimal, getcontext
import json
import datetime
import time
from typing import Tuple, Dict

# Set precision for Decimal operations
getcontext().prec = 10 

# Assuming these files are in the same directory
try:
    from config import API_KEY, API_SECRET, TRADING_PAIR, ESTIMATED_FEE_RATE
    from utils import setup_logging, get_base_quote_assets, get_current_price
except ImportError as e:
    print(f"Error importing configuration/utilities: {e}")
    print("Ensure config_eth.py and utils_eth.py are present and correctly configured.")
    exit(1)


# File to store the total portfolio value at the time the bot was initialized.
BASELINE_FILE = 'portfolio_baseline.txt'

# --- Utility Functions (Duplicated/Simplified from Bot.py for clarity/standalone run) ---

def get_account_balances(user_client: User, base_asset: str, quote_asset: str) -> Tuple[Decimal, Decimal]:
    """Fetches the available balance for the required assets."""
    try:
        balances = user_client.get_account_balance() 
        base_balance = Decimal('0')
        quote_balance = Decimal('0')

        kraken_asset_map = {
            'XBT': 'XXBT', 'BTC': 'XXBT', 'USD': 'ZUSD', 'EUR': 'ZEUR', 
            'ETH': 'XETH', 'USDT': 'USDT', 'CAD': 'ZCAD'
        }
        
        base_keys = {base_asset, kraken_asset_map.get(base_asset, base_asset)}
        quote_keys = {quote_asset, kraken_asset_map.get(quote_asset, quote_asset)}
        
        for key, balance_str in balances.items():
            balance = Decimal(balance_str)
            if key in base_keys:
                base_balance = balance
            elif key in quote_keys:
                quote_balance = balance
            # Add checks for cleaned keys (e.g., XETH vs ETH)
            elif base_asset in key: base_balance = balance 
            elif quote_asset in key: quote_balance = balance 

        return base_balance, quote_balance

    except Exception as e:
        logging.error(f"Error fetching account balances: {e}")
        return Decimal('0'), Decimal('0')


# --- Baseline Logic ---

def load_or_set_baseline(user_client: User, current_price: Decimal, quote_asset: str) -> Tuple[Decimal, datetime.datetime]:
    """Loads the baseline portfolio value and start time, or sets it if the file doesn't exist."""
    
    # 1. Load Start Time (We assume the first run time is the bot's start time)
    start_time = datetime.datetime.fromtimestamp(0) # Default to epoch
    if os.path.exists(BASELINE_FILE):
        with open(BASELINE_FILE, 'r') as f:
            try:
                data = json.load(f)
                baseline_value = Decimal(data['value'])
                start_timestamp = Decimal(data['timestamp'])
                start_time = datetime.datetime.fromtimestamp(float(start_timestamp))
                logging.info(f"Loaded baseline portfolio value: {baseline_value:.2f} {quote_asset} from {start_time}")
                return baseline_value, start_time
            except (json.JSONDecodeError, KeyError, ValueError):
                logging.warning("Baseline file corrupted. Recalculating and overwriting.")
    
    # 2. Set Baseline (First run)
    base_asset, _, _ = get_base_quote_assets(TRADING_PAIR)
    base_balance, quote_balance = get_account_balances(user_client, base_asset, quote_asset)
    
    initial_value = quote_balance + (base_balance * current_price)
    current_timestamp = Decimal(time.time())
    start_time = datetime.datetime.fromtimestamp(float(current_timestamp))

    data = {
        'value': str(initial_value),
        'timestamp': str(current_timestamp)
    }

    with open(BASELINE_FILE, 'w') as f:
        json.dump(data, f, indent=4)
        
    logging.critical(f"FIRST RUN: Baseline portfolio value set to {initial_value:.2f} {quote_asset} at {start_time}")
    return initial_value, start_time

# --- P&L Calculation Logic ---

def calculate_realized_profit(user_client: User, start_time: datetime.datetime, pair_symbol: str) -> Decimal:
    """
    Fetches trade history since the bot started and calculates the total realized profit 
    by pairing up Buys and Sells using a First-In, First-Out (FIFO) method.
    
    This function assumes all trades for the specified pair after 'start_time' are 
    part of the bot's grid cycles.
    """
    try:
        # Get start timestamp in seconds
        start_ts = int(start_time.timestamp()) 
        
        # 1. Fetch trade history since the bot started
        trades_response = user_client.get_trades_history(start=start_ts)
        
        # Kraken returns a dictionary {txid: trade_details}. We need to convert it 
        # to a list and sort by time to ensure FIFO processing.
        raw_trades = trades_response.get('trades', {})
        
        # Filter trades for the specific pair and sort chronologically
        sorted_trades = []
        for txid, trade in raw_trades.items():
            # FIX: Removed the 'status' check, as trades from this endpoint are already executed.
            if trade['pair'] == pair_symbol: 
            # Convert time to Decimal for sorting
                trade['time_decimal'] = Decimal(trade['time']) 
                sorted_trades.append(trade)

        sorted_trades.sort(key=lambda x: x['time_decimal'])
        
        logging.info(f"Processing {len(sorted_trades)} closed trades since {start_time.strftime('%Y-%m-%d %H:%M')}")

        # 2. FIFO Tracking Structures
        
        # List to hold unmatched 'buy' inventory (base asset)
        # Each entry stores: volume (base), total_cost (quote), total_fee (quote)
        # Example: [{'v': Decimal('0.1'), 'c': Decimal('200'), 'f': Decimal('0.5')}]
        unmatched_buys: list[Dict[str, Decimal]] = []
        total_realized_profit = Decimal('0')
        
        # 3. Process Trades Chronologically (FIFO Matching)
        for trade in sorted_trades:
            trade_type = trade['type']
            volume = Decimal(trade['vol'])      # Base Asset (e.g., ETH)
            cost = Decimal(trade['cost'])        # Quote Asset (e.g., USD spent/received)
            fee = Decimal(trade['fee'])          # Fee (in Quote Asset)
            
            if trade_type == 'buy':
                # Add the purchase to the inventory list
                unmatched_buys.append({
                    'v': volume, 
                    'c': cost, 
                    'f': fee
                })
                
            elif trade_type == 'sell':
                remaining_sell_volume = volume
                
                # Match this sell against the oldest available buys (FIFO)
                while remaining_sell_volume > 0 and unmatched_buys:
                    oldest_buy = unmatched_buys[0]
                    buy_volume = oldest_buy['v']
                    
                    # Determine the volume matched in this cycle segment
                    match_volume = min(remaining_sell_volume, buy_volume)
                    
                    # --- Calculate Prorated Buy Cost/Fee for the Matched Volume ---
                    # Buy Cost/Fee per unit volume
                    cost_per_unit = oldest_buy['c'] / oldest_buy['v']
                    fee_per_unit = oldest_buy['f'] / oldest_buy['v']
                    
                    prorated_buy_cost = cost_per_unit * match_volume
                    prorated_buy_fee = fee_per_unit * match_volume

                    # --- Calculate Sell Revenue/Fee for the Matched Volume ---
                    # Sell Revenue per unit volume
                    sell_revenue_per_unit = cost / volume
                    sell_fee_per_unit = fee / volume
                    
                    prorated_sell_revenue = sell_revenue_per_unit * match_volume
                    prorated_sell_fee = sell_fee_per_unit * match_volume
                    
                    # --- Calculate Realized Profit for the Cycle Segment ---
                    # Realized P&L = (Net Sell Revenue) - (Net Buy Expenditure)
                    # Realized P&L = (Prorated Sell Revenue - Prorated Sell Fee) - (Prorated Buy Cost + Prorated Buy Fee)
                    
                    realized_segment_pnl = (prorated_sell_revenue - prorated_sell_fee) - (prorated_buy_cost + prorated_buy_fee)
                    
                    total_realized_profit += realized_segment_pnl
                    
                    # --- Update Tracking ---
                    remaining_sell_volume -= match_volume
                    oldest_buy['v'] -= match_volume
                    
                    # If the oldest buy is fully consumed, remove it from the list
                    if oldest_buy['v'] <= Decimal('0'):
                        unmatched_buys.pop(0)

        # Log any remaining unmatched buys (open inventory)
        if unmatched_buys:
            remaining_volume = sum(b['v'] for b in unmatched_buys)
            logging.warning(f"Tracker finished with {remaining_volume:.8f} {pair_symbol.split('/')[0]} in unmatched (open) inventory. This inventory is accounted for in Market P&L.")
            
        return total_realized_profit

    except Exception as e:
        # Log the error but return 0 to prevent the entire tracker from failing
        logging.error(f"FATAL ERROR in advanced realized profit calculation: {e}")
        return Decimal('0')

# --- MAIN REPORTING FUNCTION ---

def run_portfolio_tracker():
    """Calculates and reports the current portfolio performance."""
    setup_logging()
    
    # Initialize clients
    try:
        market_client = Market()
        user_client = User(key=API_KEY, secret=API_SECRET) 
    except Exception as e:
        logging.error(f"Failed to initialize Kraken clients. Check API_KEY/SECRET. Error: {e}")
        return

    # FIX 1: Call get_current_price with only one argument (market_client)
    #        The function returns the single Decimal price directly.
    current_price = get_current_price(market_client)
    
    if not current_price:
        logging.error("Cannot fetch current market prices. Exiting tracker.")
        return
        
    # FIX 2: Capture all THREE return values from get_base_quote_assets.
    #        'base_asset' and 'quote_asset' are used for balances.
    #        'kraken_pair' is the API symbol (e.g., 'XETHZCAD') needed for trade history filtering.
    base_asset, quote_asset, kraken_pair = get_base_quote_assets(TRADING_PAIR)
    
    if not base_asset or not quote_asset or not kraken_pair:
        logging.error(f"FATAL: Failed to resolve asset codes for {TRADING_PAIR}. Exiting tracker.")
        return
    
    # 1. Load or set the initial baseline
    baseline_value, start_time = load_or_set_baseline(user_client, current_price, quote_asset)

    # 2. Get current holdings
    base_balance, quote_balance = get_account_balances(user_client, base_asset, quote_asset)
    
    # 3. Calculate Current Portfolio Value
    base_value_in_quote = base_balance * current_price
    current_value = quote_balance + base_value_in_quote

    # 4. Calculate P&L components
    total_pnl = current_value - baseline_value
    
    # FIX 3: Pass the Kraken API symbol (kraken_pair) to calculate_realized_profit
    #        This ensures the FIFO calculation correctly filters for trades named 'XETHZCAD', etc.
    realized_profit = calculate_realized_profit(user_client, start_time, kraken_pair)
    
    # Market Fluctuation P&L = Total P&L - Realized Profit (residual)
    market_pnl = total_pnl - realized_profit
    
    # 5. Generate Report
    
    # Helper to format P&L with sign
    def format_pnl(value: Decimal, total_pnl_base: Decimal = baseline_value) -> str:
        sign = "+" if value >= 0 else ""
        
        # Avoid division by zero if baseline is somehow 0
        percentage = Decimal('0.00')
        if total_pnl_base != 0:
            percentage = (value / total_pnl_base * Decimal('100'))
            
        return f"{sign}{value:.2f} {quote_asset} ({sign}{percentage:.2f}%)"

    report = f"""
=====================================================
📈 PORTFOLIO PERFORMANCE REPORT
=====================================================
TRADING PAIR: {TRADING_PAIR}
START DATE:   {start_time.strftime('%Y-%m-%d %H:%M:%S')}
DURATION:     {(datetime.datetime.now() - start_time)}

--- CURRENT ASSET HOLDINGS ---
{quote_asset} Balance (Cash): {quote_balance:.2f} {quote_asset}
{base_asset} Balance (Crypto): {base_balance:.8f} {base_asset}
Current Price:              {current_price:.2f} {quote_asset}

--- VALUE CALCULATION (in {quote_asset}) ---
Baseline Value (Start):     {baseline_value:.2f} {quote_asset}
Current Value:              {current_value:.2f} {quote_asset}
{base_asset} Holding Value:     {base_value_in_quote:.2f} {quote_asset}

--- P&L BREAKDOWN (Since Bot Start) ---
TOTAL P&L (UP/DOWN):        {format_pnl(total_pnl)}
-----------------------------------------------------
BOT-GENERATED P&L (Realized): {format_pnl(realized_profit)} 
MARKET P&L (Unrealized):    {format_pnl(market_pnl)} 
=====================================================
"""
    print(report)


if __name__ == "__main__":
    run_portfolio_tracker()