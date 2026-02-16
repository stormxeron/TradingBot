import logging
from decimal import Decimal
from typing import Dict, Any, Optional

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='💡 INFO - %(message)s')
# We keep logging level high to focus on CRITICAL bot actions
logging.getLogger().setLevel(logging.CRITICAL) 

# --- MOCK SETUP: Simulating API and Bot State ---

# 1. Declare MOCK_API_RESPONSE as a module-level global here
MOCK_API_RESPONSE = {} 

class MockClient:
    """A dummy object to stand in for kraken.spot.Trade."""
    # When cancel_order is called on this mock, it returns the global MOCK_API_RESPONSE
    def cancel_order(self, txid):
        # We don't need 'self' in the parameter list here
        return MOCK_API_RESPONSE

def _safe_api_call(api_call, *args, **kwargs) -> Dict[str, Any]:
    """Mocks the resilient wrapper by returning the global mock response."""
    # For this test, we assume the api_call is always the cancel_order mock
    # and we pass the txid as kwargs to satisfy the signature if needed
    return api_call(*args, **kwargs) 

def mock_save_func(managed_orders: Dict[str, Any]) -> None:
    """Mocks the save function to confirm local state is updated."""
    logging.critical(f"✅ STATE SAVED: Remaining orders in managed_orders: {len(managed_orders)}")

# --- THE FUNCTION UNDER TEST (WITH THE CRITICAL FIX) ---

def cancel_furthest_managed_order(trade_client: object, managed_orders: Dict[str, Any], current_price: Decimal, side_to_free: str, save_func: callable) -> Optional[str]:
    """
    Finds and cancels the managed order furthest from the current price.
    (Contains the CRITICAL BUG FIX for interpreting cancellation success).
    """
    
    # 1. Setup and Find Furthest Order (Simplified for test)
    target_orders = {
        txid: order_data 
        for txid, order_data in managed_orders.items() 
        if order_data['side'] == side_to_free
    }
    
    if not target_orders:
        return None
        
    furthest_txid = list(target_orders.keys())[0]
    
    logging.critical(f"🚨 EMERGENCY CANCEL: Canceling furthest order {furthest_txid}.")
    
    # 3. Cancel the order via API - NOTE: This uses the mock objects defined above
    cancel_response = _safe_api_call(trade_client.cancel_order, txid=furthest_txid)
    
    # 4. CRITICAL FIX LOGIC: Robustly check API response
    is_canceled = False

    if cancel_response:
        # Case 1: Successful cancellation (API returns count > 0)
        if (cancel_response.get('count', 0) > 0 or 
            (cancel_response.get('result') and cancel_response['result'].get('count', 0) > 0)):
            is_canceled = True

        # Case 2: Order was already gone ('Unknown order' error)
        elif 'error' in cancel_response:
            error_list = cancel_response['error']
            if any("EOrder:Unknown order" in err for err in error_list):
                logging.warning(
                    f"Order {furthest_txid} was already gone (canceled/filled). "
                    "Treating as successful budget clearance."
                )
                is_canceled = True
            else:
                logging.error(f"❌ FAILED: API cancellation for {furthest_txid} failed. Error: {error_list}")

    # Final Action
    if is_canceled:
        managed_orders.pop(furthest_txid, None)
        save_func(managed_orders) 
        
        logging.critical(f"✅ SUCCESS: Order {furthest_txid} canceled. Budget freed.")
        return furthest_txid
    else:
        logging.critical(f"❌ FATAL FAILURE: Cancellation check for {furthest_txid} failed.")
        return None

# ======================================================================
# === DEMONSTRATION & TESTING ===
# ======================================================================

if __name__ == '__main__':
    
    # We must use 'global' inside the scope if we were modifying a module-level variable 
    # from within a function, but here we are modifying it directly in the main block.
    # The functions reference the global variable directly.

    # Initial State for Testing
    test_orders = {
        'O-BUDGET-FAIL': {'side': 'buy', 'price': Decimal('3599.25'), 'volume': Decimal('0.01')},
        'O-NEAR-PRICE': {'side': 'buy', 'price': Decimal('3800.00'), 'volume': Decimal('0.01')},
    }
    mock_client = MockClient()
    current_price = Decimal('3874.54')

    print("\n" + "="*50)
    print("--- TEST 1: SIMULATING SUCCESSFUL CANCELLATION (The Bug Fix) ---")
    print("="*50)
    
    # Set the exact response that caused the failure log
    MOCK_API_RESPONSE = {
        'result': {'count': 1} 
    }

    # Run the test
    txid_to_check = list(test_orders.keys())[0]
    result = cancel_furthest_managed_order(
        mock_client, 
        test_orders, 
        current_price, 
        'buy', 
        mock_save_func
    )
    
    print(f"\nResult from Test 1: {result}")
    
    if result and txid_to_check not in test_orders:
        print("\n✅ TEST 1 PASSED: Function correctly treated {'count': 1} as success.")
    else:
        print("\n❌ TEST 1 FAILED: The fix was not applied correctly.")