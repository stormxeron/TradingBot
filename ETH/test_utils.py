import logging
from decimal import Decimal
import os
from kraken.spot import User 
from config_eth import API_KEY, API_SECRET, TRADING_PAIR # Assume these are in your config.py
# If your utils.py uses time, requests.exceptions, you'll need to import them here too
# import time
# import requests.exceptions 

# --- Import your utility functions ---
from utils_eth import setup_logging, _safe_api_call 
# You may need to import get_full_open_orders_from_kraken directly if it's not imported in utils.py

# --- Set up temporary logging for the test ---
# This ensures you see the output clearly
LOG_LEVEL = logging.DEBUG
setup_logging() 

def get_full_open_orders_from_kraken(user_client, trading_pair: str) -> dict:
    """
    Fetches all open orders from Kraken and filters/parses them 
    only for the provided trading_pair (e.g., 'XETHZCAD').
    """
    
    logging.critical(f"CONFIG CHECK: trading_pair is set to: '{trading_pair}'")
    full_order_details = {}

    try:
        logging.info("Attempting to fetch raw open orders for file rebuild...")
        
        response = user_client.get_open_orders() 
        
        logging.debug(f"Raw API Response Check: {response}") 
        
        if response is None or response.get('error'):
            logging.critical(f"API Error fetching orders: {response.get('error', 'Unknown Error')}")
            return {}
        
        # 🚨 FIX: This line now checks for 'result' OR assumes the 'open' orders are at the top level.
        open_orders = response.get('result', response).get('open', {})
        
        for txid, details in open_orders.items():
            
            # --- FILTERING & PARSING LOGIC ---
            order_pair = details.get('descr', {}).get('pair')
            
            if order_pair != trading_pair: 
                logging.debug(f"Skipping order {txid} for pair {order_pair}, expected {trading_pair}")
                continue 

            try:
                side = details.get('descr', {}).get('type')
                price_str = details.get('descr', {}).get('price')
                volume_str = details.get('vol')
                
                # Perform conversion to Decimal, treating empty/None strings as zero
                price = Decimal(price_str) if price_str else Decimal(0)
                volume = Decimal(volume_str) if volume_str else Decimal(0)
                
                full_order_details[txid] = {
                    'side': side,
                    'price': price,
                    'volume': volume
                }

            except Exception as e:
                # We know this print statement doesn't always show, but it's the right logic
                print(f"!!! CRASH FOUND !!! Order ID: {txid}. Exception: {e}. Raw price: {price_str}, Raw volume: {volume_str}")
                continue
        
        logging.info(f"Successfully fetched {len(full_order_details)} open orders from Kraken for rebuild.")
        return full_order_details

    except Exception as e:
        logging.critical(f"FATAL EXCEPTION during get_full_open_orders_from_kraken: {e}")
        return {}
    
def run_test():
    """Initializes client and tests the target function."""
    
    logging.info("--- Starting Utility Function Test ---")

    # 1. Initialize the User client (needed for trade/order functions)
    try:
        user_client = User(
            key= API_KEY, 
            secret=API_SECRET
        )
        logging.info("Kraken User client initialized.")
    except Exception as e:
        logging.error(f"Failed to initialize Kraken client: {e}")
        return

    # 2. Execute the function under test
    logging.info("Calling get_full_open_orders_from_kraken...")
    
    # 🛑 CRITICAL STEP: The function will log the raw response at DEBUG level!
    result_orders = get_full_open_orders_from_kraken(user_client, 'ETHCAD')
    
    # 3. Analyze the results
    
    if isinstance(result_orders, dict) and 'error' in result_orders:
        logging.critical(f"TEST FAILED: Function returned a definitive error: {result_orders['error']}")
    elif len(result_orders) == 0:
        logging.error("TEST FAILED: Function returned 0 orders. Check the DEBUG log for the raw API response!")
    else:
        # Success if it returns the expected number of open orders
        logging.critical(f"✅ TEST SUCCESS! Function retrieved {len(result_orders)} orders.")
        
        # Optional: Print details of one order to confirm parsing is correct
        first_txid = next(iter(result_orders))
        logging.info(f"Example Parsed Order ({first_txid}): {result_orders[first_txid]}")

    logging.info("--- Utility Function Test Complete ---")

if __name__ == "__main__":
    run_test()