# get_recent_trades.py

import time
import logging
import sys
import requests # Needed for exceptions.ReadTimeout
from decimal import Decimal
from typing import Dict, Any, List
from kraken.spot import SpotClient 

# --- CRITICAL: Import API Keys ---
try:
    from config_eth import API_KEY, API_SECRET 
except ImportError:
    print("FATAL: Cannot import API_KEY and API_SECRET from config_eth.py. Check file location.")
    sys.exit(1)

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='💡 INFO - %(message)s')

# =========================================================================
# === EMBEDDED RESILIENCE FUNCTIONS (NO EXTERNAL UTILS REQUIRED) =========
# =========================================================================

def _safe_api_call(api_call, *args, max_retries: int = 5, **kwargs) -> Dict[str, Any]:
    """
    Executes a Kraken API call with exponential backoff and error handling.
    This is the resilient wrapper embedded directly into the script.
    """
    method_name = getattr(api_call, '__name__', str(api_call))
    
    for attempt in range(max_retries):
        try:
            # Pass *args to correctly handle raw_client.request
            response = api_call(*args, **kwargs) 
            
            if response is not None and isinstance(response, dict) and response.get('error'):
                error_list = response['error']
                
                # Fatal NON-RETRYABLE Errors
                if any("Invalid" in err or 'EGeneral:Invalid arguments' in err for err in error_list):
                    logging.error(f"FATAL NON-RETRYABLE ERROR: {error_list}")
                    return {"error": error_list, "fatal": True}

                # Treat other API errors as transient for potential retry
                raise Exception(f"Kraken API reported an error: {error_list}")
            
            return response

        # CRITICAL FIXES FOR NETWORK AND PARSING ERRORS
        except requests.exceptions.ReadTimeout as e:
            logging.warning(f"Network Read Timeout on attempt {attempt + 1}/{max_retries}: {e}")
        except Exception as e:
            # Catches JSONDecodeError (parsing error) and ConnectionError
            logging.warning(f"Transient network/parsing error on attempt {attempt + 1}/{max_retries}: {e}")

        if attempt == max_retries - 1:
            logging.error(f"Max retries reached. Failed to execute API call.")
            return {"error": [f"Max retries reached: {method_name}"]}

        sleep_time = 2 ** attempt
        logging.info(f"Retrying in {sleep_time} seconds...")
        time.sleep(sleep_time)
        
    return {}

def retrieve_recent_trades(client: object, lookback_hours: int = 10) -> Dict[str, Any]:
    """
    Retrieves the trade history using the proven raw API method and resilient wrapper.
    """
    
    # 1. Calculate parameters
    seconds_ago = lookback_hours * 3600
    start_timestamp = Decimal(time.time()) - Decimal(seconds_ago)
    start_time_str = str(start_timestamp.quantize(Decimal('0')))

    endpoint = "/0/private/TradesHistory"
    params = {
        'start': start_time_str,
        'trades': True 
    }
    
    logging.info(f"Retrieving trades since timestamp {start_time_str} ({lookback_hours} hours ago).")
    
    # 2. Execute the raw API request using the embedded resilient wrapper
    response = _safe_api_call(
        client.request, 
        method="POST", 
        uri=endpoint, 
        params=params
    ) 
    
    return response

# =========================================================================
# ======================== MAIN EXECUTION BLOCK ===========================
# =========================================================================

if __name__ == '__main__':
    
    # 1. Initialize Client
    try:
        raw_client = SpotClient(key=API_KEY, secret=API_SECRET)
    except Exception as e:
        logging.critical(f"FATAL: Client initialization failed: {e}")
        sys.exit(1)

    # 2. Call the new function
    trade_response = retrieve_recent_trades(raw_client, lookback_hours=10)
    
    print("\n--- RAW API RESPONSE (Self-Contained) ---")
    print(trade_response) 

    # 3. Restructuring and analysis logic (FIXED TO CHECK FOR 'TRADES' DIRECTLY)
    
    # Check for the error structure first
    if trade_response and 'error' in trade_response:
        print(f"❌ API Error: {trade_response['error']}")
        
    # Check for the success structure (which contains the 'trades' key)
    elif trade_response and 'trades' in trade_response:
        
        # Access trades directly from the top level
        trades = trade_response['trades']
        unique_order_ids = {trade.get('ordertxid') for trade in trades.values() if trade.get('ordertxid')}

        if unique_order_ids:
            # Data was successfully retrieved and restructured
            print(f"\n🎉 SUCCESS: Extracted {len(unique_order_ids)} unique Order IDs.")
            
            # Print unique order IDs and the total count
            print(f"Total Trades Found: {trade_response.get('count', 'N/A')}")
            print("Unique Order IDs:")
            for i, oid in enumerate(unique_order_ids):
                print(f"  {i+1}. {oid}")
        else:
            print("\n❌ SUCCESS: Request returned a structured, but empty, trade history (no recent trades).")
            
    else:
         # This catches cases where the top-level keys ('trades' or 'error') are missing
         print(f"\n❌ FAILED: Final response was unstructured or empty after all retries.")