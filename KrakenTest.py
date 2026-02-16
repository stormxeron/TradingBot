import datetime
from decimal import Decimal, getcontext, ROUND_HALF_UP
import logging
import sys
import time
import requests
from typing import Dict, Any, Optional, Tuple
from kraken.spot import User, Market, SpotClient # Requires 'kraken-api' library

# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================

from config import API_KEY, API_SECRET
from utils import setup_logging, _safe_api_call, get_current_price

# Define the start time: 10 years ago
TEN_HOURS_AGO = datetime.datetime.now() - datetime.timedelta(hours = 24)

# List of common pairs to check. Add any pairs you have traded on Kraken here!
TRADING_PAIRS_TO_CHECK = [
    'XBTUSD', 'ETHCAD'
]

# Set high precision for financial calculations
getcontext().prec = 60


def resolve_kraken_symbol(pair: str) -> Tuple[str, str, str]:
    """
    Resolves an input pair (e.g., 'XBTUSD') to its Kraken symbol, base, and quote assets.
    Returns (Kraken_Symbol, Base_Asset, Quote_Asset)
    """
    normalized = pair.upper().replace('/', '')
    
    # Heuristic mapping for common pairs
    base = ''
    quote = ''
    kraken_symbol = normalized
    
    if normalized.endswith('CAD'):
        quote = 'CAD'
    elif normalized.endswith('USD'):
        quote = 'USD'
        
    if quote:
        base = normalized.replace(quote, '')
    
    # Adjust for Kraken's prefixes
    if base in ('XBT', 'BT', 'BTC'):
        base = 'XBT'
        if quote == 'USD': kraken_symbol = 'XXBTZUSD'
        elif quote == 'CAD': kraken_symbol = 'XBT/CAD' # CAD often lacks the Z prefix in API response trade pairs
        else: kraken_symbol = f"X{base}{quote}" # General crypto prefix
    
    # Handle other common pairs explicitly (simplification)
    elif base == 'ETH':
        if quote == 'USD': kraken_symbol = 'XETHZUSD'
        elif quote == 'CAD': kraken_symbol = 'XETHZCAD'
        
        
    # Final check on asset names (removes the initial 'X' or 'Z' for user display)
    display_base = base.replace('X', '').replace('Z', '')
    display_quote = quote.replace('X', '').replace('Z', '')
        
    return kraken_symbol.upper(), display_base, display_quote


def get_current_price(market_client: Market, pair: str) -> Optional[Decimal]:
    """Fetches the current last traded price for a given pair."""
    
    kraken_symbol, _, _ = resolve_kraken_symbol(pair)
    
    # We try both the user input pair and the resolved Kraken symbol for maximum reliability
    lookup_pairs = [pair.upper().replace('/', ''), kraken_symbol]
    
    for lookup_pair in lookup_pairs:
        response = _safe_api_call(market_client.get_ticker, pair=lookup_pair)
        
        # The response key might be the symbol itself or a variation
        response_key = next((k for k in response.keys() if lookup_pair in k or k in lookup_pair), None)
        
        if response_key and 'c' in response[response_key]:
            try:
                last_price_str = response[response_key]['c'][0]
                logging.info(f"  > Current Market Price for {pair}: {Decimal(last_price_str):,.4f}")
                return Decimal(last_price_str)
            except (IndexError, KeyError, TypeError, ValueError):
                pass

    logging.error(f"Could not fetch current price for {pair}.")
    return None

def get_exchange_rate(market_client: Market, from_currency: str, to_currency: str) -> Decimal:
    """Fetches the exchange rate (e.g., USD to CAD). Defaults to 1 if currencies match."""
    
    if from_currency == to_currency:
        return Decimal('1')
        
    try:
        # Prioritize standard fiat pair lookup
        rate = get_current_price(market_client, f"{from_currency}{to_currency}")
        
        if rate is None or rate == Decimal('0'):
            # Fallback to historical Kraken Z-prefixed pair (e.g., ZUSDZCAD)
            rate = get_current_price(market_client, f"Z{from_currency}Z{to_currency}")

        if rate is None or rate == Decimal('0'):
            logging.error(f"Failed to get exchange rate for {from_currency}/{to_currency}. Assuming rate of 1.0.")
            return Decimal('1')

        return rate
        
    except Exception as e:
        logging.error(f"Error fetching exchange rate: {e}. Assuming rate of 1.0.")
        return Decimal('1')


