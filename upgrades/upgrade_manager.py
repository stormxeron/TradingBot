"""
Upgrade Manager - Orchestrates all bot upgrades
"""
import logging
import time
from typing import Dict, Any, List
from decimal import Decimal

class UpgradeManager:
    """Manages all upgrade modules and coordinates their execution"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.upgrades = {}
        self.data_pipeline = None
        self.safety_monitor = None
        
        # State
        self.is_initialized = False
        self.last_cycle_time = 0
        self.cycle_count = 0
        self._last_momentum_reset_time = 0  # ADD THIS LINE
        
        logging.info("🔄 Upgrade Manager initialized")
    
    def initialize(self, 
                   volume_precision: Decimal,
                   price_precision: Decimal,
                   base_asset: str,
                   quote_asset: str,
                   trading_pair: str,
                   user_client=None,
                   market_client=None,
                   utils_module=None) -> bool:  # <-- ADD utils_module parameter
        
        """
        Initialize all upgrades after main() sets up core variables
        """
        try:
            # Store clients for data pipeline
            self.user_client = user_client
            self.market_client = market_client

            self.base_asset = base_asset
            self.quote_asset = quote_asset
            
            # Initialize data pipeline first
            from upgrades.data_pipeline import DataPipeline
            self.data_pipeline = DataPipeline(
                base_asset=base_asset,
                quote_asset=quote_asset,
                trading_pair=trading_pair
            )

            # 🚨 CRITICAL: Pass utils module to data pipeline
            if utils_module:
                self.data_pipeline._utils_module = utils_module
                logging.debug(f"✅ Utils module set for DataPipeline")
            else:
                logging.warning("⚠️ No utils module provided to DataPipeline")

            
            # Initialize safety monitor
            from upgrades.safety_monitor import SafetyMonitor
            self.safety_monitor = SafetyMonitor(config=self.config)
            
            # Initialize upgrades based on config
            if self.config.get('ENABLE_VIRTUAL_LEVERAGE', False):
                from upgrades.virtual_leverage import VirtualLeverage
                self.upgrades['virtual_leverage'] = VirtualLeverage(
                    config=self.config,
                    volume_precision=volume_precision,
                    price_precision=price_precision,
                    base_asset=base_asset,
                    quote_asset=quote_asset
                )
                logging.info("✅ Virtual Leverage upgrade loaded")
            
            if self.config.get('ENABLE_CAPITAL_EFFICIENCY', False):
                from upgrades.capital_efficiency import CapitalEfficiency
                self.upgrades['capital_efficiency'] = CapitalEfficiency(
                    config=self.config,
                    volume_precision=volume_precision,
                    price_precision=price_precision
                )
                logging.info("✅ Capital Efficiency upgrade loaded")
            
            if self.config.get('ENABLE_MOMENTUM_ANALYZER', False):
                from upgrades.momentum_analyzer import MomentumAnalyzer
                self.upgrades['momentum_analyzer'] = MomentumAnalyzer(
                    config=self.config,
                    price_precision=price_precision
                )
                logging.info("✅ Momentum Analyzer upgrade loaded")
            
            self.is_initialized = True
            #logging.critical("🚀 UPGRADE SYSTEM FULLY INITIALIZED")
            return True
            
        except Exception as e:
            logging.error(f"🔥 Failed to initialize upgrades: {e}")
            return False
        
    def _calculate_idle_percent_once(self, base_balance: Decimal, quote_balance: Decimal, 
                                   managed_orders: Dict, current_price: Decimal) -> Decimal:
        """
        Calculate idle percentage once to avoid duplication
        Returns: idle percentage as Decimal (0.10 = 10%)
        """
        try:
            if not managed_orders:
                return Decimal('1.0')  # 100% idle if no orders
            
            # Calculate total portfolio value
            base_value = base_balance * current_price
            portfolio_value = base_value + quote_balance
            
            if portfolio_value == Decimal('0'):
                return Decimal('1.0')  # 100% idle if no portfolio
            
            # Calculate locked value from orders
            locked_value = self._calculate_locked_value(managed_orders)
            
            # Calculate utilization
            utilization = locked_value / portfolio_value
            idle_percent = Decimal('1') - utilization
            
            # Ensure within bounds
            idle_percent = max(Decimal('0'), min(idle_percent, Decimal('1')))
            
            return idle_percent
            
        except Exception as e:
            logging.error(f"Idle calculation error: {e}")
            return Decimal('1.0')  # Safe default
    
    def _calculate_locked_value(self, managed_orders: Dict) -> Decimal:
        """
        Calculate locked value from orders
        """
        locked = Decimal('0')
        for order in managed_orders.values():
            try:
                # Use order price for valuation (not current price)
                if order['side'] == 'buy':
                    locked += order['volume'] * order['price']
                else:  # sell
                    locked += order['volume'] * order['price']  # Using order price
            except (KeyError, TypeError) as e:
                logging.warning(f"Order data error in locked calc: {e}")
                continue
        
        return locked
    
    def is_active(self) -> bool:
        """Check if upgrade system is active and operational"""
        return self.is_initialized and bool(self.upgrades)  # Has upgrades loaded
    
    def get_minimum_viable_gap(self, current_gap: Decimal) -> Decimal:
        """
        Get minimum viable gap based on upgrade recommendations
        Higher leverage/volatility might require larger gaps
        """
        if not self.is_active():
            return current_gap
        
        # Base on current gap
        min_gap = current_gap
        
        # Check each upgrade for gap requirements
        for upgrade_name, upgrade in self.upgrades.items():
            if hasattr(upgrade, 'get_minimum_gap'):
                try:
                    upgrade_min = upgrade.get_minimum_gap(current_gap)
                    min_gap = max(min_gap, upgrade_min)  # Use the larger gap
                except Exception as e:
                    logging.debug(f"Upgrade {upgrade_name} gap check failed: {e}")
        
        return min_gap
    
    def get_recommended_leverage(self) -> Decimal:
        """Get current recommended leverage from upgrades"""
        if not self.is_active():
            return Decimal('1.0')
        
        # This would track the current leverage recommendation
        # You might want to store this from process_cycle results
        if hasattr(self, '_last_leverage'):
            return self._last_leverage
        return Decimal('1.0')

    def process_cycle(self, 
                    trade_client,
                    user_client, 
                    market_client,
                    current_price: Decimal,
                    current_atr_gap_factor: Decimal,
                    managed_orders: Dict,
                    base_balance: Decimal,
                    quote_balance: Decimal,
                    market_data: List = None) -> Dict[str, Any]:
        """
        Process one monitor cycle through all upgrades
        Returns: Dict of upgrade decisions
        """
        if not self.is_initialized:
            return {}
        
        self.cycle_count += 1
        
        try:
            
            # 1. Collect fresh data
            fresh_data = self.data_pipeline.collect(
                trade_client=trade_client,
                user_client=user_client,
                market_client=market_client,
                current_price=current_price,
                current_atr_gap_factor=current_atr_gap_factor,
                managed_orders=managed_orders,
                base_balance=base_balance,
                quote_balance=quote_balance,
            )
            
            # STORE for buffer calculation
            self._last_fresh_data = fresh_data

            # Add ATR data to fresh_data
            fresh_data['current_atr_gap_factor'] = current_atr_gap_factor
            
            # 🚨 CRITICAL: Calculate idle capital ONCE and share with all upgrades
            idle_percent = self._calculate_idle_percent_once(
                base_balance, quote_balance, managed_orders, current_price
            )
            fresh_data['idle_percent'] = idle_percent
            
            # 🔥 NEW: Calculate dynamic safety buffer and pass to upgrades
            safety_buffer = self._calculate_dynamic_safety_buffer(current_atr_gap_factor)
            fresh_data['safety_buffer'] = float(safety_buffer)
            fresh_data['dynamic_buffer_percent'] = float(safety_buffer * Decimal('100'))
            
            portfolio_value = base_balance * current_price + quote_balance
            locked_value = self._calculate_locked_value(managed_orders)
            idle_value = portfolio_value - locked_value
            idle_percent = idle_value / portfolio_value if portfolio_value > 0 else Decimal('0')
            utilization = (Decimal('1') - idle_percent) * 100

            # Calculate locked amounts from managed orders
            locked_base = Decimal('0')
            locked_quote = Decimal('0')

            for order in managed_orders.values():
                if order['side'] == 'sell':
                    locked_base += order['volume']
                elif order['side'] == 'buy':
                    locked_quote += order['volume'] * order['price']

            idle_base = base_balance - locked_base
            idle_quote = quote_balance - locked_quote

            # Make it one line:
            #logging.info(f"💰 ${portfolio_value:.2f} ({utilization:.1f}% util, {idle_percent*100:.1f}% idle) | 🟢{self.base_asset}:{idle_base:.4f} 🔵{self.quote_asset}:{idle_quote:.1f}")

            # Log once at the beginning
            #logging.critical(f"💰 PORTFOLIO SUMMARY:")
            #logging.critical(f"   Portfolio: ${(base_balance * current_price + quote_balance):.2f}")
            #logging.critical(f"   Locked: ${self._calculate_locked_value(managed_orders):.2f}")
            #logging.critical(f"   Utilization: {(Decimal('1') - idle_percent)*100:.1f}%")
            #logging.critical(f"   Idle: {idle_percent*100:.1f}%")
            #logging.critical(f"   Dynamic Safety Buffer: {safety_buffer*100:.1f}%")
            
            # 2. Run safety checks first
            if self.safety_monitor:
                safety_status = self.safety_monitor.check(fresh_data)
                if not safety_status.get('trading_allowed', True):
                    logging.critical("🚨 SAFETY MONITOR: Trading halted!")
                    return {'emergency_shutdown': True}
            
            # 3. Run all upgrades in sequence
            decisions = {}
            upgrade_data = {}  # NEW: Collect all upgrade data

            for upgrade_name, upgrade in self.upgrades.items():
                try:
                    upgrade_decisions = upgrade.analyze(fresh_data)
                    decisions[upgrade_name] = upgrade_decisions
                    
                    # Collect data from each upgrade instead of letting them log
                    if upgrade_name == 'momentum_analyzer':
                        upgrade_data['momentum'] = upgrade_decisions.get('momentum', Decimal('0'))
                        upgrade_data['trend'] = upgrade_decisions.get('trend', 'neutral')
                        upgrade_data['confidence'] = upgrade_decisions.get('confidence', Decimal('0.6'))
                    elif upgrade_name == 'virtual_leverage':
                        # Get BOTH values
                        upgrade_data['original_leverage'] = upgrade_decisions.get('unsafe_leverage', Decimal('1.0'))  # 1.26x
                        upgrade_data['recommended_leverage'] = upgrade_decisions.get('safe_leverage', Decimal('1.0'))  # 1.10x
                        # Get safety_buffer from details if available
                        details = upgrade_decisions.get('details', {})
                        upgrade_data['safety_buffer'] = Decimal(str(details.get('safety_buffer', 0.03)))
                        
                except Exception as e:
                    logging.error(f"🔥 {upgrade_name} analysis failed: {e}")
                    decisions[upgrade_name] = {'error': str(e), 'action_required': False}

            # 4. Resolve conflicts between upgrades
            final_decisions = self._resolve_conflicts(decisions, idle_percent, fresh_data)
            
            conflict_resolved_leverage = final_decisions.get('recommended_leverage', Decimal('1.0'))

            # 5. Add ALL collected data to final_decisions
            final_decisions.update({
                'momentum': upgrade_data.get('momentum', Decimal('0')),
                'trend': upgrade_data.get('trend', 'neutral'),
                'confidence': upgrade_data.get('confidence', Decimal('0.6')),
                'idle_percent': idle_percent,
                'safety_buffer': Decimal(str(fresh_data.get('safety_buffer', 0.03))),
                # CORRECT NAMES:
                'original_leverage': upgrade_data.get('original_leverage', Decimal('1.0')),  # unsafe_leverage: 1.38x
                'net_leverage': upgrade_data.get('recommended_leverage', Decimal('1.0')),     # safe_leverage: 1.34x
                # Use the conflict-resolved leverage (after all caps)
                'final_leverage': conflict_resolved_leverage,  # final: 1.30x
            })

            # Also keep the conflict-resolved leverage if you need it
            final_decisions['conflict_resolved_leverage'] = conflict_resolved_leverage

            # Also add the idle/portfolio values you're already calculating
            final_decisions.update({
                'portfolio_value': portfolio_value,
                'utilization': utilization,
                'idle_base': idle_base,
                'idle_quote': idle_quote,
            })

            # Store for future reference
            self._last_leverage = final_decisions.get('final_leverage', Decimal('1.0'))

            # 6. Add metadata
            final_decisions['cycle_count'] = self.cycle_count
            final_decisions['upgrades_active'] = list(self.upgrades.keys())

            if 'capital_efficiency' in decisions:
                ce_decision = decisions['capital_efficiency']
                
            return final_decisions
            
        except Exception as e:
            logging.error(f"🔥 Upgrade cycle failed: {e}")
            return {'error': str(e), 'emergency_fallback': True}
        
    def _calculate_dynamic_safety_buffer(self, current_volatility: Decimal = None) -> Decimal:
        """
        Calculate dynamic safety buffer based on market conditions.
        Lower volatility = smaller buffer (safer to use more capital).
        """
        # Default buffer if no volatility data
        if current_volatility is None:
            return Decimal('0.03')  # Default 3%
        
        # Dynamic buffer scaling
        if current_volatility > Decimal('0.015'):  # High vol > 1.5%
            return Decimal('0.05')  # 5% buffer during high volatility
        elif current_volatility < Decimal('0.005'):  # Very low vol < 0.5%
            return Decimal('0.02')  # 2% buffer during calm markets
        else:
            return Decimal('0.03')  # 3% buffer for normal conditions
    
    def _resolve_conflicts(self, decisions: Dict[str, Dict], idle_percent: Decimal, fresh_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Resolve conflicts between upgrade decisions with PROPER PRIORITY LOGIC
        """

        # Add this at the top of the method:
        leverage_multiplier = Decimal(str(self.config.get('VIRTUAL_LEVERAGE_MULTIPLIER', 2.0)))
        
        # Initialize requested_leverage here, before any conditionals
        requested_leverage = Decimal('1.0')

        resolved = {
            'recommended_leverage': Decimal('1.0'),
            'emergency_shutdown': False,
            'force_grid_reset': False,
            'reset_reason': '',
            'actions': [],
            'idle_capital_percent': float(idle_percent),
            'target_utilization': Decimal('0.95')
        }
        
        # Round idle_percent
        idle_percent = idle_percent.quantize(Decimal('0.001'))
        
        # Check each upgrade
        virtual_leverage = decisions.get('virtual_leverage', {})
        capital_efficiency = decisions.get('capital_efficiency', {})
        momentum_analyzer = decisions.get('momentum_analyzer', {})
        
        # 🚨 PRIORITY 1: Emergency shutdown
        for upgrade_name, decision in decisions.items():
            if decision.get('emergency_shutdown', False):
                logging.critical(f"🛑 {upgrade_name.upper()}: EMERGENCY SHUTDOWN")
                resolved['emergency_shutdown'] = True
                return resolved
        
        # 🚨 CRITICAL: Calculate MAXIMUM safe leverage with DYNAMIC buffer
        # Get current volatility from fresh_data if available
        current_volatility = None
        if hasattr(self, '_last_fresh_data'):
            current_volatility = self._last_fresh_data.get('current_atr_gap_factor')
        
        # Calculate DYNAMIC safety buffer
        safety_buffer = self._calculate_dynamic_safety_buffer(current_volatility)
        max_safe_leverage = Decimal('1.0')
        
        if idle_percent > safety_buffer:
            # Safe idle for leverage = idle_percent - DYNAMIC buffer
            safe_idle_for_leverage = idle_percent - safety_buffer
            usable_idle = idle_percent - safety_buffer  # Same calculation, different name
            
            if safe_idle_for_leverage > Decimal('0'):
                # 🔥 FIXED: Use CONFIG multiplier, not hardcoded 2.5
                leverage_from_idle = Decimal('1.0') + (safe_idle_for_leverage * leverage_multiplier)
                max_safe_leverage = min(leverage_from_idle, Decimal(str(self.config.get('MAX_VIRTUAL_LEVERAGE', '3.0'))))
                max_safe_leverage = max_safe_leverage.quantize(Decimal('0.01'))
            
            # Log the calculation
            usable = idle_percent - safety_buffer
            calculated = Decimal('1.0') + (usable * leverage_multiplier)
            #logging.critical(f"   Formula: 1 + ({usable*100:.1f}% × {leverage_multiplier}) = {calculated:.2f}x")
            
        else:
            # If idle_percent <= safety_buffer, set usable_idle to 0
            usable_idle = Decimal('0')
            max_safe_leverage = Decimal('1.0')
            
        # Log outside (always show)
        logging.info(f"💰 Safety: {idle_percent*100:.1f}% idle - {safety_buffer*100:.1f}% buffer = {usable_idle*100:.1f}% usable → max {max_safe_leverage:.2f}x")
        
        # 🚨 PRIORITY 2: Capital efficiency
        if capital_efficiency.get('action_required', False):
            action = capital_efficiency.get('action', '')
            
            logging.critical(f"💰 CAPITAL EFFICIENCY: {action}")
            
            # If idle capital is high, FORCE ACTION
            if idle_percent > Decimal('0.10'):  # More than 10% idle
                resolved['force_grid_reset'] = True
                resolved['reset_reason'] = 'high_idle_capital'
                resolved['recommended_leverage'] = max_safe_leverage
                
                logging.critical(f"   Forcing reset with {max_safe_leverage:.2f}x leverage")
            
            resolved['actions'].append({
                'type': 'capital_efficiency',
                'action': action,
                'idle_percent': float(idle_percent),
                'urgency': capital_efficiency.get('urgency', 'medium')
            })
            
        # 🚨 PRIORITY 2.5: Momentum Analyzer (CONSERVATIVE)
        
        elif momentum_analyzer.get('action_required', False):
            
            action = momentum_analyzer.get('action', 'Unknown momentum action')
            trend = momentum_analyzer.get('trend', 'neutral')
            momentum_value = Decimal(str(momentum_analyzer.get('momentum', 0.0)))
            confidence = Decimal(str(momentum_analyzer.get('confidence', 0.0)))

            logging.info(f"🔧 MOMENTUM CHECK: action_required={momentum_analyzer.get('action_required', False)}")
            logging.info(f"🔧 MOMENTUM CHECK: momentum={momentum_analyzer.get('momentum', 0)}")
            logging.info(f"🔧 MOMENTUM CHECK: confidence={momentum_analyzer.get('confidence', 0)}")
            logging.info(f"🔧 MOMENTUM CHECK: trend={momentum_analyzer.get('trend', 'neutral')}")

            # Also check other upgrades:
            logging.info(f"🔧 CAPITAL EFFICIENCY: action_required={capital_efficiency.get('action_required', False)}")
            logging.info(f"🔧 VIRTUAL LEVERAGE: action_required={virtual_leverage.get('action_required', False)}")
            
            logging.critical(f"📈 MOMENTUM ANALYZER: {action}")
            logging.critical(f"   Trend: {trend}, Momentum: {momentum_value*100:.2f}%, Confidence: {confidence*100:.0f}%")
            
            # Initialize bias_action with default
            bias_action = 'NO BIAS'
            
            # ============================================
            # 🔥 CONSERVATIVE ACTION LOGIC - Only reset on STRONG signals
            # ============================================
            
            # CRITICAL: Only act on STRONG signals with HIGH confidence
            STRONG_MOMENTUM_THRESHOLD = Decimal(str(self.config.get('STRONG_MOMENTUM_THRESHOLD', '0.005')))
            HIGH_CONFIDENCE_THRESHOLD = Decimal(str(self.config.get('MOMENTUM_CONFIDENCE_THRESHOLD', '0.7')))
            
            # Check if signal is strong enough to warrant action
            is_strong_signal = (abs(momentum_value) >= STRONG_MOMENTUM_THRESHOLD and 
                            confidence >= HIGH_CONFIDENCE_THRESHOLD)
            
            # Check time since last reset (prevent constant resetting)
            current_time = time.time()
            time_since_last_reset = current_time - self._last_momentum_reset_time if hasattr(self, '_last_momentum_reset_time') else float('inf')
            MIN_RESET_COOLDOWN = self.config.get('MOMENTUM_RESET_COOLDOWN_MINUTES', 60) * 60  # Convert to seconds
            
            if is_strong_signal and time_since_last_reset >= MIN_RESET_COOLDOWN:
                # 1. Force grid reset (but only for strong signals)
                resolved['force_grid_reset'] = True
                resolved['reset_reason'] = f'strong_momentum_{trend}'
                
                # 2. Set grid bias direction (with safety check)
                if trend == 'bullish':
                    resolved['grid_bias'] = 'sell'  # Bullish → bias toward sells
                    bias_action = 'SELL bias'
                elif trend == 'bearish':
                    resolved['grid_bias'] = 'buy'   # Bearish → bias toward buys
                    bias_action = 'BUY bias'
                else:
                    resolved['grid_bias'] = 'neutral'
                    bias_action = 'NEUTRAL (no bias)'
                
                # Track last reset time
                self._last_momentum_reset_time = current_time
                
                logging.critical(f"   🎯 STRONG MOMENTUM: Forcing grid reset with {bias_action}")
                logging.critical(f"   📊 Momentum strength: {abs(momentum_value)*100:.3f}% (STRONG)")
                
            else:
                # Weak signal - just log it but don't reset
                if not is_strong_signal:
                    logging.info(f"   📉 Weak momentum signal ({abs(momentum_value)*100:.3f}%, {confidence*100:.0f}% confidence) - NO ACTION")
                else:
                    logging.info(f"   ⏰ Too soon since last momentum reset ({time_since_last_reset/60:.0f}min ago) - NO ACTION")
                
                resolved['force_grid_reset'] = False
                resolved['grid_bias'] = 'neutral'
                bias_action = 'NO ACTION'
            
            # Store momentum data
            resolved['momentum_action'] = action
            resolved['momentum_trend'] = trend
            resolved['momentum_strength'] = float(momentum_value)
            resolved['momentum_confidence'] = float(confidence)
            
            # Add to actions list
            resolved['actions'].append({
                'type': 'momentum',
                'action': action,
                'trend': trend,
                'momentum': float(momentum_value),
                'confidence': float(confidence),
                'grid_bias': resolved['grid_bias'],
                'action_taken': resolved['force_grid_reset'],
                'urgency': 'high' if resolved['force_grid_reset'] else 'low'
            })
        
        # 🚨 PRIORITY 3: Leverage recommendations
        elif virtual_leverage.get('action_required', False):
            requested_leverage = Decimal(str(virtual_leverage.get('recommended_leverage', 1.0)))
            requested_leverage = requested_leverage.quantize(Decimal('0.01'))
            
            # CRITICAL: Cap leverage at safe maximum
            safe_leverage = min(requested_leverage, max_safe_leverage)
            resolved['recommended_leverage'] = safe_leverage
            
            if requested_leverage > max_safe_leverage:
                logging.warning(f"⚠️ CAPITAL CONSTRAINED: Leverage reduced from {requested_leverage:.2f}x to {safe_leverage:.2f}x ({idle_percent*100:.1f}% idle)")
            else:
                logging.critical(f"💸 VIRTUAL LEVERAGE: {safe_leverage:.2f}x")
            
            resolved['actions'].append({
                'type': 'leverage',
                'leverage': float(safe_leverage),
                'reason': virtual_leverage.get('action', ''),
                'capped': requested_leverage > max_safe_leverage
            })
        
        # 🚨 PRIORITY 4: If no upgrade action but idle is high, still apply leverage
        elif idle_percent > Decimal('0.05'):  # 5% idle threshold
            if max_safe_leverage > Decimal('1.0'):
                resolved['recommended_leverage'] = max_safe_leverage
                logging.critical(f"💰 AUTO-LEVERAGE: {max_safe_leverage:.2f}x (using {idle_percent*100:.1f}% idle)")
                
                resolved['actions'].append({
                    'type': 'auto_leverage',
                    'leverage': float(max_safe_leverage),
                    'reason': f'Auto-applied based on {idle_percent*100:.1f}% idle capital'
                })
        
        # Log final decision summary
        #logging.critical(f"📊 FINAL DECISION: Leverage {resolved['recommended_leverage']:.2f}x applied")

        # FIXED: Only log debug info if requested_leverage was actually set
        #if virtual_leverage.get('action_required', False):
        #    logging.critical(f"🔍 LEVERAGE DISCREPANCY DEBUG:")
        #    logging.critical(f"   max_safe_leverage: {max_safe_leverage:.4f}x")
        #    logging.critical(f"   idle_percent: {idle_percent:.4f} = {idle_percent*100:.2f}%")
        #    logging.critical(f"   safety_buffer used: {safety_buffer:.4f} = {safety_buffer*100:.2f}%")
        #    logging.critical(f"   usable_idle: {idle_percent - safety_buffer:.4f} = {(idle_percent - safety_buffer)*100:.2f}%")
        #    logging.critical(f"   Calculation: 1 + ({(idle_percent - safety_buffer):.4f} × {leverage_multiplier}) = {Decimal('1') + ((idle_percent - safety_buffer) * leverage_multiplier):.4f}x")

        return resolved
    
    def apply_decisions(self, 
                       decisions: Dict[str, Any],
                       original_values: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply upgrade decisions to original values
        Returns: Modified values
        """
        modified = original_values.copy()
        
        # Apply virtual leverage to volumes
        if 'recommended_leverage' in decisions:
            leverage = decisions['recommended_leverage']
            if 'base_balance' in modified and 'quote_balance' in modified:
                modified['base_balance'] = modified['base_balance'] * leverage
                modified['quote_balance'] = modified['quote_balance'] * leverage
                logging.info(f"💰 Applied virtual leverage: {leverage}x")
        
        # Add other decision applications here
        
        return modified
    
    def shutdown(self):
        """Clean shutdown of all upgrades"""
        logging.info("🔄 Shutting down upgrades...")
        for upgrade_name, upgrade in self.upgrades.items():
            try:
                if hasattr(upgrade, 'shutdown'):
                    upgrade.shutdown()
            except Exception as e:
                logging.error(f"Error shutting down {upgrade_name}: {e}")
        
        self.is_initialized = False
        logging.info("✅ Upgrades shut down")