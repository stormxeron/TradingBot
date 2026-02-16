import sys
import logging
import time

# --- IMPORTANT: Ensure these imports match your bot's setup ---
try:
    from config import API_KEY, API_SECRET 
    # NOTE: You must replace 'Trade' with the correct import path if yours is different.
    # The common path for the Kraken Python SDK is:
    from kraken.spot import Trade 
except ImportError as e:
    print(f"FATAL ERROR: Could not import necessary files/libraries: {e}")
    sys.exit(1)

# The list of untracked orders to be canceled
ORDERS_TO_CANCEL = [
    'OVWAPI-YSKUD-YHXM46', 
    'O7TOIF-ARWVZ-PFF5LO', 
    'OG3E7M-NI735-KHBEEN'
]

def run_cleanup():
    """Initializes the API client and cancels the specific untracked orders."""
    print("\n--- STARTING KRAKEN GRID CLEANUP ---")
    
    try:
        # Initialize the Trade client using your API keys
        trade_client = Trade(API_KEY, API_SECRET)
        print("API Trade client initialized successfully.")
    except Exception as e:
        print(f"❌ Failed to initialize Kraken API clients: {e}")
        sys.exit(1)

    successful_cancellations = 0

    for txid in ORDERS_TO_CANCEL:
        print(f"Attempting to cancel order: {txid}...")
        try:
            # Call the cancel_order API method
            response = trade_client.cancel_order(txid=txid)
            
            # Kraken's response for cancellation might be nested
            if response and response.get('result'):
                # Check for successful response
                print(f"✅ SUCCESS: Order {txid} canceled. Result: {response['result']}")
                successful_cancellations += 1
            else:
                print(f"⚠️ WARNING: Order {txid} was not explicitly canceled by the API. It may already be gone or the TXID is incorrect. Response: {response}")

        except Exception as e:
            # The order might already be canceled, filled, or the TXID is wrong.
            print(f"❌ FAILURE: Could not cancel order {txid}. Error: {e}")
            time.sleep(0.5)

    print(f"\n--- CLEANUP FINISHED ---")
    print(f"Total orders attempted: {len(ORDERS_TO_CANCEL)}")
    print(f"Total successful cancellations: {successful_cancellations}")
    print("-------------------------")
    print("CHECK KRAKEN TO VERIFY OPEN ORDERS COUNT IS NOW 37 (40 - 3 CANCELED VALID ORDERS).")


if __name__ == "__main__":
    # You may want to comment out your existing logging setup for this one-off script
    # logging.basicConfig(level=logging.INFO) 
    run_cleanup()