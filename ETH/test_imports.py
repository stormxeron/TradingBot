# debug_imports.py
import os
import sys

print("🔍 DEBUG UTILS IMPORT:")
print(f"Current directory: {os.getcwd()}")
print(f"ETH folder: {os.path.dirname(os.path.abspath(__file__))}")

# List files in ETH folder
print("\n📁 Files in ETH folder:")
for f in os.listdir('.'):
    if 'utils' in f.lower():
        print(f"  ✅ Found: {f}")

# Check Python path
print("\n🔧 Python sys.path:")
for p in sys.path:
    print(f"  {p}")

# Try to import utils
print("\n🔄 Attempting to import utils...")
try:
    import utils
    print(f"✅ SUCCESS: utils imported from {utils.__file__}")
    
    # Check what's in utils
    print(f"✅ utils has get_ohlc_data_cached: {hasattr(utils, 'get_ohlc_data_cached')}")
    print(f"✅ utils has _safe_api_call: {hasattr(utils, '_safe_api_call')}")
    
except ImportError as e:
    print(f"❌ FAILED: {e}")
    print("\n💡 Try absolute import:")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("utils", "./utils.py")
        utils = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(utils)
        print("✅ SUCCESS with absolute import")
    except Exception as e2:
        print(f"❌ Still failed: {e2}")