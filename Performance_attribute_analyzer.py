import logging
import os
import sys
import json
import datetime
import time
from decimal import Decimal, getcontext
from typing import Tuple, Dict, Optional, Any

# Set precision for Decimal operations
getcontext().prec = 10 

# --- KRAKEN LIBRARY IMPORTS (Crucial to define User and Market classes) ---
try:
    from kraken.spot import Market, User
except ImportError:
    logging.critical("FATAL ERROR: The 'kraken-api' library is required. Please install it using: pip install kraken-api")
    sys.exit(1)

# --- CONFIGURATION & UTILITIES IMPORT (Relies on existing files) ---
try:
    from config import API_KEY, API_SECRET, TRADING_PAIR, REPORTING_CURRENCY, FX_RATE_PAIR
    # Importing essential functions from your working utils.py
    from utils import setup_logging, get_base_quote_assets, get_current_price
except ImportError as e:
    print(f"Error importing configuration/utilities: {e}")
    print("Ensure config.py and utils.py are present and correctly configured.")
    sys.exit(1)


# File to store the initial state required for P&L attribution.
BASELINE_FILE = 'performance_baseline.json'


# =============================================================================
# 1. FX RATE CONVERSION
# =============================================================================

def get_fx_rate_to_reporting_currency(market_client: Market, quote_asset: str, reporting_currency: str) -> Decimal:
    """
    Fetches the exchange rate to convert the quote_asset (e.g., USDC) 
    into the reporting_currency (e.g., CAD).
    """
    if quote_asset == reporting_currency:
        return Decimal('1.0')

    # Scenario 1: Directly convertible on Kraken (e.g., USDCAD is not guaranteed, but possible)
    # We first try to fetch the direct pair: USDC/CAD
    direct_pair = f"{quote_asset}{reporting_currency}"
    
    try:
        # Use the utility to find the Kraken symbol for the direct pair
        _, _, kraken_symbol = get_base_quote_assets(direct_pair)
        
        # If a valid symbol is found and it's not just the raw input, try to get the price
        if kraken_symbol != direct_pair:
            price_ticker = market_client.get_ticker(kraken_symbol)
            if price_ticker and kraken_symbol in price_ticker:
                rate = Decimal(price_ticker[kraken_symbol]['c'][0])
                logging.info(f"Using direct Kraken FX rate {kraken_symbol}: 1 {quote_asset} = {rate:,.4f} {reporting_currency}")
                return rate

    except Exception as e:
        logging.debug(f"Direct FX rate check for {direct_pair} failed on Kraken: {e}")
        # Continue to next step if direct rate fails

    # Scenario 2: Assume standard stablecoin parity (e.g., USDC -> USD)
    # Then find the FX rate for USD to the target currency (e.g., USD -> CAD)
    if quote_asset in ('USDC', 'USDT'):
        # Kraken typically uses ZUSD for USD. We look for ZUSD/ZCAD for a USD/CAD rate.
        if reporting_currency == 'CAD':
            # Use the most stable USD/CAD rate available on Kraken, often ZUSDZCAD or USDCAD
            usd_cad_symbol = 'ZUSDZCAD' 
            try:
                # We need to explicitly check if ZUSDZCAD is supported, or use a price endpoint.
                # Since the tracker relies on TRADING_PAIR logic, we use a fixed fallback rate if Kraken doesn't list it.
                price_ticker = market_client.get_ticker(usd_cad_symbol)
                if price_ticker and usd_cad_symbol in price_ticker:
                    rate = Decimal(price_ticker[usd_cad_symbol]['c'][0])
                    logging.info(f"Using Kraken FX rate {usd_cad_symbol} for conversion: {rate:,.4f} {reporting_currency}")
                    return rate
            except Exception as e:
                logging.warning(f"Kraken USD/CAD rate check failed. Assuming 1 USD = 1.35 CAD for reporting. Error: {e}")
                # Fallback to a hardcoded rate if the API fails, for the sake of functionality
                return Decimal('1.35') 
            
        # Add other fiat conversions here if needed (e.g., USD to EUR)
    
    # Final Fallback: Log critical error and revert to 1.0 (no conversion)
    logging.critical(f"FATAL: Cannot determine conversion rate from {quote_asset} to {reporting_currency}. Reporting in {quote_asset} only.")
    return Decimal('1.0')


