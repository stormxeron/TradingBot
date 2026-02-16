import sys
import importlib.util
import subprocess

print("=" * 50)
print("PYTHON ENVIRONMENT DIAGNOSTIC TOOL")
print("=" * 50)

# 1. BASIC PYTHON INFO
print(f"\n[1] Python Environment:")
print(f"   Executable: {sys.executable}")
print(f"   Version: {sys.version.split()[0]}")
print(f"   Platform: {sys.platform}")

# 2. CHECK PYTHON PATH
print(f"\n[2] Python Path (first 3 entries):")
for i, path in enumerate(sys.path[:3]):
    print(f"   {i}. {path}")

# 3. CHECK MODULE IMPORT FUNCTION
def can_import(module_name):
    """Check if a module can be imported"""
    try:
        spec = importlib.util.find_spec(module_name)
        return spec is not None
    except:
        return False

# 4. CHECK KRAKEN PACKAGE (what your bot actually uses)
print("\n[3] Checking Kraken API Packages:")

# Try different possible package names
kraken_packages = [
    'kraken',           # kraken-api package
    'kraken.spot',      # The actual module you import from
    'kraken_api',       # Alternative name
    'krakenapi',        # Another alternative
]

found_kraken = False
for pkg in kraken_packages:
    if can_import(pkg):
        print(f"   ✓ Found: {pkg}")
        found_kraken = True
        
        # Try to import it to get version
        try:
            module = importlib.import_module(pkg.split('.')[0])  # Get root module
            version = getattr(module, '__version__', 'Unknown')
            print(f"     Version: {version}")
            
            # If it's 'kraken', try the specific imports your bot uses
            if pkg == 'kraken':
                print("\n   Testing bot imports from 'kraken.spot':")
                test_imports = ['Trade', 'User', 'Market']
                for import_name in test_imports:
                    try:
                        exec(f"from kraken.spot import {import_name}")
                        print(f"     ✓ {import_name}: OK")
                    except ImportError as e:
                        print(f"     ✗ {import_name}: {str(e)[:50]}...")
        except Exception as e:
            print(f"     Error checking {pkg}: {e}")
    else:
        print(f"   ✗ Missing: {pkg}")

if not found_kraken:
    print("\n   ⚠️  NO KRAKEN PACKAGE FOUND!")
    print("   Your bot needs 'kraken-api' package.")
    print("   Install with: pip install kraken-api")

# 5. CHECK YOUR BOT'S MODULES
print("\n[4] Checking Your Bot Modules:")
bot_modules = ['utils_eth', 'config_eth', 'state_loaders_eth']

for module in bot_modules:
    if can_import(module):
        print(f"   ✓ {module}: FOUND")
    else:
        print(f"   ✗ {module}: MISSING (check file exists)")

# 6. QUICK PIP CHECK (optional)
print("\n[5] Checking Installed Packages (via pip list):")
try:
    # Run pip list and filter for kraken
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'list'],
        capture_output=True,
        text=True,
        timeout=5
    )
    
    if 'kraken' in result.stdout.lower():
        print("   ✓ Kraken package found in pip list")
        # Find the exact line
        for line in result.stdout.split('\n'):
            if 'kraken' in line.lower():
                print(f"     {line.strip()}")
    else:
        print("   ✗ No kraken package in pip list")
        
except Exception as e:
    print(f"   ⚠️  Could not check pip: {e}")

print("\n" + "=" * 50)
print("DIAGNOSTIC COMPLETE")
print("=" * 50)

# SUMMARY
print("\n[SUMMARY]")
if found_kraken:
    print("✅ Kraken API package is installed")
    print("✅ Try running your bot now")
else:
    print("❌ Kraken API package is NOT installed")
    print("💡 SOLUTION: Run: pip install kraken-api")
    print("💡 Or if using virtual env, activate it first")