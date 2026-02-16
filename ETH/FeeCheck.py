
"""
Standalone script to check Kraken fee rates using your config credentials
"""

import sys
import os
import logging
from decimal import Decimal

try:
    from kraken.spot import User
    from config_eth import API_KEY, API_SECRET, TRADING_PAIR
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Make sure this script is in the same directory as your config.py")
    sys.exit(1)

# Setup basic logging
logging.basicConfig(level=logging.DEBUG, format='%(message)s')

def check_fees():
    """Check what fee data Kraken is actually returning"""
    print("🔍 KRAKEN FEE CHECKER")
    print("=" * 50)
    
    try:
        # Initialize Kraken client with your credentials
        user_client = User(key=API_KEY, secret=API_SECRET)
        print(f"✅ API client initialized for pair: {TRADING_PAIR}")
        
        # Get trade volume data
        print("\n📡 Making API call to get_trade_volume()...")
        response = user_client.get_trade_volume()
        
        print(f"\n📦 FULL API RESPONSE:")
        print(f"   Type: {type(response)}")
        print(f"   Response: {response}")
        
        # Analyze the response structure
        if response is None:
            print("❌ Response is None!")
            return
            
        print(f"\n🔍 RESPONSE KEYS: {list(response.keys()) if isinstance(response, dict) else 'Not a dict'}")
        
        # Check for errors
        if 'error' in response:
            print(f"❌ ERRORS: {response['error']}")
        else:
            print("✅ No errors in response")
            
        # Look for fees data in different locations
        fees_data = None
        if 'fees' in response:
            fees_data = response['fees']
            print(f"✅ Found 'fees' key directly: {fees_data}")
        elif 'result' in response and 'fees' in response['result']:
            fees_data = response['result']['fees']
            print(f"✅ Found 'fees' key in 'result': {fees_data}")
        else:
            print("❌ No 'fees' key found in any expected location")
            
        # If we found fees data, extract the specific pair
        if fees_data and TRADING_PAIR in fees_data:
            pair_fees = fees_data[TRADING_PAIR]
            print(f"\n💰 FEES FOR {TRADING_PAIR}:")
            print(f"   Maker: {pair_fees.get('maker', 'Not found')}")
            print(f"   Taker: {pair_fees.get('taker', 'Not found')}")
        elif fees_data:
            print(f"\n📊 Available fee pairs: {list(fees_data.keys())}")
            print(f"❌ {TRADING_PAIR} not found in fees data")
        else:
            print(f"\n❌ No fees data available for any pairs")
            
    except Exception as e:
        print(f"💥 ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_fees()