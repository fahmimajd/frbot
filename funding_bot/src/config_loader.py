"""
Configuration loader for the funding bot.
Loads and validates configuration from YAML and environment variables.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv


class Config:
    """Centralized configuration manager for the funding bot."""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration from YAML file and environment variables.

        Args:
            config_path: Path to the YAML configuration file.
                        Defaults to config/config.yaml in the project root.
        """
        # Load environment variables from .env file (project root)
        env_path = Path(__file__).parent.parent / ".env"
        load_dotenv(env_path)

        # Load YAML configuration
        if config_path is None:
            config_path = Path(__file__).parent / "config.yaml"

        with open(config_path, "r") as f:
            self._config = yaml.safe_load(f)

        # Substitute environment variables in config
        self._config = self._substitute_env_vars(self._config)

    def _substitute_env_vars(self, obj: Any) -> Any:
        """
        Recursively substitute ${VAR_NAME} patterns with environment variables.

        Args:
            obj: The object to process (dict, list, or string).

        Returns:
            The processed object with environment variables substituted.
        """
        if isinstance(obj, dict):
            return {k: self._substitute_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._substitute_env_vars(item) for item in obj]
        elif isinstance(obj, str) and obj.startswith("${") and obj.endswith("}"):
            env_var = obj[2:-1]
            return os.environ.get(env_var, obj)
        else:
            return obj

    def get(self, *keys: str, default: Any = None) -> Any:
        """
        Get a configuration value by nested keys.

        Args:
            *keys: Variable number of keys to navigate the config hierarchy.
            default: Default value if the key path doesn't exist.

        Returns:
            The configuration value or default.

        Example:
            config.get('strategy', 'funding_threshold_pct')
        """
        value = self._config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    @property
    def strategy(self) -> Dict[str, Any]:
        """Get strategy configuration section."""
        return self._config.get("strategy", {})

    @property
    def risk(self) -> Dict[str, Any]:
        """Get risk management configuration section."""
        return self._config.get("risk", {})

    @property
    def filters(self) -> Dict[str, Any]:
        """Get filter configuration section."""
        return self._config.get("filters", {})

    @property
    def exchange(self) -> Dict[str, Any]:
        """Get exchange configuration section."""
        return self._config.get("exchange", {})

    @property
    def notifications(self) -> Dict[str, Any]:
        """Get notifications configuration section."""
        return self._config.get("notifications", {})

    @property
    def database(self) -> Dict[str, Any]:
        """Get database configuration section."""
        return self._config.get("database", {})

    @property
    def logging(self) -> Dict[str, Any]:
        """Get logging configuration section."""
        return self._config.get("logging", {})

    def validate(self) -> bool:
        """
        Validate that all required configuration values are present.

        Returns:
            True if validation passes.

        Raises:
            ValueError: If required configuration is missing.
        """
        required_checks = [
            (["strategy", "funding_threshold_pct"], "Funding threshold"),
            (["strategy", "entry_window_start_min"], "Entry window start"),
            (["strategy", "entry_window_end_min"], "Entry window end"),
            (["risk", "max_leverage"], "Max leverage"),
            (["risk", "take_profit_pct"], "Take profit percentage"),
            (["risk", "stop_loss_pct"], "Stop loss percentage"),
            (["exchange", "api_url"], "Exchange API URL"),
            (["exchange", "ws_url"], "Exchange WebSocket URL"),
        ]

        for keys, description in required_checks:
            if self.get(*keys) is None:
                raise ValueError(f"{description} is required in configuration")

        # Validate logical constraints
        if self.get("strategy", "entry_window_start_min") <= self.get(
            "strategy", "entry_window_end_min"
        ):
            raise ValueError(
                "Entry window start must be greater than entry window end"
            )

        if self.get("risk", "max_leverage") > 20:
            raise ValueError("Maximum leverage cannot exceed 20x")

        return True

    def __repr__(self) -> str:
        """Return string representation of configuration."""
        return f"Config({self._config})"


# Global configuration instance (lazy loaded)
_config: Optional[Config] = None


def get_config(config_path: Optional[str] = None) -> Config:
    """
    Get or create the global configuration instance.

    Args:
        config_path: Optional path to configuration file.

    Returns:
        The global Config instance.
    """
    global _config
    if _config is None:
        _config = Config(config_path)
        _config.validate()
    return _config