# ==============================================================================
# 4. FIFO P&L CALCULATION
# ==============================================================================

def fetch_all_trades_since(user_client: User, start_time: datetime.datetime) -> Dict[str, Any]:
    """
    Fetches all user trades since a specific time, handling pagination (offset).
    """
    all_trades = {}
    offset = 0
    trades_per_page = 50 
    start_ts_seconds = int(start_time.timestamp()) 

    logging.info(f"  > Fetching all trades since {start_time.strftime('%Y-%m-%d %H:%M:%S')} (Timestamp: {start_ts_seconds})")
    
    while True:
        trades_response = _safe_api_call(
            user_client.get_trades_history, 
            start=start_ts_seconds, 
            ofs=offset
        )
        print (trades_response)
        if 'error' in trades_response:
            logging.error(f"API Error fetching trade history: {trades_response['error']}")
            return {'error': trades_response['error']}
            
        current_page_trades = trades_response.get('trades', {})

        if not current_page_trades:
            break
            
        all_trades.update(current_page_trades)
        
        trade_count = len(current_page_trades)
        if trade_count < trades_per_page:
            break 
        
        offset += trade_count
        logging.info(f"  > Fetched {trade_count} trades. Continuing with offset {offset}...")
        
        time.sleep(0.5) 
        
    logging.info(f"  > Total historical trades fetched: {len(all_trades)}")
    return {'trades': all_trades}


