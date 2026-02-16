import logging
from decimal import Decimal

# --- API and Trading Configuration ---
API_KEY = "w7927JttGcC2siVSxzANOngxXtPxfZXOcRjJgLs3THBcsYTzvI/h8y4k"  # Replace with your actual Kraken API Key
API_SECRET = "Qm0ODH3DBG2XCYIMbubcscU/zbFEEN6IknR0dpEO1kkFUd84D7tF8uP/GUB7fTn3akN4r26GXPKtcYM98axjSQ==" # Replace with your actual Kraken API Secret

# ==============================================================================
# 1. TRADING CONFIGURATION
# ==============================================================================

# The trading pair in Kraken format (e.g., 'XBTUSDC' for Bitcoin/USDC)
TRADING_PAIR = 'ETHCAD'

# Number of grid levels (N buys and N sells = 2N total orders)
# More levels = more frequent trades but smaller profits per trade
# Fewer levels = larger profits per trade but less frequent trades
NUM_GRID_LEVELS = 8  # Total orders = 10 (5 buys + 5 sells)

# How often to check for order fills (seconds)
# Shorter = faster response but more API calls
# Longer = slower response but fewer API calls
CHECK_INTERVAL_SECONDS = 60

# ==============================================================================
# 2. PROFIT TARGET & GRID SPACING
# ==============================================================================

# 🔥 PROFIT SENSITIVITY: How far apart to place orders
# Higher = larger profits per trade but fewer trades
# Lower = smaller profits per trade but more frequent trades
MIN_PROFIT_GAP_PERCENT = Decimal('0.040')  # 2.00% minimum profit per trade

MAX_PROFIT_REDUCTION = Decimal('0.0010')  # Never reduce profit target by more than 0.10%

# Maximum gap multiplier (caps extreme volatility adjustments)
MAX_GAP_MULTIPLIER = Decimal('1.5')

# ==============================================================================
# 3. VOLATILITY ADAPTIVE SETTINGS (ATR-Based)
# ==============================================================================

# ATR Calculation: Measures market volatility
ATR_WINDOW = 8                          # Look back 8 candles
ATR_INTERVAL_MINUTES = 15               # Use 15-minute candles
ATR_MULTIPLIER = Decimal('1.2')         # Base multiplier for grid spacing

# When ATR changes significantly, trigger grid reset
ATR_CHANGE_THRESHOLD = Decimal('0.35')  # 35% change triggers reset

# 🔥 VOLATILITY SENSITIVITY: Adjust these for more/less aggressive trading
LOW_VOLATILITY_THRESHOLD_PERCENT = Decimal('0.0025')    # <0.30% = LOW volatility
HIGH_VOLATILITY_THRESHOLD_PERCENT = Decimal('0.010')    # >1.00% = HIGH volatility

# Aggressive multiplier when volatility is HIGH
ATR_HIGH_VOLATILITY_MULTIPLIER = Decimal('2.0')         # 1.8x during high volatility

# ==============================================================================
# 4. CAPITAL & RISK MANAGEMENT
# ==============================================================================

# 🔥 CAPITAL UTILIZATION: How much of your balance to use
# Higher = more orders placed, potentially more profit but higher risk
# Lower = safer, more idle capital for opportunities
BUDGET_UTILIZATION_CAP = Decimal('0.98')    # Use 95% of available funds

# Fee buffer to prevent "Insufficient Funds" errors
ESTIMATED_FEE_RATE = Decimal('0.0032')      # 0.32% estimated trading fee

# ==============================================================================
# 5. GRID RECENTERING & ADJUSTMENTS
# ==============================================================================

# Recenter grid when price moves this far from center
TRAILING_ATR_THRESHOLD = Decimal('1.5')     # 2x ATR distance triggers recenter
MIN_RECENTER_PERCENT = Decimal('0.03')      # 3% minimum deviation to recenter

# Dynamic profit adjustment (moves orders closer when price approaches)
DYNAMIC_PROFIT_ADJUSTMENT_ENABLED = True
MIN_ADJUSTMENT_WAIT_MINUTES = 15            # Wait 30min between adjustments
MIN_ADJUSTMENT_SCORE = 8                    # Score needed to trigger adjustment

