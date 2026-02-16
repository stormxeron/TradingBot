# test_leverage.py
import sys
sys.path.append('.')
from decimal import Decimal

def simulate_leverage():
    """Simulate what leverage should do."""
    # Your actual balances from logs
    base_balance = Decimal('0.01425966')  # XBT
    quote_balance = Decimal('706.673644')  # USDC
    current_price = Decimal('92789.125')
    num_levels = 5
    leverage = Decimal('2.40')
    
    print(f"\n=== LEVERAGE SIMULATION ===")
    print(f"Balance: {base_balance} XBT, {quote_balance} USDC")
    print(f"Price: {current_price}")
    print(f"Requested leverage: {leverage}x")
    
    # Without leverage calculation
    base_buy_volume = quote_balance / (Decimal(num_levels) * current_price)
    base_sell_volume = base_balance / Decimal(num_levels)
    
    print(f"\nWithout leverage:")
    print(f"  Buy volume: {base_buy_volume:.8f} XBT")
    print(f"  Sell volume: {base_sell_volume:.8f} XBT")
    
    # With leverage
    leveraged_buy = base_buy_volume * leverage
    leveraged_sell = base_sell_volume * leverage
    
    print(f"\nWith {leverage}x leverage:")
    print(f"  Buy volume: {leveraged_buy:.8f} XBT")
    print(f"  Sell volume: {leveraged_sell:.8f} XBT")
    
    # Check budget
    total_buy_cost = leveraged_buy * current_price * Decimal(num_levels)
    total_sell_value = leveraged_sell * Decimal(num_levels)
    
    print(f"\nBudget check:")
    print(f"  Total buy cost: {total_buy_cost:.2f} USDC")
    print(f"  Available quote: {quote_balance:.2f} USDC")
    print(f"  Total sell value: {total_sell_value:.8f} XBT")
    print(f"  Available base: {base_balance:.8f} XBT")
    
    # Safety cap (92%)
    safe_quote = quote_balance * Decimal('0.92')
    safe_base = base_balance * Decimal('0.92')
    
    print(f"\nWith 92% safety cap:")
    print(f"  Available (capped): {safe_quote:.2f} USDC, {safe_base:.8f} XBT")
    
    if total_buy_cost > safe_quote:
        print(f"  ❌ Buy cost exceeds available quote by {(total_buy_cost/safe_quote-1)*100:.1f}%")
    else:
        print(f"  ✅ Buy cost fits within budget")
        
    if total_sell_value > safe_base:
        print(f"  ❌ Sell value exceeds available base by {(total_sell_value/safe_base-1)*100:.1f}%")
    else:
        print(f"  ✅ Sell value fits within budget")

if __name__ == "__main__":
    simulate_leverage()