import logging
# We need User client for balance and open orders, and Trade client for placing/canceling.
from kraken.spot import SpotClient, User, Trade 
import sys
from time import sleep
from decimal import Decimal

# --------------------------------------------------------------------------------
# BLOCK 1: CONFIGURATION (ACTION REQUIRED)
# --------------------------------------------------------------------------------

# Setup logging
logging.basicConfig(
    stream=sys.stdout, 
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ACTION REQUIRED: Your actual API Key and Secret
API_KEY = "IUbxEkxe7EeQATSYUqUysodAxa5SszY1xTMyUYBw1zciCLfz8gafFLc" 
API_SECRET = "5vO+X9yuJvyNl9T2mdEQOL2Lfh/BUpPaCiK5K5UQEXGCohOJPtzvbHk7EfVpK8ytYak7hlo7TEr6CPkX16qVkw==EY"

# GRID CONFIGURATION - SUSTAINABLE AGGRESSION SETTINGS (1.0% Daily Target)
TRADING_PAIR = "XBTUSD"       # US Dollar Pair
GRID_CENTER_PRICE = 68000.00  # Current XBT/USD Price
GRID_GAP_PERCENT = 0.005      # 0.5% gap for initial placement
GRID_LEVELS = 10              # 10 levels total (5 SELLs, 5 BUYs)
MIN_VOLUME = 0.0001           # Kraken minimum XBT trade size

# --------------------------------------------------------------------------------

# --------------------------------------------------------------------------------
# BLOCK 2: UTILITY FUNCTIONS (UPDATED TO TAKE BOTH CLIENTS)
# --------------------------------------------------------------------------------

def cancel_all_open_orders(user_client, trade_client):
    """
    Cancels all existing open orders to ensure a clean grid initialization.
    Uses user_client to get orders and trade_client to cancel them.
    """
    try:
        logging.info("Checking for and canceling any existing open orders...")
        
        # 1. Use the User client to query open orders
        open_orders = user_client.get_open_orders()
        
        order_txids = list(open_orders.get('open', {}).keys())
        
        if not order_txids:
            logging.info("No existing open orders found. Proceeding with initialization.")
            return

        logging.warning(f"Found {len(order_txids)} open orders. Attempting cancellation...")

        # 2. Use the Trade client to cancel the orders
        cancel_response = trade_client.cancel_open_orders(txids=order_txids)
        
        cancelled_count = cancel_response.get('count', 0)
        
        sleep(0.5) 
        
        logging.critical(f"SUCCESS: Successfully canceled {cancelled_count} existing orders.")

    except Exception as e:
        logging.error(f"Error during pre-run order cancellation. Check API keys and network.")
        logging.error(f"Details: {e}")

# --------------------------------------------------------------------------------
# BLOCK 3: CORE LOGIC FUNCTION (UPDATED TO TAKE BOTH CLIENTS)
# --------------------------------------------------------------------------------

def initialize_grid(user_client, trade_client, pair):
    """
    1. Cancels any existing open orders.
    2. Fetches current Bitcoin balance.
    3. Calculates 10 Grid levels (Buy/Sell prices).
    4. Places the 10 initial orders.
    5. Saves all 10 TXIDs to txid.txt for the monitor.
    """
    try:
        # 1. Cancel existing orders to ensure a clean grid
        cancel_all_open_orders(user_client, trade_client)

        logging.info("Attempting to fetch balance...")
        
        # 2. Fetch balance: Use the User client
        balances = user_client.get_account_balance()
        xbt_balance_str = balances.get('XXBT', balances.get('XBT', '0.00'))
        final_xbt_balance = float(xbt_balance_str)
        
        if final_xbt_balance <= MIN_VOLUME * GRID_LEVELS:
            logging.error("FATAL ERROR: XBT balance too low to start grid. Cannot place order.")
            return

        # Volume per order (1/10th of total XBT)
        volume_per_order = final_xbt_balance / GRID_LEVELS
        volume_str = f"{volume_per_order:.8f}"
        logging.info(f"Available XBT (BTC) balance: {final_xbt_balance}")
        logging.info(f"Volume per grid slice (1/{GRID_LEVELS}): {volume_str} XBT")

        
        # 3. Calculate prices for the 10 initial orders (same logic as before)
        grid_txids = []
        orders_to_place = []
        
        # Initial SELL levels (5 orders)
        for i in range(1, 6): 
            price = GRID_CENTER_PRICE * (1 + (i * GRID_GAP_PERCENT))
            orders_to_place.append({
                'side': 'sell',
                'price': f"{price:.2f}",
                'volume': volume_str
            })

        # Initial BUY levels (5 orders)
        for i in range(1, 6): 
            price = GRID_CENTER_PRICE * (1 - (i * GRID_GAP_PERCENT))
            orders_to_place.append({
                'side': 'buy',
                'price': f"{price:.2f}",
                'volume': volume_str 
            })

        logging.info(f"Placing {len(orders_to_place)} initial Grid orders...")
        
        # 4. Place all orders: Use the Trade client
        for order_data in orders_to_place:
            order = trade_client.create_order(
                pair=pair, 
                side=order_data['side'], 
                ordertype='limit',     
                volume=order_data['volume'],
                price=order_data['price']
                # validate=True # <<< REMOVE AFTER A SUCCESSFUL DRY RUN!
            )
            
            txid_list = order.get('txid', [])
            txid = txid_list[0] if txid_list else None
            
            if txid:
                # Save TXID, side, price, and volume
                grid_txids.append(f"{txid},{order_data['side']},{order_data['price']},{order_data['volume']}")
                logging.info(f"PLACED {order_data['side'].upper()} | Price: ${order_data['price']} | TXID: {txid}")
            else:
                logging.error(f"Failed to place {order_data['side']} order at {order_data['price']}.")


        # 5. Save all TXIDs to file
        if grid_txids:
            try:
                with open('txid.txt', 'w') as f:
                    f.write('\n'.join(grid_txids))
                logging.critical(f"SUCCESS: Saved {len(grid_txids)} orders to txid.txt.")
            except Exception as file_e:
                logging.error(f"Failed to save TXIDs to file: {file_e}")
        
    except Exception as e:
        logging.error(f"FATAL ERROR during grid initialization.")
        logging.error(f"Details: {e}")

# --------------------------------------------------------------------------------


# --------------------------------------------------------------------------------
# BLOCK 4: SCRIPT ENTRY POINT (UPDATED TO INITIALIZE BOTH CLIENTS)
# --------------------------------------------------------------------------------

if __name__ == "__main__":
    
    # Initialize the User client (for balance/info) and Trade client (for placing/canceling)
    user_client = User(key=API_KEY, secret=API_SECRET)
    trade_client = Trade(key=API_KEY, secret=API_SECRET)

    # Execute the main function
    initialize_grid(
        user_client=user_client,
        trade_client=trade_client, 
        pair=TRADING_PAIR
    )
    
    logging.info("----------------------------------------------------------------------")
    logging.info("NEXT ACTION: Review logs for 10 placed orders, and then run order_monitor.py.")
    logging.info("----------------------------------------------------------------------")