import time
import json
import os
from decimal import Decimal
from datetime import datetime, timedelta

try:
    from config import API_KEY, API_SECRET
    from kraken.spot import User, Market
except ImportError as e:
    print(f"❌ Import error: {e}")
    exit(1)

# Portfolio data file
PORTFOLIO_DATA_FILE = 'portfolio_baseline.json'

def get_current_prices_in_cad(market_client):
    """Get current market prices in CAD"""
    try:
        # Get ETH/CAD price
        eth_ticker = market_client.get_ticker(pair='XETHZCAD')
        eth_price_cad = Decimal(eth_ticker['XETHZCAD']['a'][0])  # Ask price in CAD
        
        # Get BTC/CAD price (via BTC/USD and USD/CAD)
        btc_ticker = market_client.get_ticker(pair='XXBTZUSD')
        btc_price_usd = Decimal(btc_ticker['XXBTZUSD']['a'][0])  # Ask price in USD
        
        # Get USD/CAD rate - use USDTCAD or CADUSD pairs
        try:
            # Try different USD/CAD pairs
            usdcad_ticker = market_client.get_ticker(pair='USDTZCAD')  # USDT to CAD
            usd_cad_rate = Decimal(usdcad_ticker['USDTZCAD']['a'][0])
        except:
            try:
                usdcad_ticker = market_client.get_ticker(pair='USDCZCAD')  # USDC to CAD
                usd_cad_rate = Decimal(usdcad_ticker['USDCZCAD']['a'][0])
            except:
                try:
                    cadusd_ticker = market_client.get_ticker(pair='ZCADZUSD')  # CAD to USD
                    cad_usd_rate = Decimal(cadusd_ticker['ZCADZUSD']['a'][0])
                    usd_cad_rate = Decimal('1') / cad_usd_rate  # Invert to get USD/CAD
                except:
                    # Fallback: Use fixed rate
                    usd_cad_rate = Decimal('1.35')
                    print("⚠️  Using fallback USD/CAD rate: 1.35")
        
        btc_price_cad = btc_price_usd * usd_cad_rate
        
        return {
            'ETH_CAD': eth_price_cad,
            'BTC_CAD': btc_price_cad,
            'USD_CAD': usd_cad_rate
        }
    except Exception as e:
        print(f"❌ Price fetch error: {e}")
        # Fallback prices in CAD
        return {
            'ETH_CAD': Decimal('4150'),
            'BTC_CAD': Decimal('87000'),
            'USD_CAD': Decimal('1.35')
        }

def get_current_portfolio(user_client):
    """Get current portfolio balances"""
    try:
        balances = user_client.get_account_balance()
        
        # Convert all values to Decimal safely
        portfolio = {
            'timestamp': datetime.now().isoformat()
        }
        
        # Safely convert each balance to Decimal
        for asset in ['XETH', 'XXBT', 'ZCAD', 'USDC', 'ZUSD']:
            balance_str = balances.get(asset, '0')
            try:
                portfolio[asset] = Decimal(str(balance_str))
            except:
                portfolio[asset] = Decimal('0')
                print(f"⚠️  Could not convert {asset} balance: {balance_str}")
        
        return portfolio
    except Exception as e:
        print(f"❌ Balance fetch error: {e}")
        return None

def calculate_portfolio_value_cad(portfolio, prices):
    """Calculate total portfolio value in CAD"""
    eth_value_cad = portfolio.get('XETH', Decimal('0')) * prices['ETH_CAD']
    btc_value_cad = portfolio.get('XXBT', Decimal('0')) * prices['BTC_CAD']
    cad_value = portfolio.get('ZCAD', Decimal('0'))
    
    # Convert USD assets to CAD
    usdc_value_cad = portfolio.get('USDC', Decimal('0')) * prices['USD_CAD']  # USDC ≈ USD
    usd_value_cad = portfolio.get('ZUSD', Decimal('0')) * prices['USD_CAD']
    
    total_cad = eth_value_cad + btc_value_cad + cad_value + usdc_value_cad + usd_value_cad
    
    return total_cad