# Profit-taker protection (how long to wait for profit orders)
MAX_PROFIT_TAKER_WAIT_HOURS = 7             # Cancel profit orders after 7 hours
MAX_PROFIT_TAKER_DISTANCE_PERCENT = 0.02    # Cancel if 2% away from price

# ==============================================================================
# 6. TREND FOLLOWING & ASYMMETRY
# ==============================================================================

# Trend detection for asymmetric grid placement
TREND_FILTER_MA_WINDOW = 12                 # 12-period moving average

# 🔥 ASYMMETRY AGGRESSIVENESS: Place more orders in trend direction
# 0.5 = symmetrical grid (equal buys/sells)
# 0.6 = 60% of orders in trend direction (40% against)
# 0.7 = 70% in trend direction (more aggressive)
GRID_ASYMMETRY_BIAS = Decimal('0.65')

# ==============================================================================
# 7. PROFIT REINVESTMENT
# ==============================================================================

# Reinvest profits back into trading
REINVEST_PERCENT = Decimal('0.5')           # Reinvest 30% of profits
ACCUMULATED_PROFIT_BASE = Decimal('0')      # Starting base profit
ACCUMULATED_PROFIT_QUOTE = Decimal('0')     # Starting quote profit

# ==============================================================================
# 8. VOLATILITY MULTIPLIER ADVANCED TUNING
# ==============================================================================

# How much to weigh recent data vs long-term normal (0.0 to 1.0)
# Higher = more responsive to recent market conditions
VOL_RECENT_WEIGHT = Decimal('0.85')

# Maximum boost for rapid volatility increases (0.0 to 0.5)
# Example: 0.15 = up to 15% extra multiplier when vol spikes
VOL_MOMENTUM_BOOST_MAX = Decimal('0.25')

# Maximum reduction for rapid volatility decreases (0.0 to 0.3)
# Example: 0.08 = up to 8% reduction when vol crashes
VOL_MOMENTUM_DAMPEN_MAX = Decimal('0.08')

# How sensitive the z-score level changes are (0.1 to 1.0)
# Lower = more granular levels, Higher = fewer levels
VOL_Z_SCORE_INCREMENT = Decimal('0.3')

# Multipliers for each volatility level (fine-tune these)
VOL_MULTIPLIER_HYPER = Decimal('3')      # z > 3.0
VOL_MULTIPLIER_VHIGH = Decimal('2.5')      # z > 2.2
VOL_MULTIPLIER_HIGH = Decimal('2.2')       # z > 1.6
VOL_MULTIPLIER_ELEVATED = Decimal('2.0')   # z > 1.1
VOL_MULTIPLIER_MODHIGH = Decimal('1.8')    # z > 0.6
VOL_MULTIPLIER_SLIGHT_HIGH = Decimal('1.5') # z > 0.2
VOL_MULTIPLIER_NORMAL = Decimal('1.2')     # -0.2 ≤ z ≤ 0.2
VOL_MULTIPLIER_SLIGHT_LOW = Decimal('1.4') # z > -0.6
VOL_MULTIPLIER_LOW = Decimal('1.6')        # z > -1.0
VOL_MULTIPLIER_VLOW = Decimal('1.8')       # z > -1.5
VOL_MULTIPLIER_ELOW = Decimal('2.0')       # z > -2.0
VOL_MULTIPLIER_DEAD = Decimal('2.3')       # z ≤ -2.0

# Minimum and maximum allowed multipliers (safety limits)
VOL_MIN_MULTIPLIER = Decimal('1.0')
VOL_MAX_MULTIPLIER = Decimal('2.5')

# Volatility change thresholds for momentum detection
VOL_INCREASE_THRESHOLD = Decimal('0.15')   # 15% increase triggers boost
VOL_DECREASE_THRESHOLD = Decimal('0.10')   # 10% decrease triggers dampen

