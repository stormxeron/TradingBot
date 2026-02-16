"""
Shared State - Global state accessible to all upgrades
"""
from typing import Dict, Any

class SharedState:
    """Singleton for sharing state between upgrades"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SharedState, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        self.data = {
            'market_conditions': {},
            'upgrade_decisions': {},
            'performance_metrics': {},
            'emergency_flags': {},
            'last_update': 0
        }
    
    def update(self, key: str, value: Any):
        """Update shared state"""
        self.data[key] = value
        self.data['last_update'] = self._get_timestamp()
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get value from shared state"""
        return self.data.get(key, default)
    
    def get_all(self) -> Dict[str, Any]:
        """Get all shared state"""
        return self.data.copy()
    
    def clear(self):
        """Clear all shared state"""
        self.data.clear()
        self._initialize()
    
    def _get_timestamp(self) -> float:
        import time
        return time.time()

# Global instance
shared_state = SharedState()