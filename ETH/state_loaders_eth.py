import json
import os
import logging
import sys
import time
from decimal import Decimal
from typing import Dict, Any, Union, Tuple, Optional

from config_eth import MONITOR_FILE, BOT_STATE_FILENAME

# ==============================================================================
# 1. CORE UTILITIES: PATHS and JSON ENCODING
# ==============================================================================

class DecimalEncoder(json.JSONEncoder):
    """
    REQUIRED: Custom encoder to convert Decimal objects to strings when saving 
    data to JSON files (since JSON doesn't support Decimal natively).
    """
    def default(self, o):
        if isinstance(o, Decimal):
            return str(o)
        return super(DecimalEncoder, self).default(o)

def _get_persistent_file_path(filename: str) -> str:
    """
    FIX: Resolves the file name to an absolute path, anchored to the directory 
    of the main bot script being executed. This solves the "file not found" issue.
    """
    # sys.argv[0] is the path to the script being executed
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    # Combine the script's directory with the file name to get the full path
    full_path = os.path.join(script_dir, filename)
    logging.debug(f"Resolved file path for '{filename}': {full_path}")
    return full_path

def _convert_strings_to_decimal(data: Dict[str, Any], keys: list) -> Dict[str, Any]:
    """Helper function to convert specific string values (loaded from JSON) back to Decimal."""
    for key in keys:
        if key in data and isinstance(data[key], str):
            try:
                data[key] = Decimal(data[key])
            except Exception:
                logging.error(f"Failed to convert stored string for key '{key}' to Decimal. Value: '{data[key]}'.")
    return data

# ==============================================================================
# 2. BOT METADATA STATE LOADER/SAVER
# ==============================================================================

def load_bot_state(default_atr_factor: Decimal, filename: str = 'bot_state.json') -> Dict[str, Any]:
    """
    Load bot state from JSON file, converting string numbers back to Decimals.
    """
    try:
        with open(BOT_STATE_FILENAME, 'r') as f:
            data = json.load(f)
        
        # Convert string numbers back to Decimals
        bot_state = {}
        for key, value in data.items():
            if isinstance(value, (int, float)):
                bot_state[key] = Decimal(str(value))
            elif isinstance(value, str):
                # Try to convert string to Decimal if it's numeric
                try:
                    bot_state[key] = Decimal(value)
                except:
                    bot_state[key] = value
            elif key == 'volatility_history' and isinstance(value, list):
                # NEW: Convert list of strings to list of Decimals
                bot_state[key] = [Decimal(str(v)) for v in value]
            else:
                bot_state[key] = value
        
        # NEW: Ensure volatility_history exists
        if 'volatility_history' not in bot_state:
            bot_state['volatility_history'] = []

        if 'accumulated_profit_base' not in bot_state:
            bot_state['accumulated_profit_base'] = Decimal('0')
        else:
            bot_state['accumulated_profit_base'] = Decimal(str(bot_state['accumulated_profit_base']))
            
        if 'accumulated_profit_quote' not in bot_state:
            bot_state['accumulated_profit_quote'] = Decimal('0')
        else:
            bot_state['accumulated_profit_quote'] = Decimal(str(bot_state['accumulated_profit_quote']))
        if 'bot_start_time' not in bot_state:
            bot_state['bot_start_time'] = Decimal(time.time())
        
        logging.info(f"Bot state loaded from {filename}. Volatility history: {len(bot_state['volatility_history'])} periods")
        return bot_state
        
    except FileNotFoundError:
        logging.warning(f"⚠️ WARNING - Bot state file '{filename}' not found. Initializing default state.")
        return {
            'last_atr_gap_factor': default_atr_factor,
            'last_center_price': Decimal('0'),
            'volatility_history': [],  # NEW: Add empty volatility history
            'accumulated_profit_base': Decimal('0'),
            'accumulated_profit_quote': Decimal('0'),
            'bot_start_time': Decimal(time.time())
        }
    except Exception as e:
        logging.error(f"Error loading bot state: {e}. Initializing default state.")
        return {
            'last_atr_gap_factor': default_atr_factor,
            'last_center_price': Decimal('0'),
            'volatility_history': [],  # NEW: Add empty volatility history
            'accumulated_profit_base': Decimal('0'),
            'accumulated_profit_quote': Decimal('0'),
            'bot_start_time': Decimal(time.time())
        }

