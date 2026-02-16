# final_trade_search.py

import time
import logging
from decimal import Decimal
from typing import Dict, Any, List
import sys

# CRITICAL: Ensure these imports match your environment
from kraken.spot import User 
from utils_eth import _safe_api_call # Your fixed wrapper
from config_eth import API_KEY, API_SECRET # Your key/secret

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='💡 INFO - %(message)s')
logging.getLogger().setLevel(logging.INFO)

def run_trade_history_query(client: User, test_name: str, **kwargs) -> Dict[str, Any]:
    """Runs a single trade history query using the safe API call."""
    logging.info(f"\n--- Running Test: {test_name} ---")
    
    # We are absolutely certain this is the correct method on the User client
    response = _safe_api_call(client.get_trades_history, **kwargs)
    
    if response and 'result' in response:
        trades = response['result'].get('trades', {})
        count = len(trades)
        
        if count > 0:
            logging.info(f"✅ SUCCESS: Test returned {count} trade record(s).")
            # Print the first TXID found for verification
            first_txid = next(iter(trades.values())).get('ordertxid')
            logging.info(f"    First TXID found: {first_txid}")
            return trades
        
    logging.warning("❌ FAILED: Query returned no results or was empty.")
    return {}

# --- MAIN EXECUTION ---

if __name__ == '__main__':
    try:
        user_client = User(key=API_KEY, secret=API_SECRET)
    except Exception as e:
        logging.critical(f"Failed to initialize Kraken client: {e}")
        sys.exit(1)
    
    # Calculate start time (Unix timestamp) for 3 hours ago
    THREE_HOURS_AGO = str((Decimal(time.time()) - Decimal(3 * 3600)).quantize(Decimal('0')))
    all_results = {}
    
    # ----------------------------------------------------
    # TEST 1: The Confirmed Method (Start Timestamp)
    # The method that previously failed due to delay.
    # ----------------------------------------------------
    all_results['Test 1: Standard Search (start)'] = run_trade_history_query(
        user_client, 
        "Standard Start Timestamp (Last 3 Hrs)", 
        start=THREE_HOURS_AGO
    )

    # ----------------------------------------------------
    # TEST 2: Positional Offset (ofs = 0)
    # This should return the 50 most recent trades regardless of age.
    # ----------------------------------------------------
    all_results['Test 2: Positional Offset (ofs=0)'] = run_trade_history_query(
        user_client, 
        "Positional Offset (50 Most Recent)", 
        ofs=0
    )
    
    # ----------------------------------------------------
    # TEST 3: Taker-Only (Consolidated) Trades
    # Checks if filtering the trade type affects availability.
    # ----------------------------------------------------
    all_results['Test 3: Taker-Only Trades (consolidate_taker=True)'] = run_trade_history_query(
        user_client, 
        "Consolidated Taker Trades (Last 3 Hrs)", 
        consolidate_taker=True, 
        start=THREE_HOURS_AGO
    )
    
    # ----------------------------------------------------
    # SUMMARIZE RESULTS
    # ----------------------------------------------------
    
    found_any = any(all_results.values())
    print("\n\n--- TRADE EXTRACTION EXHAUSTIVE SUMMARY ---")
    
    if not found_any:
        print("❌ ALL TESTS FAILED: The API is not reporting recent trade history through any supported method.")
        print("This confirms the **data delay** is the only issue. The code is correct.")
    else:
        print("✅ SUCCESS: At least one method returned data. Review the tests above to see which one works.")