def save_baseline_portfolio(portfolio, prices):
    """Save current portfolio as baseline for next cycle"""
    # Convert Decimals to strings for JSON serialization
    portfolio_str = {}
    for key, value in portfolio.items():
        if key != 'timestamp':
            portfolio_str[key] = str(value)
        else:
            portfolio_str[key] = value
    
    prices_str = {k: str(v) for k, v in prices.items()}
    
    baseline_data = {
        'portfolio': portfolio_str,
        'prices': prices_str,
        'timestamp': portfolio['timestamp']
    }
    
    with open(PORTFOLIO_DATA_FILE, 'w') as f:
        json.dump(baseline_data, f, indent=2)
    
    print(f"✅ Baseline portfolio saved for next cycle")

def load_baseline_portfolio():
    """Load baseline portfolio from file"""
    if not os.path.exists(PORTFOLIO_DATA_FILE):
        return None
    
    try:
        with open(PORTFOLIO_DATA_FILE, 'r') as f:
            data = json.load(f)
        
        # Safely convert strings back to Decimal
        portfolio_dec = {}
        for key, value in data['portfolio'].items():
            if key != 'timestamp':
                try:
                    portfolio_dec[key] = Decimal(str(value))
                except:
                    portfolio_dec[key] = Decimal('0')
                    print(f"⚠️  Could not convert baseline {key}: {value}")
            else:
                portfolio_dec[key] = value
        
        prices_dec = {}
        for key, value in data['prices'].items():
            try:
                prices_dec[key] = Decimal(str(value))
            except:
                prices_dec[key] = Decimal('0')
                print(f"⚠️  Could not convert baseline price {key}: {value}")
        
        return {
            'portfolio': portfolio_dec,
            'prices': prices_dec,
            'timestamp': data['timestamp']
        }
    except Exception as e:
        print(f"❌ Error loading baseline: {e}")
        # Try to delete corrupt file
        try:
            os.remove(PORTFOLIO_DATA_FILE)
            print("🗑️  Deleted corrupt baseline file")
        except:
            pass
        return None

def ask_update_baseline():
    """Ask user if they want to update the baseline"""
    while True:
        response = input("\n🔄 Update baseline for next run? (y/n): ").strip().lower()
        if response in ['y', 'yes', '1']:
            return True
        elif response in ['n', 'no', '0']:
            return False
        else:
            print("❌ Please enter 'y' or 'n'")

def get_time_since_baseline(baseline_timestamp):
    """Calculate time elapsed since baseline was set"""
    baseline_time = datetime.fromisoformat(baseline_timestamp)
    current_time = datetime.now()
    time_diff = current_time - baseline_time
    
    hours = time_diff.total_seconds() / 3600
    days = hours / 24
    
    if days >= 1:
        return f"{days:.1f} days"
    else:
        return f"{hours:.1f} hours"

