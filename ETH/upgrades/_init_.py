"""
Upgrades Package - Modular upgrades for DynamicGrid bot
"""
from upgrades.upgrade_manager import UpgradeManager
from upgrades.virtual_leverage import VirtualLeverage
from upgrades.capital_efficiency import CapitalEfficiency
from upgrades.momentum_analyzer import MomentumAnalyzer
from upgrades.safety_monitor import SafetyMonitor
from upgrades.data_pipeline import DataPipeline

__version__ = "1.0.0"
__all__ = [
    'UpgradeManager',
    'VirtualLeverage', 
    'CapitalEfficiency',
    'MomentumAnalyzer',
    'SafetyMonitor',
    'DataPipeline'
]