def save_bot_state(state: Dict[str, Any], accumulated_profit_base: Decimal = Decimal('0'), accumulated_profit_quote: Decimal = Decimal('0')):
    """
    Saves the current bot state to bot_state.json using the custom DecimalEncoder.
    """

    state['accumulated_profit_base'] = accumulated_profit_base
    state['accumulated_profit_quote'] = accumulated_profit_quote

    file_path = _get_persistent_file_path(BOT_STATE_FILENAME)
    try:
        with open(file_path, 'w') as f:
            # Use DecimalEncoder to correctly serialize Decimal objects
            json.dump(state, f, indent=4, cls=DecimalEncoder)
        logging.debug(f"State successfully saved to {file_path}. Volatility history: {len(state.get('volatility_history', []))} periods. Profits: {accumulated_profit_base} base, {accumulated_profit_quote} quote")
    except Exception as e:
        logging.error(f"Failed to save bot state to {file_path}: {e}")

def load_managed_orders() -> Dict[str, Dict[str, Decimal]]:
    """Loads managed orders from the JSON file."""
    global managed_orders
    if os.path.exists(MONITOR_FILE):
        try:
            with open(MONITOR_FILE, 'r') as f:
                data = json.load(f)
                # Convert price and volume strings back to Decimal objects
                for txid, order_data in data.items():
                    order_data['price'] = Decimal(order_data['price'])
                    order_data['volume'] = Decimal(order_data['volume'])
                managed_orders = data
                logging.info(f"Loaded {len(managed_orders)} managed orders from {MONITOR_FILE}.")
                return managed_orders
        except Exception as e:
            logging.error(f"Error loading managed orders file {MONITOR_FILE}: {e}")
            # If the file is corrupt, better to start clean than use bad data
            managed_orders = {}
            return {}
    else:
        logging.info("Managed orders file not found. Starting fresh.")
        return {}

# ==============================================================================
# 3. MANAGED ORDERS STATE LOADER/SAVER
# ==============================================================================

def save_managed_orders_to_file(managed_orders: Dict[str, Any], filename: str = 'managed_orders.json') -> None:
    """
    Save managed orders to JSON file, ensuring all Decimals are converted to strings.
    """
    try:
        # Convert all Decimal values to strings for JSON serialization
        serializable_orders = {}
        for txid, order_data in managed_orders.items():
            serializable_order = {}
            for key, value in order_data.items():
                if isinstance(value, Decimal):
                    serializable_order[key] = str(value)
                else:
                    serializable_order[key] = value
            serializable_orders[txid] = serializable_order
        
        with open(filename, 'w') as f:
            json.dump(serializable_orders, f, indent=2)
        logging.info(f"Saved {len(managed_orders)} managed orders to {filename}")
    except Exception as e:
        logging.error(f"Error saving managed orders: {e}")

def load_managed_orders_from_file(filename: str = 'managed_orders.json') -> Dict[str, Any]:
    """
    Load managed orders from JSON file, converting string numbers back to Decimals.
    """
    try:
        with open(filename, 'r') as f:
            data = json.load(f)
        
        # Convert string numbers back to Decimals
        managed_orders = {}
        for txid, order_data in data.items():
            converted_order = {}
            for key, value in order_data.items():
                if key in ['price', 'volume'] and isinstance(value, str):
                    # Convert price and volume back to Decimal
                    try:
                        converted_order[key] = Decimal(value)
                    except:
                        converted_order[key] = value
                else:
                    converted_order[key] = value
            managed_orders[txid] = converted_order
        
        logging.info(f"Loaded {len(managed_orders)} managed orders from {filename}")
        return managed_orders
        
    except FileNotFoundError:
        logging.warning(f"Managed orders file '{MONITOR_FILE}' not found. Starting with empty grid.")
        return {}
    except Exception as e:
        logging.error(f"Error loading managed orders: {e}. Starting with empty grid.")
        return {}

def cleanup_managed_orders_file():
    """
    One-time function to fix any existing managed_orders file with string values.
    """
    try:
        if os.path.exists('managed_orders.json'):
            with open('managed_orders.json', 'r') as f:
                data = json.load(f)
            
            # Check if any values need conversion
            needs_fix = False
            for txid, order_data in data.items():
                price = order_data.get('price')
                if isinstance(price, str):
                    needs_fix = True
                    break
            
            if needs_fix:
                logging.info("Fixing managed_orders file with string values...")
                fixed_orders = load_managed_orders_from_file()  # This will convert strings to Decimals
                save_managed_orders_to_file(fixed_orders)  # This will save them properly
                logging.info("Managed orders file fixed.")
    except Exception as e:
        logging.error(f"Error during managed_orders cleanup: {e}")
