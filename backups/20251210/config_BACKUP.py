import logging
from decimal import Decimal

# --- API and Trading Configuration ---
API_KEY = "IUbxEkxe7EeQATSYUqUysodAxa5SszY41xTMyUYBw1zciCLfz8gafFLc"  # Replace with your actual Kraken API Key
API_SECRET = "5vO+X9yuJvyNl9T2mdEQOL2Lfh/BUpPaCiK5K5UQEXGCohOJPtzvbHk7EfVpK8ytYak7hlo7TEr6CPkX16qVkw==" # Replace with your actual Kraken API Secret

# --- 2. TRADING PAIR & VOLUME ---
# The pair you are trading. Format: BASECURRENCYFIAT (e.g., 'XBTUSD' for Bitcoin/USD)
TRADING_PAIR = 'XBTUSDC' 

# The base volume (in BASE CURRENCY, e.g., XBT) for *each* grid order.
# NOTE: This variable is currently UNUSED. The Bot calculates volume based on your full available balance.
# Uncomment the line below and change logic in Bot.py if you want a fixed volume per order.
# BASE_VOLUME_PER_ORDER = Decimal('0.0001') 

# Number of grid levels to place initially (BOT.PY places N buys and N sells).
# Total initial orders = 2 * NUM_GRID_LEVELS
NUM_GRID_LEVELS = 5

# Minimum Profit Gap: Set floor to 0.20%
MIN_PROFIT_GAP_PERCENT = Decimal('0.0200') 

MAX_GAP_MULTIPLIER = Decimal('1.5')

# ATR Calculation Settings (Used for dynamic gap)
ATR_WINDOW = 8                          # Number of candles to look back for ATR calculation
ATR_INTERVAL_MINUTES = 15                # Interval for OHLC data (e.g., 60 = 1 hour candles)

ATR_CHANGE_THRESHOLD = Decimal('0.35')

# Thresholds for classifying market volatility (as a percentage of price)
# If ATR is below 0.35%, it's considered LOW volatility.
LOW_VOLATILITY_THRESHOLD_PERCENT = Decimal('0.0030') # 0.35% threshold

# If ATR is above 0.80%, it's considered HIGH volatility.
HIGH_VOLATILITY_THRESHOLD_PERCENT = Decimal('0.010') # 0.80% threshold

# AGGRESSIVE MULTIPLIER: Used when volatility exceeds the HIGH_VOLATILITY_THRESHOLD
ATR_HIGH_VOLATILITY_MULTIPLIER = Decimal('1.8') # 1.5x ATR gap for aggressive profit taking

ATR_MULTIPLIER = Decimal('1.2')         # Multiplier for the calculated ATR percentage

# --- 4. MONITOR SETTINGS ---
CHECK_INTERVAL_SECONDS = 45      # How often (in seconds) the monitor checks for fills.

# --- 5. FEE MANAGEMENT (NEW) ---
# Estimated Fee Rate (e.g., 0.16% or 0.0016 for Kraken Maker Fee).
# This is used to reserve a small amount of currency to prevent failed orders.
ESTIMATED_FEE_RATE = Decimal('0.0032') 

# NEW: The maximum percentage of your available balance (0.99 = 99%) to use for the initial grid.
# This reserves a small cushion to avoid "Insufficient Funds" errors due to fees/rounding.
BUDGET_UTILIZATION_CAP = Decimal('0.92')

# --- 6. LOGGING SETTINGS ---
LOG_FILE = 'grid_bot.log'       # File name for logging output
LOG_LEVEL = logging.INFO      # Set to logging.DEBUG for verbose output

# --- 6. TRADE HISTORY FILE ---
TRADE_HISTORY_FILE = 'trade_fills.txt'

# These are the *names* of the files, the path logic below makes them reliable.
MONITOR_FILE = 'managed_orders.json'
BOT_STATE_FILENAME = 'bot_state.json'

# We use the configured minimum gap as the floor for our dynamic gap
GRID_GAP_FACTOR = MIN_PROFIT_GAP_PERCENT
# Define the conservative utilization factor (e.g., use 5% of available funds per replacement cycle)
UTILIZATION_FACTOR = Decimal('0.05') 

# --- Advanced Features (Hybrid & Asymmetry) ---
GRID_ASYMMETRY_BIAS = Decimal('0.7') # 0.5 = symmetrical. 0.6 means 60% of orders will be placed on one side if a trend is detected.
TREND_FILTER_MA_WINDOW = 12 # Lookback period for MA to determine short-term trend bias
MIN_GRID_LEVELS_ALERT = 2 # Minimum level count to trigger a warning (optional)
MIN_RECENTER_PERCENT = Decimal('0.03')
# --- NEW: Hybrid Strategy and Risk Management ---
TRAILING_ATR_THRESHOLD = Decimal('2.0') # Number of ATRs away from the center price to trigger grid re-centering.

# --- DYNAMIC PROFIT ADJUSTMENT SETTINGS ---
DYNAMIC_PROFIT_ADJUSTMENT_ENABLED = True
MIN_ADJUSTMENT_WAIT_MINUTES = 30  # Wait at least 1 hour before considering adjustment
MIN_PRICE_APPROACHES = 3          # Price must approach target at least 3 times
MIN_ADJUSTMENT_SCORE = 8          # 6/10 points needed to trigger adjustment
MAX_PROFIT_REDUCTION = Decimal('0.0010')  # Never reduce profit target by more than 0.10%

MAX_PROFIT_TAKER_WAIT_HOURS = 7  # 6 hours max wait for profit-takers
MAX_PROFIT_TAKER_DISTANCE_PERCENT = 0.02

REINVEST_PERCENT = Decimal('0.3')  # Reinvest 30% of accumulated profits

ACCUMULATED_PROFIT_BASE = Decimal('0')
ACCUMULATED_PROFIT_QUOTE = Decimal('0')

FX_RATE_PAIR = "" 

# --- NEW: ERROR HANDLING CONFIGURATION (FIX FOR IMPORT ERROR) ---
# When a fatal API error (e.g., 'EGeneral:Cancel pending') occurs, should the bot
# attempt to cancel all orders before halting? Set to False to disable mass cancellation attempt.
CANCEL_ON_FATAL_ERROR = True # Added this variable

REPORTING_CURRENCY = "CAD"