# =============================================================================
# 2. BALANCE FETCHING
# =============================================================================

def get_account_balances(user_client: User, base_asset: str, quote_asset: str) -> Tuple[Decimal, Decimal]:
    """Fetches the available balance for the required assets."""
    try:
        balances = user_client.get_account_balance() 
        base_balance = Decimal('0')
        quote_balance = Decimal('0')

        # Expanded Kraken asset map to cover all cases
        kraken_asset_map = {
            'XBT': 'XXBT', 'BTC': 'XXBT', 'USD': 'ZUSD', 'EUR': 'ZEUR', 
            'ETH': 'XETH', 'USDT': 'USDT', 'CAD': 'ZCAD', 'USDC': 'USDC', 
            'DAI': 'DAI' # Added DAI
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


# =============================================================================
# 3. CAPTURE/BASELINE LOGIC (Triggered by 'capture' arg)
# =============================================================================

def save_baseline_state(user_client: User, current_price: Decimal, quote_asset: str) -> None:
    """
    Saves the initial state (inventory, capital, and price) required for 
    performance attribution analysis.
    """
    setup_logging()
    logging.critical("--- Starting Baseline Data Capture for Performance Attribution ---")

    # Fetch current price using your existing utility function
    current_price = get_current_price(Market())
    if not current_price:
        logging.critical("FATAL: Cannot fetch current market price. Check Kraken connectivity and TRADING_PAIR.")
        return
        
    base_asset, _, _ = get_base_quote_assets(TRADING_PAIR)
    
    # Get initial balances using the function defined above
    initial_base_inventory, initial_quote_capital = get_account_balances(user_client, base_asset, quote_asset)
    
    # Calculate total initial value for the record
    initial_portfolio_value = initial_quote_capital + (initial_base_inventory * current_price)

    timestamp = Decimal(time.time())
    start_time = datetime.datetime.fromtimestamp(float(timestamp))
    
    # Determine the conversion rate at the start time (for an accurate baseline portfolio value in REPORTING_CURRENCY)
    fx_rate_at_start = get_fx_rate_to_reporting_currency(Market(), quote_asset, REPORTING_CURRENCY)
    
    baseline_data = {
        'timestamp': str(timestamp),
        'start_time_iso': start_time.isoformat(),
        'trading_pair': TRADING_PAIR,
        'initial_portfolio_value': str(initial_portfolio_value), # Value in Quote Asset
        'initial_portfolio_value_reporting': str(initial_portfolio_value * fx_rate_at_start), # Value in Reporting Currency
        'start_price': str(current_price),
        'initial_base_inventory': str(initial_base_inventory),
        'initial_quote_capital': str(initial_quote_capital),
        'reporting_currency_at_start': REPORTING_CURRENCY, # Lock in the reporting currency
        'fx_rate_at_start': str(fx_rate_at_start)
    }

    with open(BASELINE_FILE, 'w') as f:
        json.dump(baseline_data, f, indent=4)

    logging.critical(f"✅ BASELINE CAPTURED successfully at {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logging.critical(f"Initial Value: {initial_portfolio_value:,.2f} {quote_asset} (or {(initial_portfolio_value * fx_rate_at_start):,.2f} {REPORTING_CURRENCY})")
    logging.critical("\n\n*** ACTION REQUIRED ***")
    logging.critical("1. Stop your bot immediately.")
    logging.critical("2. Cancel ALL open orders on Kraken.")
    logging.critical("3. Delete the bot's trading state file (e.g., monitor.json).")
    logging.critical("4. Restart your bot to begin the measured run.")


# =============================================================================
# 4. PERFORMANCE ATTRIBUTION CORE MATH
# =============================================================================

def calculate_performance_attribution(
    baseline_data: Dict[str, Decimal],
    current_value: Decimal,
    current_price: Decimal,
    fx_rate_current: Decimal
) -> Tuple[Decimal, Decimal, Decimal]:
    """
    Calculates performance attribution: Total P&L = Holding P&L + Trading P&L.
    All P&L figures are calculated in the reporting currency.
    """
    
    # Convert baseline values to the current reporting currency for accurate comparison
    initial_portfolio_value_reporting = baseline_data['initial_portfolio_value_reporting']
    
    # Convert current value (calculated in quote_asset) to reporting currency
    current_value_reporting = current_value * fx_rate_current

    # 1. Total Net P&L (All Profit)
    total_net_pl_reporting = current_value_reporting - initial_portfolio_value_reporting
    
    # 2. Holding P&L (Market Effect - What you would have made by doing nothing)
    initial_base_inventory = baseline_data['initial_base_inventory']
    start_price = baseline_data['start_price']
    
    # The P&L is calculated in the quote asset (DAI/USDC) and then converted to reporting currency.
    price_difference = current_price - start_price
    holding_pl_in_quote = initial_base_inventory * price_difference
    
    # Apply the CURRENT FX rate to the holding P&L (this is an approximation for simplicity)
    holding_pl_reporting = holding_pl_in_quote * fx_rate_current

    # 3. Trading P&L (Bot Effect - The residual profit created by the trades)
    trading_pl_reporting = total_net_pl_reporting - holding_pl_reporting
    
    return total_net_pl_reporting, holding_pl_reporting, trading_pl_reporting


# =============================================================================
# 5. FIFO REALIZED PROFIT (Robust version)
# =============================================================================

def calculate_realized_profit(user_client: User, start_time: datetime.datetime, kraken_pair: str) -> Decimal:
    """
    Fetches trade history since the bot started and calculates the 
    total realized profit in the QUOTE ASSET (no FX conversion here).
    
    Variables are defined outside the try block to ensure they are available for 
    logging in case of an exception.
    """
    
    # --- STEP 0: Define required variables outside the try block for robust error handling ---
    base_asset_display, quote_asset_display, _ = get_base_quote_assets(kraken_pair)
    total_realized_profit = Decimal('0')
    # -----------------------------------------------------------------------------------------
    
    # Map the common display name to the Kraken internal name for filtering
    kraken_pairs = {kraken_pair}
    if kraken_pair in ('XBTUSD', 'XXBTZUSD'):
        kraken_pairs.add('XXBTZUSD')
        kraken_pairs.add('XBTUSD') 
    
    try:
        start_ts = int(start_time.timestamp()) 
        # API call fixed: Removed the unsupported 'pair' argument from get_trades_history
        trades_response = user_client.get_trades_history(start=start_ts)
        
        raw_trades = trades_response.get('trades', {})
        
        # 1. Collect and Filter Trades
        sorted_trades = []
        for txid, trade in raw_trades.items():
            # Filter trades by the required Kraken pairs
            if trade['pair'] in kraken_pairs:
                trade['time_decimal'] = Decimal(trade['time']) 
                sorted_trades.append(trade)

        # 2. Sort by time (FIFO requires chronological order)
        sorted_trades.sort(key=lambda x: x['time_decimal'])
        
        # FIFO Queue for Unmatched Buys:
        # Stores {'v': remaining_volume, 'unit_cost': cost_per_unit, 'unit_fee': fee_per_unit}
        unmatched_buys: list[Dict[str, Decimal]] = []
        
        for trade in sorted_trades:
            trade_type = trade['type']
            volume = Decimal(trade['vol'])
            cost = Decimal(trade['cost']) # Total price paid (buy) or revenue received (sell)
            fee = Decimal(trade['fee'])
            
            if volume == Decimal('0'):
                continue
            
            # --- BUY: Add to FIFO Stack ---
            if trade_type == 'buy':
                # Calculate unit cost and unit fee ONLY ONCE when the buy is added.
                unit_cost = cost / volume 
                unit_fee = fee / volume
                
                unmatched_buys.append({
                    'v': volume,          
                    'unit_cost': unit_cost, 
                    'unit_fee': unit_fee    
                })
                
            # --- SELL: Match against FIFO Stack ---
            elif trade_type == 'sell':
                remaining_sell_volume = volume
                
                # Calculate the SELL unit metrics once
                sell_revenue_per_unit = cost / volume
                sell_fee_per_unit = fee / volume
                
                # 3. Match the sell against the oldest open buys
                while remaining_sell_volume > 0 and unmatched_buys:
                    oldest_buy = unmatched_buys[0]
                    
                    match_volume = min(remaining_sell_volume, oldest_buy['v'])
                    
                    # --- Prorate and Calculate P&L for this matched segment ---
                    
                    # Cost of acquisition (Prorated from the matched BUY segment)
                    buy_cost_basis = oldest_buy['unit_cost'] * match_volume
                    buy_fee_incurred = oldest_buy['unit_fee'] * match_volume
                    
                    # Revenue from sale (Prorated from the current SELL trade)
                    sell_revenue_received = sell_revenue_per_unit * match_volume
                    sell_fee_incurred = sell_fee_per_unit * match_volume
                    
                    # P&L calculation: Profit = (Sell Revenue - Sell Fee) - (Buy Cost + Buy Fee)
                    realized_segment_pnl = (sell_revenue_received - sell_fee_incurred) - (buy_cost_basis + buy_fee_incurred)
                    total_realized_profit += realized_segment_pnl
                    
                    # --- Update Volumes ---
                    remaining_sell_volume -= match_volume
                    oldest_buy['v'] -= match_volume
                    
                    # If the buy is fully consumed, remove it from the stack
                    if oldest_buy['v'] <= Decimal('0'):
                        unmatched_buys.pop(0)

        if unmatched_buys:
            remaining_volume = sum(b['v'] for b in unmatched_buys)
            # base_asset_display is now defined outside the try block
            logging.warning(f"Unmatched inventory: {remaining_volume:,.8f} {base_asset_display}. This volume is an open position and is not included in realized P&L.")
            
        logging.info(f"Realized P&L calculated: {total_realized_profit:,.2f} {quote_asset_display}")
        return total_realized_profit

    except Exception as e:
        # Log the specific error for debugging. 
        # The variables needed for logging are guaranteed to be defined now.
        logging.error(f"FATAL ERROR in advanced realized profit calculation: {e}")
        return Decimal('0')


# =============================================================================
# 6. MAIN REPORTING FUNCTION (Default execution path)
# =============================================================================

def run_performance_analysis():
    """Calculates and reports the current portfolio performance and attribution."""
    setup_logging()
    
    # 1. Load Baseline Data
    baseline_data_raw = load_baseline()
    if baseline_data_raw is None:
        return
        
    # Extract data and convert to Decimal for math
    baseline_data = baseline_data_raw
    start_time_ts = baseline_data['start_time']
    start_time = datetime.datetime.fromtimestamp(float(start_time_ts))
    
    # Initialize clients
    try:
        market_client = Market()
        user_client = User(key=API_KEY, secret=API_SECRET) 
    except Exception as e:
        logging.error(f"Failed to initialize Kraken clients. Check API_KEY/SECRET. Error: {e}")
        return

    # 2. Get Current State
    current_price = get_current_price(market_client)
    if not current_price:
        logging.error("Cannot fetch current market prices. Exiting tracker.")
        return
        
    base_asset, quote_asset, kraken_pair = get_base_quote_assets(TRADING_PAIR)
    base_balance, quote_balance = get_account_balances(user_client, base_asset, quote_asset)
    
    # 3. Calculate Current Portfolio Value (in Quote Asset)
    base_value_in_quote = base_balance * current_price
    current_value_quote_asset = quote_balance + base_value_in_quote

    # 4. Get Current FX Rate for Reporting
    fx_rate_current = get_fx_rate_to_reporting_currency(market_client, quote_asset, REPORTING_CURRENCY)
    
    # 5. Calculate P&L components (Attribution Model - ALL P&L figures are in REPORTING_CURRENCY)
    total_pnl, holding_pl, trading_pl = calculate_performance_attribution(
        baseline_data=baseline_data,
        current_value=current_value_quote_asset,
        current_price=current_price,
        fx_rate_current=fx_rate_current
    )
    
    # 6. Calculate P&L components (FIFO Model - Profit is calculated in Quote Asset)
    realized_profit_quote = calculate_realized_profit(user_client, start_time, kraken_pair)
    
    # Convert FIFO profit to Reporting Currency
    realized_profit_reporting = realized_profit_quote * fx_rate_current
    
    # Helper to format P&L with sign
    def format_pnl(value: Decimal) -> str:
        sign = "+" if value >= 0 else ""
        percentage = Decimal('0.00')
        initial_val = baseline_data['initial_portfolio_value_reporting']
        
        if initial_val != 0:
            percentage = (value / initial_val * Decimal('100'))
            
        return f"{sign}{value:,.2f} {REPORTING_CURRENCY} ({sign}{percentage:,.2f}%)"

    report = f"""
=====================================================
📈 PORTFOLIO PERFORMANCE & ATTRIBUTION REPORT
=====================================================
TRADING PAIR:   {TRADING_PAIR} ({base_asset}/{quote_asset})
START DATE:     {datetime.datetime.fromtimestamp(float(baseline_data['start_time'])).strftime('%Y-%m-%d %H:%M:%S')}
REPORTING IN:   {REPORTING_CURRENCY}
FX RATE:        1 {quote_asset} = {fx_rate_current:,.4f} {REPORTING_CURRENCY}
DURATION:       {(datetime.datetime.now() - start_time)}

--- CURRENT ASSET HOLDINGS ---
{quote_asset} Balance (Cash): {quote_balance:,.2f} {quote_asset}
{base_asset} Balance (Crypto): {base_balance:,.8f} {base_asset}
Current Price:              {current_price:,.2f} {quote_asset}

--- VALUE CALCULATION (in {REPORTING_CURRENCY}) ---
Baseline Value (Start):     {baseline_data['initial_portfolio_value_reporting']:,.2f} {REPORTING_CURRENCY}
Current Value:              {(current_value_quote_asset * fx_rate_current):,.2f} {REPORTING_CURRENCY}

=====================================================
📊 PERFORMANCE ATTRIBUTION (Total P&L Breakdown)
=====================================================
TOTAL P&L (Net Change):     {format_pnl(total_pnl)}
-----------------------------------------------------
A. HOLDING P&L (Market Effect): {format_pnl(holding_pl)}
   - Profit/Loss from price movement on initial inventory.

B. TRADING P&L (Bot's Contribution): {format_pnl(trading_pl)}
   - The alpha/residual generated by the bot's trading activity.
=====================================================
FIFO REALIZED PROFIT (For Audit)
=====================================================
REALIZED PROFIT (FIFO):     {format_pnl(realized_profit_reporting)}
   - Total P&L from completed Buy/Sell cycles since start.
   - Original FIFO profit in {quote_asset}: +{realized_profit_quote:,.2f} {quote_asset}
"""
    print(report)

# =============================================================================
# 7. ARGUMENT HANDLING ENTRY POINT
# =============================================================================

def load_baseline() -> Optional[Dict[str, Decimal]]:
    """Loads the enriched baseline data and converts strings to Decimals."""
    if not os.path.exists(BASELINE_FILE):
        logging.error(f"Baseline file '{BASELINE_FILE}' not found.")
        logging.info("Please run the script with 'python performance_tracker.py capture' first.")
        return None
        
    with open(BASELINE_FILE, 'r') as f:
        try:
            data = json.load(f)
            return {
                'start_time': Decimal(data['timestamp']),
                'start_price': Decimal(data['start_price']),
                'initial_portfolio_value': Decimal(data['initial_portfolio_value']),
                # NEW FIELD: Value already converted to reporting currency at start
                'initial_portfolio_value_reporting': Decimal(data.get('initial_portfolio_value_reporting', data['initial_portfolio_value'])),
                'initial_base_inventory': Decimal(data['initial_base_inventory']),
                'initial_quote_capital': Decimal(data['initial_quote_capital']),
                'trading_pair': data['trading_pair']
            }
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logging.error(f"Error loading or parsing baseline file: {e}")
            return None

if __name__ == "__main__":

    # Check for the 'capture' command-line argument
    if len(sys.argv) > 1 and sys.argv[1].lower() == 'capture':
        try:
            # Initialize clients for capture
            market_client = Market()
            user_client = User(key=API_KEY, secret=API_SECRET)
            current_price = get_current_price(market_client)
            base_asset, quote_asset, kraken_pair = get_base_quote_assets(TRADING_PAIR)
            save_baseline_state(user_client, current_price, quote_asset)
        except Exception as e:
            # Catch exceptions during client initialization or API calls
            logging.critical(f"FATAL ERROR during capture initialization: {e}")
            logging.critical("Ensure your API_KEY and API_SECRET in config.py are correct.")
    else:
        # Default behavior: run the analysis
        run_performance_analysis()