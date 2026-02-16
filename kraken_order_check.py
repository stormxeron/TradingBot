import sys
import os
from pprint import pprint

# --- CONFIGURATION IMPORTS ---
# Assumes config.py is in the same directory
try:
    from config import API_KEY, API_SECRET, TRADING_PAIR
    # FIX: Importing both Trade (for cancelling if needed) and User (for fetching open orders)
    from kraken.spot import Trade, User 
except ImportError as e:
    print(f"FATAL ERROR: Could not import API keys or Trade/User client. Ensure config.py is present and the kraken library is installed. Error: {e}")
    sys.exit(1)

# List of the 6 orders that your bot's TRIMMING logic CANCELED but are still in txid.txt (Ghosts)
CANCELED_GHOST_IDS = {
    'O2QRAA-5FDVJ-72NXBQ',
    'OL57V5-43GNP-RU4UGP',
    'OLYEJL-KMPL3-OYFXUT',
    'O3NQJV-OIOW3-NG5LN6',
    'ODEPLE-EYKOO-YOXH2B',
    'OAOKM7-4TSSD-OZXK3R'
}

# List of the 3 orders that your bot FAILED TO COMMIT/TRACK but might be OPEN on Kraken (Orphans)
UNTRACKED_ORPHAN_IDS = {
    'OVWAPI-YSKUD-YHXM46', 
    'O7TOIF-ARWVZ-PFF5LO', 
    'OG3E7M-NI735-KHBEEN'
}

def get_managed_orders_from_file():
    """Reads the TXIDs from txid.txt."""
    MONITOR_FILE = 'txid.txt'
    if not os.path.exists(MONITOR_FILE):
        print(f"File not found: {MONITOR_FILE}. Assuming 0 managed orders.")
        return set()
    try:
        with open(MONITOR_FILE, 'r') as f:
            # Strip whitespace and filter out empty lines
            txids = {line.strip() for line in f if line.strip()}
        return txids
    except Exception as e:
        print(f"Error reading {MONITOR_FILE}: {e}")
        return set()

def run_order_check():
    """Fetches open orders and cross-references the problematic IDs."""
    print("--- STARTING KRAKEN ORDER VERIFICATION ---")
    
    try:
        # Initialize the User client for fetching open orders
        user_client = User(API_KEY, API_SECRET)
    except Exception as e:
        print(f"❌ Failed to initialize Kraken API client: {e}")
        sys.exit(1)

    # 1. Get current open orders from Kraken
    try:
        # Use user_client for get_open_orders()
        open_orders_response = user_client.get_open_orders()
        # The response structure is often {'open': {txid: {...}, txid: {...}}}
        open_txids = set(open_orders_response.get('open', {}).keys())
        
        print(f"✅ Found {len(open_txids)} open orders on Kraken.")
    except Exception as e:
        print(f"❌ Failed to fetch open orders from Kraken: {e}")
        return

    # 2. Get managed orders from the local file
    managed_txids = get_managed_orders_from_file()
    print(f"✅ Found {len(managed_txids)} managed orders in txid.txt.")
    
    print("\n--- ANALYSIS ---")

    # A. Check for Untracked Orphans (The 3 orders from failed commits)
    open_orphans = UNTRACKED_ORPHAN_IDS.intersection(open_txids)
    
    print(f"\n[UNTRACKED ORPHANS (Failed Commits): {len(open_orphans)} Found]")
    if open_orphans:
        print("🛑 ACTION REQUIRED: These orders are OPEN on Kraken but not in your file.")
        print("   If you want to manually cancel them, run another script/command for these IDs:")
        for txid in open_orphans:
            print(f"   - {txid}")
        
    else:
        print("🎉 GREAT NEWS: The untracked orphan orders are NO LONGER OPEN on Kraken.")

    # B. Check for Canceled Ghosts (The 6 orders canceled by trimming)
    ghost_txids = CANCELED_GHOST_IDS.intersection(managed_txids)
    
    print(f"\n[CANCELED GHOSTS (Trimming Casualties): {len(ghost_txids)} Found]")
    if ghost_txids:
        print("🛑 CRITICAL ACTION REQUIRED: These orders are CANCELLED on Kraken (or were trimmed), but their IDs are still in txid.txt.")
        print("   You MUST remove these IDs from txid.txt before restarting.")
        
        # Check if any of these canceled IDs are still somehow open (just in case)
        open_ghosts = ghost_txids.intersection(open_txids)
        if open_ghosts:
             print("\n⚠️ WARNING: The following GHOSTS are ALSO OPEN on Kraken. Remove from file AND cancel manually:")
             for txid in open_ghosts:
                print(f"   - {txid}")
        
        # IDs that are only in the file and not open on Kraken
        file_only_ghosts = ghost_txids.difference(open_txids)
        if file_only_ghosts:
            print("\n❌ File Only: Remove these IDs from txid.txt:")
            for txid in file_only_ghosts:
                 print(f"   - {txid}")
    else:
        print("🎉 GREAT NEWS: All known canceled order IDs have been removed from txid.txt.")
        
    # C. Final Conclusion
    print("\n--- VERIFICATION CONCLUSION ---")
    if len(open_txids) == 40 and len(managed_txids) == 40 and (len(open_orphans) > 0 or len(ghost_txids) > 0):
        print(f"🛑 CRITICAL MISMATCH CONFIRMED: Kraken has {len(open_txids)} orders, and the file has {len(managed_txids)} orders.")
        print(f"The numbers match, but the grid is CORRUPTED.")
        print("FOLLOW ALL STEPS in the ANALYSIS above: Cancel any OPEN ORPHANS, and DELETE all GHOSTS from txid.txt.")
    elif len(open_txids) == len(managed_txids):
        print("✅ GRID APPEARS ALIGNED. If open_txids != 40, you still need to restart the bot to let it repair the missing levels.")
    else:
        print(f"❌ GRID MISMATCH: Kraken ({len(open_txids)}) != File ({len(managed_txids)}). The bot will try to fix this on restart.")
        
    print("\n--- END OF VERIFICATION ---")

if __name__ == "__main__":
    run_order_check()