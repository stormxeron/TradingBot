import re

def check_variable_usage(var1, var2):
    print(f"\n🔍 Checking: {var1} vs {var2}")
    
    # Check DynamicGrid_engine.py (main file)
    with open('DynamicGrid_engine.py', 'r') as f:
        content = f.read()
        
        var1_count = len(re.findall(rf'\b{var1}\b', content))
        var2_count = len(re.findall(rf'\b{var2}\b', content))
        
        print(f"  In DynamicGrid_engine.py:")
        print(f"    {var1}: {var1_count} occurrences")
        print(f"    {var2}: {var2_count} occurrences")
        
        # Show context
        for match in re.finditer(rf'.{{0,50}}{var1}.{{0,50}}', content):
            print(f"      ...{match.group()}...")
    
    # Check utils.py
    with open('utils.py', 'r') as f:
        content = f.read()
        var1_count = len(re.findall(rf'\b{var1}\b', content))
        var2_count = len(re.findall(rf'\b{var2}\b', content))
        
        if var1_count > 0 or var2_count > 0:
            print(f"  In utils.py:")
            print(f"    {var1}: {var1_count} occurrences")
            print(f"    {var2}: {var2_count} occurrences")

# Check the confusing pairs
check_variable_usage('ATR_MULTIPLIER', 'MAX_GAP_MULTIPLIER')
check_variable_usage('BUDGET_UTILIZATION_CAP', 'MIN_CAPITAL_UTILIZATION')