def calculate_realized_and_unrealized_profit(
    user_client: User, 
    market_client: Market, 
    start_time: datetime.datetime, 
    input_pair: str,
    all_raw_trades: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Calculates realized P&L (FIFO) and the current unrealized P&L for open positions
    for a single input_pair using a pre-fetched set of all trades.
    """
    
    # Determine the strict Kraken symbol and display assets
    kraken_symbol, base_asset, quote_asset = resolve_kraken_symbol(input_pair)

    # Default structure for P&L results
    pnl_results = {
        'pair': input_pair, 
        'kraken_symbol': kraken_symbol, 
        'base_asset': base_asset,
        'quote_asset': quote_asset,
        'realized_pnl': Decimal('0'),
        'unrealized_pnl': Decimal('0'), 
        'holding_volume': Decimal('0'),
        'current_price': Decimal('0'),
        'average_cost': Decimal('0'),
        'error': False,
        'realized_pnl_cad': Decimal('0'), # Initializing CAD fields
        'unrealized_pnl_cad': Decimal('0'),
        'cad_rate': Decimal('1')
    }
    
    logging.info(f"  > Target Assets: {base_asset}/{quote_asset}. Filtering on Kraken Symbol: {kraken_symbol}")


    # 1. Fetch Current Price
    current_price = get_current_price(market_client, input_pair)
    if current_price is None or current_price == Decimal('0'):
        logging.error(f"Skipping P&L for {input_pair}: Failed to get current price.")
        pnl_results['error'] = True
        return pnl_results
    pnl_results['current_price'] = current_price
        
    # 2. Filter Trades by exact Kraken Symbol
    sorted_trades = []
    
    strict_match_symbol = kraken_symbol.upper()
    
    for trade in all_raw_trades.values():
        if trade['pair'].upper() == strict_match_symbol:
            sorted_trades.append(trade)
            
    # Fallback for known edge cases like XBT/CAD vs XBTCAD in trade history
    if not sorted_trades and 'XBT/CAD' in input_pair.upper():
         logging.warning("  > Strict symbol match failed for XBT/CAD. Trying XBTCAD fallback.")
         for trade in all_raw_trades.values():
            if trade['pair'].upper() == 'XBTCAD':
                sorted_trades.append(trade)

    if not sorted_trades:
        logging.info(f"  > No trades found matching symbol {kraken_symbol} or fallback.")
        return pnl_results
        
    logging.info(f"  > Found {len(sorted_trades)} relevant trades for {input_pair}.")

    # 3. Sort by time (FIFO requires chronological order)
    sorted_trades.sort(key=lambda x: Decimal(x['time']))
        
    # FIFO Queue
    unmatched_buys: list[Dict[str, Decimal]] = []
    total_realized_profit = Decimal('0')
        
    # 4. Process Trades and Calculate P&L
    for trade in sorted_trades:
        trade_type = trade['type']
        volume = Decimal(trade['vol'])
        cost = Decimal(trade['cost']) 
        fee = Decimal(trade['fee'])

        if volume == Decimal('0'): continue
            
        if trade_type == 'buy':
            unmatched_buys.append({
                'v': volume,          
                'total_cost': cost, 
                'total_fee': fee    
            })
        
        elif trade_type == 'sell':
            remaining_sell_volume = volume
            
            while remaining_sell_volume > 0 and unmatched_buys:
                oldest_buy = unmatched_buys[0]
                match_volume = min(remaining_sell_volume, oldest_buy['v'])

                proration_factor = match_volume / oldest_buy['v']
                
                # Buy Cost Basis and Fee (Prorated)
                buy_cost_basis = oldest_buy['total_cost'] * proration_factor
                buy_fee_incurred = oldest_buy['total_fee'] * proration_factor
                    
                # Revenue and Fee from Current Sale (Prorated)
                sell_revenue_received = (cost / volume) * match_volume
                sell_fee_incurred = (fee / volume) * match_volume
                    
                # Realized P&L = (Sell Revenue) - (Buy Cost Basis) - (Total Fees)
                realized_segment_pnl = sell_revenue_received - buy_cost_basis - buy_fee_incurred - sell_fee_incurred
                total_realized_profit += realized_segment_pnl
                    
                # Update Volumes and Costs of the buy segment
                oldest_buy['total_cost'] -= buy_cost_basis
                oldest_buy['total_fee'] -= buy_fee_incurred
                oldest_buy['v'] -= match_volume
                    
                # Update remaining sell volume
                remaining_sell_volume -= match_volume
                    
                # If the buy is fully consumed, remove it
                if oldest_buy['v'] <= Decimal('0') + Decimal('1e-10'):
                    unmatched_buys.pop(0)

    pnl_results['realized_pnl'] = total_realized_profit
        
    # 5. Calculate Unrealized P&L
    if unmatched_buys:
        total_holding_volume = sum(b['v'] for b in unmatched_buys)
        total_holding_cost = sum(b['total_cost'] for b in unmatched_buys)
        total_holding_fee = sum(b['total_fee'] for b in unmatched_buys)
        
        pnl_results['holding_volume'] = total_holding_volume
            
        if total_holding_volume > Decimal('0'):
            average_cost_basis = (total_holding_cost + total_holding_fee) / total_holding_volume
            current_market_value = total_holding_volume * current_price
                
            unrealized_pnl = current_market_value - (total_holding_cost + total_holding_fee)
                
            pnl_results['unrealized_pnl'] = unrealized_pnl
            pnl_results['average_cost'] = average_cost_basis
        
    return pnl_results

# ==============================================================================
# 5. MAIN EXECUTION
# ==============================================================================

def run_historical_pnl_calculation():
    """
    Main function to run the calculation and output the summary.
    """
    # 1. Check API Keys
    if API_KEY == "YOUR_KRAKEN_API_KEY" or API_SECRET == "YOUR_KRAKEN_SECRET":
        print("CRITICAL: Please edit the script and set your API_KEY and API_SECRET.")
        return

    # 2. Setup Logging
    setup_logging()
    
    logging.info("--- STARTING 10-HOUR COMPREHENSIVE P&L ANALYSIS ---")
    logging.info(f"Checking trade history since: {TEN_HOURS_AGO.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 3. Initialize Kraken Clients
    try:
        user_client = User(key=API_KEY, secret=API_SECRET)
        market_client = Market()
    except Exception as e:
        logging.critical(f"Failed to initialize Kraken client(s). Check API key/secret. Error: {e}")
        return

    # 4. Fetch ALL Trade History once to reduce API calls
    trades_data = fetch_all_trades_since(user_client, TEN_HOURS_AGO)
    if 'error' in trades_data:
        logging.critical("Failed to fetch trade history. Cannot proceed with P&L calculation.")
        return
        
    all_raw_trades = trades_data.get('trades', {})
    if not all_raw_trades:
        logging.warning("No trades found in the last 10 hours for any pair.")


    # 5. Storage for aggregated P&L
    total_realized_cad = Decimal('0')
    total_unrealized_cad = Decimal('0')
    all_pnl_results = []
    
    # 6. Process all pairs
    for pair in TRADING_PAIRS_TO_CHECK:
        logging.info(f"\n--- Processing Input Pair: {pair} ---")
        
        # Calculate P&L for this specific pair, using the pre-fetched trades
        pnl_data = calculate_realized_and_unrealized_profit(user_client, market_client, TEN_HOURS_AGO, pair, all_raw_trades)
        

        # Determine the assets for logging
        base_asset = pnl_data['base_asset']
        quote_currency = pnl_data['quote_asset']
        
        if pnl_data['error']:
            logging.warning(f"Skipping aggregation for {pair} due to error.")
            all_pnl_results.append(pnl_data)
            continue
            
        # Get Exchange Rate to CAD
        if quote_currency != 'CAD':
            cad_rate = get_exchange_rate(market_client, quote_currency, 'CAD')
            logging.info(f"  > Exchange Rate {quote_currency}/CAD: {cad_rate:,.4f}")
        else:
            cad_rate = Decimal('1')
        
        # Conversion to CAD and Aggregation
        realized_pnl_cad = pnl_data['realized_pnl'] * cad_rate
        unrealized_pnl_cad = pnl_data['unrealized_pnl'] * cad_rate
        
        total_realized_cad += realized_pnl_cad
        total_unrealized_cad += unrealized_pnl_cad
        
        # Store CAD converted results for final report display
        pnl_data['realized_pnl_cad'] = realized_pnl_cad
        pnl_data['unrealized_pnl_cad'] = unrealized_pnl_cad
        pnl_data['cad_rate'] = cad_rate

        all_pnl_results.append(pnl_data)

        logging.info(f"  > Realized P&L in {quote_currency}: {pnl_data['realized_pnl']:,.4f} -> {realized_pnl_cad:,.2f} CAD")
        if pnl_data['holding_volume'] > 0:
            logging.info(f"  > Unrealized P&L in {quote_currency}: {pnl_data['unrealized_pnl']:,.4f} -> {unrealized_pnl_cad:,.2f} CAD (Holding: {pnl_data['holding_volume']:,.6f} {base_asset})")
        else:
            logging.info(f"  > Unrealized P&L: 0.00 CAD (No open position)")


    # 7. Final Summary Report
    total_pnl_cad = total_realized_cad + total_unrealized_cad

    print("\n" * 2)
    print("=" * 75)
    print("💰 TOTAL COMPREHENSIVE P&L SUMMARY (Last 10 Hours)")
    print("-" * 75)
    print(f"Reporting Date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Aggregation Currency: CAD (Calculated using current market rates)")
    print("=" * 75)
    
    print("\n[ INDIVIDUAL P&L BREAKDOWN ]")
    
    # Display individual pair results
    for data in all_pnl_results:
        
        # Skip pairs that had no trades and no open position
        if data['realized_pnl_cad'] == Decimal('0') and data['unrealized_pnl_cad'] == Decimal('0') and data['holding_volume'] == Decimal('0'):
            continue
        
        # Use the Kraken symbol for the pair display to be explicit about the market that produced the P&L
        kraken_symbol = data['kraken_symbol'].replace('/','') 
        quote_currency = data['quote_asset']
        
        print(f"\n--- {kraken_symbol} ({data['base_asset']} / {quote_currency}) ---")
        print(f"  Realized P&L: {data['realized_pnl_cad']:,.2f} CAD (Closed Trades)")
        
        if data['holding_volume'] > Decimal('0'):
            # These values are in the quote currency of the original pair (e.g., USD)
            avg_cost_formatted = data['average_cost'].quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            current_price_formatted = data['current_price'].quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            
            print(f"  Unrealized P&L: {data['unrealized_pnl_cad']:,.2f} CAD (Open Position)")
            print(f"    - Holding: {data['holding_volume']:,.6f} {data['base_asset']}")
            print(f"    - Avg Cost: {avg_cost_formatted:,.2f} {quote_currency} | Current Price: {current_price_formatted:,.2f} {quote_currency}")
        else:
            print("  Unrealized P&L: 0.00 CAD (No open position)")


    print("\n[ AGGREGATED TOTALS IN CAD ]")
    print("-" * 35)
    print(f"  1. TOTAL REALIZED P&L: {total_realized_cad:,.2f} CAD")
    print(f"  2. TOTAL UNREALIZED P&L: {total_unrealized_cad:,.2f} CAD")
    print("-" * 35)

    total_pnl_sign = "+" if total_pnl_cad >= Decimal('0') else ""
    print(f"  TOTAL COMPREHENSIVE P&L: {total_pnl_sign}{total_pnl_cad:,.2f} CAD")
    print("=" * 75)

if __name__ == "__main__":
    try:
        run_historical_pnl_calculation()
    except Exception as e:
        print(f"An unexpected error occurred during execution: {e}")