def analyze_performance_since_baseline(user_client, market_client):
    """Main function to analyze performance since last baseline"""
    
    print("🤖 PORTFOLIO PERFORMANCE ANALYZER (CAD) - MANUAL MODE")
    print("=" * 70)
    
    # 1. Get current portfolio and prices in CAD
    current_portfolio = get_current_portfolio(user_client)
    if not current_portfolio:
        return
    
    current_prices = get_current_prices_in_cad(market_client)
    current_value_cad = calculate_portfolio_value_cad(current_portfolio, current_prices)
    
    print(f"💰 CURRENT PORTFOLIO (CAD):")
    print(f"   ETH: {current_portfolio['XETH']:.6f} @ ${current_prices['ETH_CAD']:.2f} CAD = ${current_portfolio['XETH'] * current_prices['ETH_CAD']:.2f} CAD")
    print(f"   BTC: {current_portfolio['XXBT']:.8f} @ ${current_prices['BTC_CAD']:.2f} CAD = ${current_portfolio['XXBT'] * current_prices['BTC_CAD']:.2f} CAD")
    print(f"   CAD: ${current_portfolio['ZCAD']:.2f} CAD")
    if current_portfolio['USDC'] > Decimal('0.01'):
        print(f"   USDC: {current_portfolio['USDC']:.2f} USD @ {current_prices['USD_CAD']:.4f} = ${current_portfolio['USDC'] * current_prices['USD_CAD']:.2f} CAD")
    if current_portfolio.get('ZUSD', Decimal('0')) > Decimal('0.01'):
        print(f"   USD: {current_portfolio['ZUSD']:.2f} USD @ {current_prices['USD_CAD']:.4f} = ${current_portfolio['ZUSD'] * current_prices['USD_CAD']:.2f} CAD")
    print(f"   TOTAL: ${current_value_cad:.2f} CAD")
    
    # 2. Load baseline
    baseline_data = load_baseline_portfolio()
    
    if not baseline_data:
        print(f"\n📝 No baseline found. Creating initial baseline...")
        save_baseline_portfolio(current_portfolio, current_prices)
        print(f"   Run this again to see performance vs this baseline!")
        return
    
    baseline_portfolio = baseline_data['portfolio']
    baseline_prices = baseline_data['prices']
    baseline_value_cad = calculate_portfolio_value_cad(baseline_portfolio, baseline_prices)
    
    # Show time since baseline
    time_elapsed = get_time_since_baseline(baseline_data['timestamp'])
    print(f"\n⏰ Baseline was set {time_elapsed} ago")
    
    print(f"\n📊 BASELINE (from {time_elapsed} ago) in CAD:")
    print(f"   ETH: {baseline_portfolio['XETH']:.6f} @ ${baseline_prices['ETH_CAD']:.2f} CAD = ${baseline_portfolio['XETH'] * baseline_prices['ETH_CAD']:.2f} CAD")
    print(f"   BTC: {baseline_portfolio['XXBT']:.8f} @ ${baseline_prices['BTC_CAD']:.2f} CAD = ${baseline_portfolio['XXBT'] * baseline_prices['BTC_CAD']:.2f} CAD")
    print(f"   CAD: ${baseline_portfolio['ZCAD']:.2f} CAD")
    if baseline_portfolio['USDC'] > Decimal('0.01'):
        print(f"   USDC: {baseline_portfolio['USDC']:.2f} USD @ {baseline_prices['USD_CAD']:.4f} = ${baseline_portfolio['USDC'] * baseline_prices['USD_CAD']:.2f} CAD")
    if baseline_portfolio.get('ZUSD', Decimal('0')) > Decimal('0.01'):
        print(f"   USD: {baseline_portfolio['ZUSD']:.2f} USD @ {baseline_prices['USD_CAD']:.4f} = ${baseline_portfolio['ZUSD'] * baseline_prices['USD_CAD']:.2f} CAD")
    print(f"   TOTAL: ${baseline_value_cad:.2f} CAD")
    
    # 3. Calculate performance since baseline in CAD
    total_pnl_cad = current_value_cad - baseline_value_cad
    
    # 4. Get trades since baseline timestamp
    baseline_time = datetime.fromisoformat(baseline_data['timestamp'])
    start_ts = int(baseline_time.timestamp())
    
    try:
        trades_response = user_client.get_trades_history(start=start_ts)
        raw_trades = trades_response.get('trades', {})
        
        # Calculate trading P&L in CAD
        trading_profit_cad = Decimal('0')
        total_fees_cad = Decimal('0')
        trade_count = 0
        
        kraken_pairs = ['XETHZCAD', 'XBTUSDC', 'XBTZUSD']
        for txid, trade in raw_trades.items():
            if trade['pair'] in kraken_pairs:
                trade_count += 1
                side = trade['type']
                cost = Decimal(str(trade['cost']))
                fee = Decimal(str(trade['fee']))
                pair = trade['pair']
                
                # Convert everything to CAD
                if pair == 'XETHZCAD':  # Already in CAD
                    cost_cad = cost
                    fee_cad = fee
                elif pair in ['XBTUSDC', 'XBTZUSD']:  # Convert from USD to CAD
                    cost_cad = cost * current_prices['USD_CAD']
                    fee_cad = fee * current_prices['USD_CAD']
                else:
                    cost_cad = cost  # Fallback
                    fee_cad = fee
                
                if side == 'buy':
                    trading_profit_cad -= cost_cad + fee_cad
                else:  # sell
                    trading_profit_cad += cost_cad - fee_cad
                
                total_fees_cad += fee_cad
    except Exception as e:
        print(f"❌ Trade history error: {e}")
        trading_profit_cad = Decimal('0')
        total_fees_cad = Decimal('0')
        trade_count = 0
    
    # 5. Calculate market impact vs trading impact in CAD
    # What portfolio would be worth if we held (no trades) - using CURRENT prices
    hold_value_cad = calculate_portfolio_value_cad(baseline_portfolio, current_prices)
    market_impact_cad = hold_value_cad - baseline_value_cad  # Price change effect
    trading_impact_cad = total_pnl_cad - market_impact_cad   # Bot trading effect
    
    # 6. Display comprehensive analysis in CAD
    print("\n" + "=" * 70)
    print(f"🎯 PERFORMANCE SINCE BASELINE ({time_elapsed}) - CAD")
    print("=" * 70)
    
    print(f"📈 TOTAL P&L: ${total_pnl_cad:+.2f} CAD")
    print(f"   Market Impact: ${market_impact_cad:+.2f} CAD (price changes)")
    print(f"   Trading Impact: ${trading_impact_cad:+.2f} CAD (bot activity)")
    
    print(f"\n🤖 BOT PERFORMANCE:")
    print(f"   Trades Executed: {trade_count}")
    print(f"   Gross Trading P&L: ${trading_profit_cad + total_fees_cad:+.2f} CAD")
    print(f"   Fees Paid: ${total_fees_cad:.2f} CAD")
    print(f"   Net Trading Profit: ${trading_profit_cad:+.2f} CAD")
    
    print(f"\n📊 ASSET ALLOCATION CHANGES:")
    eth_change = current_portfolio['XETH'] - baseline_portfolio['XETH']
    btc_change = current_portfolio['XXBT'] - baseline_portfolio['XXBT']
    cad_change = current_portfolio['ZCAD'] - baseline_portfolio['ZCAD']
    
    print(f"   ETH: {eth_change:+.6f} ({baseline_portfolio['XETH']:.6f} → {current_portfolio['XETH']:.6f})")
    print(f"   BTC: {btc_change:+.8f} ({baseline_portfolio['XXBT']:.8f} → {current_portfolio['XXBT']:.8f})")
    print(f"   CAD: ${cad_change:+.2f} CAD")
    
    print(f"\n💡 INTERPRETATION:")
    if trading_impact_cad > 0:
        print(f"   ✅ Bot added ${trading_impact_cad:.2f} CAD of value!")
    else:
        print(f"   ❌ Bot destroyed ${abs(trading_impact_cad):.2f} CAD of value")
    
    if market_impact_cad > 0:
        print(f"   📈 Market helped: +${market_impact_cad:.2f} CAD")
    else:
        print(f"   📉 Market hurt: ${market_impact_cad:.2f} CAD")
    
    # 7. Ask user if they want to update baseline
    if ask_update_baseline():
        print(f"\n💾 Updating baseline...")
        save_baseline_portfolio(current_portfolio, current_prices)
        print(f"   New baseline set! Run anytime to track from this point.")
    else:
        print(f"\n📊 Baseline unchanged. Run again to continue tracking from same baseline.")
    
    # 8. Save detailed report to file
    save_detailed_report_cad({
        'timestamp': datetime.now().isoformat(),
        'time_elapsed': time_elapsed,
        'total_pnl_cad': float(total_pnl_cad),
        'market_impact_cad': float(market_impact_cad),
        'trading_impact_cad': float(trading_impact_cad),
        'trading_profit_cad': float(trading_profit_cad),
        'trade_count': trade_count,
        'fees_paid_cad': float(total_fees_cad),
        'current_value_cad': float(current_value_cad),
        'baseline_value_cad': float(baseline_value_cad),
        'usd_cad_rate': float(current_prices['USD_CAD'])
    })

def save_detailed_report_cad(report_data):
    """Save detailed performance report in CAD"""
    report_file = 'performance_history_cad.json'
    
    # Load existing history or create new
    if os.path.exists(report_file):
        with open(report_file, 'r') as f:
            history = json.load(f)
    else:
        history = []
    
    history.append(report_data)
    
    # Keep only last 30 days
    if len(history) > 30:
        history = history[-30:]
    
    with open(report_file, 'w') as f:
        json.dump(history, f, indent=2)
    
    print(f"✅ Performance report saved to {report_file}")

if __name__ == "__main__":
    user_client = User(key=API_KEY, secret=API_SECRET)
    market_client = Market()
    
    analyze_performance_since_baseline(user_client, market_client)
    
    print(f"\n🎯 Run this script anytime to track performance!")