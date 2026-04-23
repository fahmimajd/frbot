"""
Multi-pair funding rate scanner.
Scans all Binance USDT-margined perpetual pairs and ranks them by signal strength.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.config_loader import Config
from src.exchange.binance_rest import BinanceRESTClient

logger = logging.getLogger(__name__)


@dataclass
class FundingSignal:
    """Represents a funding rate signal for a symbol."""

    symbol: str
    funding_rate: float
    mark_price: float
    index_price: float
    basis_pct: float
    next_funding_time: int
    volume_24h: float
    score: float
    timestamp: datetime


class FundingScanner:
    """
    Scans and ranks trading pairs by funding rate opportunity.

    Scans all USDT-margined perpetual contracts on Binance Futures
    and ranks them based on funding rate magnitude and other criteria.
    """

    # Minimum 24h volume threshold (in USDT)
    MIN_VOLUME_24H = 50_000_000  # $50M

    def __init__(self, config: Config, rest_client: BinanceRESTClient):
        """
        Initialize the funding scanner.

        Args:
            config: Configuration instance.
            rest_client: Binance REST API client.
        """
        self.config = config
        self.rest_client = rest_client
        self.min_volume = config.get(
            "strategy", "min_volume_24h_usdt", default=self.MIN_VOLUME_24H
        )
        self.funding_threshold = config.get(
            "strategy", "funding_threshold_pct", default=0.03
        ) / 100.0  # Convert from percentage

    async def get_all_symbols(self) -> List[str]:
        """
        Get all USDT-margined perpetual symbols.

        Returns:
            List of symbol names.
        """
        # Get exchange info to retrieve all symbols
        endpoint = "/fapi/v1/exchangeInfo"
        exchange_info = await self.rest_client._request("GET", endpoint)

        symbols = []
        for symbol_info in exchange_info.get("symbols", []):
            if (
                symbol_info.get("quoteAsset") == "USDT"
                and symbol_info.get("contractType") == "PERPETUAL"
                and symbol_info.get("status") == "TRADING"
            ):
                symbols.append(symbol_info["symbol"])

        logger.info(f"Found {len(symbols)} active USDT perpetual symbols")
        return symbols

    async def scan_symbol(self, symbol: str) -> Optional[FundingSignal]:
        """
        Scan a single symbol for funding rate data.

        Args:
            symbol: Trading pair symbol.

        Returns:
            FundingSignal if data is available, None otherwise.
        """
        try:
            # Get funding rate and mark price
            premium_index = await self.rest_client.get_funding_rate(symbol)

            funding_rate = float(premium_index.get("lastFundingRate", 0))
            mark_price = float(premium_index.get("markPrice", 0))
            index_price = float(premium_index.get("indexPrice", 0))
            next_funding_time = int(premium_index.get("nextFundingTime", 0))

            # Calculate basis
            if index_price > 0:
                basis_pct = ((mark_price - index_price) / index_price) * 100
            else:
                basis_pct = 0.0

            # Get 24h volume from ticker
            ticker_endpoint = "/fapi/v1/ticker/24hr"
            ticker_data = await self.rest_client._request(
                "GET", ticker_endpoint, {"symbol": symbol}
            )
            volume_24h = float(ticker_data.get("quoteVolume", 0))

            # Filter by volume
            if volume_24h < self.min_volume:
                logger.debug(
                    f"Skipping {symbol}: 24h volume ${volume_24h:,.0f} below threshold"
                )
                return None

            # Calculate score (higher absolute funding rate = stronger signal)
            score = abs(funding_rate)

            return FundingSignal(
                symbol=symbol,
                funding_rate=funding_rate,
                mark_price=mark_price,
                index_price=index_price,
                basis_pct=basis_pct,
                next_funding_time=next_funding_time,
                volume_24h=volume_24h,
                score=score,
                timestamp=datetime.now(timezone.utc),
            )

        except Exception as e:
            logger.error(f"Error scanning {symbol}: {e}")
            return None

    async def scan_all(self) -> List[FundingSignal]:
        """
        Scan all symbols and return ranked signals.

        Returns:
            List of FundingSignal objects, ranked by score (descending).
        """
        logger.info("Starting full market scan...")

        symbols = await self.get_all_symbols()
        signals = []

        # Scan symbols with concurrency limit
        semaphore = __import__("asyncio").Semaphore(10)

        async def scan_with_semaphore(sym: str) -> Optional[FundingSignal]:
            async with semaphore:
                return await self.scan_symbol(sym)

        tasks = [scan_with_semaphore(symbol) for symbol in symbols]
        results = await __import__("asyncio").gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, FundingSignal):
                signals.append(result)
            elif isinstance(result, Exception):
                logger.error(f"Scan task error: {result}")

        # Sort by score (highest absolute funding rate first)
        signals.sort(key=lambda x: x.score, reverse=True)

        logger.info(
            f"Scan complete: found {len(signals)} signals above volume threshold"
        )
        return signals

    async def get_top_signals(self, top_n: int = 10) -> List[FundingSignal]:
        """
        Get top N signals by score.

        Args:
            top_n: Number of top signals to return.

        Returns:
            List of top FundingSignal objects.
        """
        all_signals = await self.scan_all()
        top_signals = all_signals[:top_n]

        for signal in top_signals:
            logger.info(
                f"Top signal: {signal.symbol} | "
                f"Funding: {signal.funding_rate * 100:.4f}% | "
                f"Score: {signal.score:.6f}"
            )

        return top_signals

    def filter_by_threshold(
        self, signals: List[FundingSignal]
    ) -> List[FundingSignal]:
        """
        Filter signals by funding rate threshold.

        Args:
            signals: List of signals to filter.

        Returns:
            Filtered list of signals meeting the threshold.
        """
        filtered = [
            s
            for s in signals
            if abs(s.funding_rate) >= self.funding_threshold
        ]
        logger.info(
            f"Filtered {len(filtered)} signals above "
            f"{self.funding_threshold * 100:.2f}% threshold"
        )
        return filtered

    def get_next_funding_time(self) -> datetime:
        """
        Get the next funding settlement time.

        Funding occurs at 00:00, 08:00, and 16:00 UTC.

        Returns:
            Datetime of next funding settlement.
        """
        now = datetime.now(timezone.utc)
        hour = now.hour

        # Next funding times: 00:00, 08:00, 16:00 UTC
        funding_hours = [0, 8, 16]

        # Find next funding hour
        next_hour = None
        for h in funding_hours:
            if h > hour:
                next_hour = h
                break

        if next_hour is None:
            # Past 16:00, next is 00:00 tomorrow
            next_funding = now.replace(hour=0, minute=0, second=0, microsecond=0)
            next_funding = next_funding.replace(day=now.day + 1)
        else:
            next_funding = now.replace(
                hour=next_hour, minute=0, second=0, microsecond=0
            )

        return next_funding

    def get_minutes_to_funding(self) -> int:
        """
        Get minutes until next funding settlement.

        Returns:
            Minutes until next funding.
        """
        now = datetime.now(timezone.utc)
        next_funding = self.get_next_funding_time()
        delta = next_funding - now
        return int(delta.total_seconds() / 60)