# ==============================================================================
# 9. UPGRADE SYSTEM CONFIGURATION
# ==============================================================================

ENABLE_UPGRADES = True

# 🔥 VIRTUAL LEVERAGE: Increases order sizes without borrowing
# Higher = more potential profit but higher risk
ENABLE_VIRTUAL_LEVERAGE = True
VIRTUAL_LEVERAGE_MULTIPLIER = 2.8           # Target 2.4x leverage
MAX_VIRTUAL_LEVERAGE = 3.0                  # Never exceed 3x
MIN_VIRTUAL_LEVERAGE = 1.0                  # Minimum 1x (no leverage)

# CAPITAL EFFICIENCY: Minimizes idle capital
ENABLE_CAPITAL_EFFICIENCY = True
MAX_IDLE_CAPITAL_PERCENT = 0.012            # Target <1% idle capital
REBALANCE_TRIGGER_PERCENT = 0.20            # Rebalance if >5% idle
MIN_CAPITAL_UTILIZATION = 0.95              # Target >90% utilization

# MOMENTUM ANALYZER: Adjusts grid based on price momentum
ENABLE_MOMENTUM_ANALYZER = True
MOMENTUM_LOOKBACK_PERIODS = 20              # Look back 20 periods
MIN_MOMENTUM_STRENGTH = 0.001               # 0.1% momentum required
MOMENTUM_CONFIDENCE_THRESHOLD = 0.7         # 70% confidence needed

# ==============================================================================
# 10. SAFETY & ERROR HANDLING
# ==============================================================================

# Emergency shutdown protection
EMERGENCY_SHUTDOWN_ENABLED = True
MAX_DAILY_DRAWDOWN = 0.20                   # Shutdown if 20% daily loss
MAX_POSITION_IMBALANCE = 0.70               # Max 70/30 buy/sell ratio

# Cancel orders on fatal errors
CANCEL_ON_FATAL_ERROR = True

# ==============================================================================
# 10. LOGGING & FILES
# ==============================================================================

import logging
LOG_FILE = 'grid_bot.log'
LOG_LEVEL = logging.INFO                    # Change to DEBUG for detailed logs

# File names for state persistence
TRADE_HISTORY_FILE = 'trade_fills.txt'
MONITOR_FILE = 'managed_orders.json'
BOT_STATE_FILENAME = 'bot_state.json'

# ==============================================================================
# QUICK PROFIT OPTIMIZATION GUIDE
# ==============================================================================
"""
🔥 TO INCREASE PROFITS (MORE AGGRESSIVE):
1. Reduce MIN_PROFIT_GAP_PERCENT to 0.0150 (1.5%) for more frequent trades
2. Increase NUM_GRID_LEVELS to 6-8 for denser grid
3. Increase VIRTUAL_LEVERAGE_MULTIPLIER to 2.8-3.0
4. Reduce MIN_ADJUSTMENT_WAIT_MINUTES to 15 for faster adjustments
5. Increase BUDGET_UTILIZATION_CAP to 0.98

⚠️ TO REDUCE RISK (MORE CONSERVATIVE):
1. Increase MIN_PROFIT_GAP_PERCENT to 0.0250 (2.5%)
2. Reduce NUM_GRID_LEVELS to 3-4
3. Set VIRTUAL_LEVERAGE_MULTIPLIER to 1.5
4. Increase ATR_HIGH_VOLATILITY_MULTIPLIER to 2.0 for wider spacing in volatility
5. Set BUDGET_UTILIZATION_CAP to 0.90

📈 FOR TRENDING MARKETS:
1. Increase GRID_ASYMMETRY_BIAS to 0.75
2. Reduce TRAILING_ATR_THRESHOLD to 1.5 for faster recentering
3. Increase REINVEST_PERCENT to 0.5

📉 FOR RANGING MARKETS:
1. Set GRID_ASYMMETRY_BIAS to 0.55 (almost symmetrical)
2. Increase NUM_GRID_LEVELS for more reversal trades
3. Reduce MIN_PROFIT_GAP_PERCENT for smaller profits per trade
"""