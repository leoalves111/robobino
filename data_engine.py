"""
Motor de dados read-only para BinomoAPI.
Conecta via WebSocket, agrega ticks em velas M5 e bloqueia operações de trading.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import pandas as pd
import requests
from BinomoAPI.api import BinomoAPI
from BinomoAPI.exceptions import BinomoAPIException, ConnectionError as BinomoConnectionError

from api_connection import check_connect, classify_connection_error
from candle_cache import MAX_CACHE_CANDLES, CandleCache

logger = logging.getLogger(__name__)

TRADING_METHODS = frozenset(
    {
        "place_call_option",
        "place_put_option",
        "Call",
        "Put",
    }
)

CRYPTO_IDX_RIC = "Z-CRY/IDX"
CRYPTO_IDX_ID = 347
CANDLE_API_URLS = (
    "https://api.binomo.com/candles/public/v1/candles",
    "https://api.binomo.com/candles/v1/candles",
)
LIVE_RATE_URLS = (
    "https://api.binomo.com/bo/v2/assets/{ric}/rate",
    "https://api.binomo.com/platform/public/v1/assets/{asset_id}/rate",
)
CACHE_FLUSH_INTERVAL_SEC = 60


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


class ReadOnlyBinomoAPI:
    """Wrapper que bloqueia qualquer método de execução de ordens."""

    def __init__(self, api: BinomoAPI) -> None:
        self._api = api

    def __getattr__(self, name: str) -> Any:
        if name in TRADING_METHODS:
            raise RuntimeError(
                f"BLOQUEADO: '{name}' é uma operação de trading. "
                "Este sistema opera em modo READ-ONLY."
            )
        return getattr(self._api, name)

    @property
    def raw(self) -> BinomoAPI:
        return self._api


class CandleAggregator:
    """Agrega cotações em tempo real em velas OHLC de intervalo fixo."""

    def __init__(self, interval_seconds: int = 300) -> None:
        self.interval_seconds = interval_seconds
        self._current: Optional[Candle] = None
        self._closed: list[Candle] = []
        self._on_close: Optional[Callable[[Candle], None]] = None

    def on_candle_close(self, callback: Callable[[Candle], None]) -> None:
        self._on_close = callback

    @property
    def closed_candles(self) -> list[Candle]:
        return list(self._closed)

    def seed(self, candles: list[Candle]) -> None:
        self._closed = candles[-500:]
        if self._closed:
            self._current = None

    def update(self, price: float, ts: Optional[datetime] = None) -> Optional[Candle]:
        ts = ts or datetime.now(timezone.utc)
        bucket_start = self._bucket_start(ts)

        if self._current is None:
            self._current = Candle(
                timestamp=bucket_start,
                open=price,
                high=price,
                low=price,
                close=price,
            )
            return None

        if bucket_start > self._current.timestamp:
            closed = self._current
            self._closed.append(closed)
            if len(self._closed) > 500:
                self._closed.pop(0)

            self._current = Candle(
                timestamp=bucket_start,
                open=price,
                high=price,
                low=price,
                close=price,
            )

            if self._on_close:
                self._on_close(closed)
            return closed

        self._current.high = max(self._current.high, price)
        self._current.low = min(self._current.low, price)
        self._current.close = price
        return None

    def to_dataframe(self) -> pd.DataFrame:
        rows = [c.to_dict() for c in self._closed]
        if self._current:
            rows.append(self._current.to_dict())
        if not rows:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows)
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    def _bucket_start(self, ts: datetime) -> datetime:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        epoch = int(ts.timestamp())
        aligned = epoch - (epoch % self.interval_seconds)
        return datetime.fromtimestamp(aligned, tz=timezone.utc)


@dataclass
class DataEngineState:
    connected: bool = False
    last_price: Optional[float] = None
    last_message_at: Optional[datetime] = None
    candles_count: int = 0
    balance: Optional[float] = None
    price_ticks: int = 0
    history_loaded: bool = False
    cache_status: str = "—"
    status_message: str = "Inicializando"
    reconnect_attempts: int = 0
    order_status: str = "NONE"


class DataEngine:
    """
    Gerencia conexão BinomoAPI, stream de preços e agregação M5.
    Reconecta automaticamente em caso de falha.
    """

    def __init__(
        self,
        auth_token: str,
        device_id: str,
        asset_name: str = "Crypto IDX",
        timeframe_seconds: int = 300,
        demo: bool = True,
        order_guard: Any | None = None,
    ) -> None:
        self.auth_token = auth_token
        self.device_id = device_id
        self.asset_name = asset_name
        self.timeframe_seconds = timeframe_seconds
        self.demo = demo
        self._order_guard = order_guard

        self.asset_ric = CRYPTO_IDX_RIC
        self.asset_id = CRYPTO_IDX_ID

        self.state = DataEngineState()
        self.aggregator = CandleAggregator(interval_seconds=timeframe_seconds)
        self._api: Optional[ReadOnlyBinomoAPI] = None
        self._running = False
        self._processed_msg_count = 0
        self._candle_queue: asyncio.Queue[Candle] = asyncio.Queue()
        self._last_closed_bucket: Optional[datetime] = None
        self._cache = CandleCache(
            max_candles=MAX_CACHE_CANDLES,
            parse_row=self._parse_candle_item,
            policy=CandleCache.policy_from_env(timeframe_seconds),
        )

        self.aggregator.on_candle_close(self._handle_closed_candle)

    def _handle_closed_candle(self, candle: Candle) -> None:
        self.state.candles_count = len(self.aggregator.closed_candles)
        self._cache.append(candle)
        try:
            self._candle_queue.put_nowait(candle)
        except asyncio.QueueFull:
            pass

    def _seed_history(self, candles: list[Candle], source: str) -> None:
        if not candles:
            return
        trimmed = candles[-MAX_CACHE_CANDLES:]
        self.aggregator.seed(trimmed)
        self.state.candles_count = len(trimmed)
        self.state.history_loaded = True
        self._cache.replace_all(trimmed)
        self._cache.flush()
        self.state.cache_status = f"{self._cache.last_status} ({len(trimmed)})"
        logger.info("Histórico carregado via %s: %d velas M5", source, len(trimmed))

    def is_connected(self) -> bool:
        return self.state.connected and check_connect(self._api)

    async def get_candles_safe(self):
        """Velas para análise — seguro se API cair (usa buffer local)."""
        if not self.is_connected():
            logger.warning("get_candles_safe: API desconectada — usando velas em memória")
        return self.get_dataframe()

    async def connect(self) -> None:
        if not self.auth_token or not self.device_id:
            raise BinomoAPIException(
                "Erro de autenticação — AUTH_TOKEN ou DEVICE_ID ausentes"
            )

        await self._cleanup()
        raw_api = BinomoAPI(
            auth_token=self.auth_token,
            device_id=self.device_id,
            demo=self.demo,
            enable_logging=False,
        )
        self._api = ReadOnlyBinomoAPI(raw_api)

        try:
            ric = self._api.get_asset_ric(self.asset_name)
            if ric:
                self.asset_ric = ric

            ok = await self._api.connect()
            if not ok or not check_connect(self._api):
                raise BinomoConnectionError(
                    "Falha na conexão — WebSocket não estabelecido"
                )

            await self._subscribe_asset_channel()
            await self._load_historical_candles()

            initial_rate = await asyncio.to_thread(self._fetch_live_rate_sync)
            if initial_rate:
                self._apply_price(initial_rate)
                logger.info("Preço inicial via REST: %.5f", initial_rate)

            balance = await self._fetch_balance()
            self.state.balance = balance

            self.state.connected = True
            self.state.status_message = "Monitorando Crypto IDX M5"
            self.state.reconnect_attempts = 0
            logger.info("Conectado ao ativo %s (%s)", self.asset_name, self.asset_ric)
        except Exception:
            self.state.connected = False
            await self._cleanup()
            raise

    async def _subscribe_asset_channel(self) -> None:
        if self._api is None or not check_connect(self._api):
            raise BinomoConnectionError(
                "Falha na conexão — impossível inscrever no canal do ativo"
            )
        api = self._api.raw
        channel = f"asset:{self.asset_ric}"
        payload = {
            "topic": channel,
            "event": "phx_join",
            "payload": {},
            "ref": str(getattr(api, "_ref_counter", 1)),
            "join_ref": str(getattr(api, "_ref_counter", 1)),
        }
        await api._send_websocket_message_async(json.dumps(payload))
        logger.info("Inscrito no canal %s", channel)

    async def _load_historical_candles(self) -> None:
        candles = await asyncio.to_thread(self._fetch_historical_candles_sync)
        if candles:
            self._seed_history(candles, "API")
            return

        result = await asyncio.to_thread(self._cache.load)
        self.state.cache_status = result.message

        if result.valid and result.candles:
            self._seed_history(result.candles, "cache local")
            return

        self.state.history_loaded = False
        expired = result.message.startswith("expirado")
        if expired:
            logger.warning(
                "Cache ignorado (%s) — período anterior descartado. "
                "Aguardando novas velas M5 ao vivo (sem dados de dias/sessões passadas).",
                result.message,
            )
        elif result.message in ("corrompido", "vazio", "cache_ausente"):
            logger.warning(
                "Operação imediata limitada — aguardando novas velas M5 (cache: %s).",
                result.message,
            )
        else:
            logger.warning(
                "Histórico indisponível — velas serão construídas via stream ao vivo."
            )

    def _auth_headers(self) -> dict[str, str]:
        return {
            "authorization-token": self.auth_token,
            "device-id": self.device_id,
            "device-type": "web",
            "accept": "application/json",
            "content-type": "application/json",
            "origin": "https://binomo.com",
            "referer": "https://binomo.com/",
        }

    def _fetch_historical_candles_sync(self) -> list[Candle]:
        headers = self._auth_headers()
        params = {
            "asset_id": self.asset_id,
            "sort": "desc",
            "size": self.timeframe_seconds,
            "limit": 120,
        }

        for url in CANDLE_API_URLS:
            try:
                response = requests.get(url, headers=headers, params=params, timeout=20)
                if response.status_code != 200:
                    logger.debug("Histórico %s → HTTP %s", url, response.status_code)
                    continue

                data = response.json()
                items = data.get("data", data) if isinstance(data, dict) else data
                if not isinstance(items, list) or not items:
                    continue

                candles: list[Candle] = []
                for item in reversed(items):
                    candle = self._parse_candle_item(item)
                    if candle:
                        candles.append(candle)
                if candles:
                    logger.info("Histórico obtido de %s (%d velas)", url, len(candles))
                    return candles
            except Exception as exc:
                logger.debug("Falha histórico %s: %s", url, exc)

        return []

    def _fetch_live_rate_sync(self) -> Optional[float]:
        headers = self._auth_headers()
        urls = [
            url.format(ric=self.asset_ric, asset_id=self.asset_id)
            for url in LIVE_RATE_URLS
        ]
        for url in urls:
            try:
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code != 200:
                    continue
                data = response.json()
                price = self._extract_price(data)
                if price is None and isinstance(data, dict) and "data" in data:
                    inner = data["data"]
                    if isinstance(inner, dict):
                        price = self._extract_price(inner)
                    elif isinstance(inner, list) and inner:
                        price = self._extract_price(inner[0])
                if price and price > 0:
                    return price
            except Exception:
                continue
        return None

    @staticmethod
    def _parse_candle_item(item: dict[str, Any]) -> Optional[Candle]:
        try:
            ts_raw = item.get("created_at") or item.get("time") or item.get("timestamp")
            if isinstance(ts_raw, (int, float)):
                ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
            else:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))

            open_p = float(item.get("open") or item.get("o") or item.get("rate_open", 0))
            high = float(item.get("high") or item.get("h") or item.get("rate_high", open_p))
            low = float(item.get("low") or item.get("l") or item.get("rate_low", open_p))
            close = float(item.get("close") or item.get("c") or item.get("rate", open_p))
            volume = float(item.get("volume", 0) or 0)

            if close <= 0:
                return None
            return Candle(timestamp=ts, open=open_p, high=high, low=low, close=close, volume=volume)
        except Exception:
            return None

    async def run(self) -> None:
        """Loop principal de streaming com reconexão automática."""
        self._running = True
        while self._running:
            try:
                if not self.state.connected:
                    await self.connect()

                await self._process_websocket_messages()
                await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                break
            except (BinomoConnectionError, BinomoAPIException, ConnectionError, OSError) as exc:
                await self._handle_disconnect(str(exc))
            except Exception as exc:
                logger.exception("Erro no stream: %s", exc)
                await self._handle_disconnect(str(exc))

    async def _process_websocket_messages(self) -> None:
        if self._api is None or not check_connect(self._api):
            raise BinomoConnectionError("WebSocket não conectado")
        ws = self._api.raw._ws_client
        if ws is None:
            raise BinomoConnectionError("WebSocket não inicializado")

        messages = getattr(ws, "_last_messages", [])
        while self._processed_msg_count < len(messages):
            msg = messages[self._processed_msg_count]
            self._processed_msg_count += 1
            self._parse_message(msg)

    def _parse_message(self, msg: dict[str, Any]) -> None:
        topic = str(msg.get("topic", ""))
        event = str(msg.get("event", ""))
        payload = msg.get("payload", {})
        if not isinstance(payload, dict):
            return

        self._maybe_ingest_candles(payload)

        if self._order_guard is not None:
            self._order_guard.ingest_message(event, payload)
            self.state.order_status = self._order_guard.order_status

        if not self._is_target_asset(topic, payload, event):
            return

        price = self._extract_price(payload)
        if price is None:
            return

        self._apply_price(price)

    def _maybe_ingest_candles(self, payload: dict[str, Any]) -> None:
        for key in ("candles", "history", "items", "data"):
            block = payload.get(key)
            if isinstance(block, list) and block and isinstance(block[0], dict):
                if not any(k in block[0] for k in ("open", "close", "rate", "o", "c")):
                    continue
                candles: list[Candle] = []
                for item in block:
                    if isinstance(item, dict):
                        candle = self._parse_candle_item(item)
                        if candle:
                            candles.append(candle)
                if len(candles) >= 5:
                    self._seed_history(candles, "WebSocket")
                return

    def _is_target_asset(self, topic: str, payload: dict[str, Any], event: str = "") -> bool:
        target_topic = f"asset:{self.asset_ric}"
        if topic == target_topic or self.asset_ric in topic:
            asset = payload.get("asset_ric") or payload.get("asset") or payload.get("ric")
            if asset and self.asset_ric not in str(asset) and self.asset_name not in str(asset):
                return False
            return True
        if event in ("social_trading_deal", "rate", "quote", "tick"):
            asset = payload.get("asset_ric") or payload.get("asset") or payload.get("ric")
            if asset and self.asset_ric in str(asset):
                return True
        return False

    def _apply_price(self, price: float) -> None:
        if price <= 0:
            return
        self.state.last_price = price
        self.state.last_message_at = datetime.now(timezone.utc)
        self.state.price_ticks += 1
        closed = self.aggregator.update(price)
        if closed:
            self.state.candles_count = len(self.aggregator.closed_candles)

    def tick_clock(self) -> None:
        """Fecha vela M5 no relógio mesmo sem tick novo (usa último preço)."""
        if self.state.last_price is None:
            return
        now = datetime.now(timezone.utc)
        current = self.aggregator._current
        if current is None:
            self.aggregator.update(self.state.last_price, now)
            return
        bucket = self.aggregator._bucket_start(now)
        if bucket > current.timestamp:
            self._apply_price(self.state.last_price)

    @staticmethod
    def _extract_price(payload: dict[str, Any]) -> Optional[float]:
        for key in (
            "rate",
            "close",
            "price",
            "bid",
            "ask",
            "value",
            "entrie_rate",
            "entry_rate",
            "current_rate",
            "last_rate",
            "open",
        ):
            if key in payload and payload[key] is not None:
                try:
                    val = float(payload[key])
                    if val > 0:
                        return val
                except (TypeError, ValueError):
                    continue

        for key in ("quote", "quotes", "data", "candle", "candles"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                found = DataEngine._extract_price(nested)
                if found:
                    return found
            elif isinstance(nested, list) and nested:
                item = nested[0]
                if isinstance(item, dict):
                    found = DataEngine._extract_price(item)
                    if found:
                        return found
        return None

    async def rate_poll_loop(self, interval: float = 2.0) -> None:
        """Polling REST de backup para garantir ticks de preço."""
        while self._running:
            try:
                if self.state.connected:
                    price = await asyncio.to_thread(self._fetch_live_rate_sync)
                    if price:
                        self._apply_price(price)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Rate poll falhou: %s", exc)
            await asyncio.sleep(interval)

    async def clock_loop(self, interval: float = 1.0) -> None:
        """Verifica fechamento de vela M5 pelo relógio."""
        while self._running:
            try:
                self.tick_clock()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Clock tick falhou: %s", exc)
            await asyncio.sleep(interval)

    async def cache_flush_loop(self, interval: float = CACHE_FLUSH_INTERVAL_SEC) -> None:
        """Persistência periódica — protege contra desligamento inesperado."""
        while self._running:
            try:
                await asyncio.sleep(interval)
                if self._cache.is_dirty:
                    await asyncio.to_thread(self._cache.flush)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Flush periódico do cache falhou: %s", exc)

    async def _handle_disconnect(self, reason: str) -> None:
        self.state.connected = False
        self.state.reconnect_attempts += 1
        wait = min(30, 2 ** min(self.state.reconnect_attempts, 5))
        self.state.status_message = f"Desconectado ({reason}). Reconectando em {wait}s..."
        logger.warning(self.state.status_message)

        await self._cleanup()
        await asyncio.sleep(wait)

    async def _cleanup(self) -> None:
        if self._api is not None:
            try:
                closer = getattr(self._api.raw, "close_sync", None)
                if callable(closer):
                    closer()
                else:
                    close_fn = self._api.raw.close
                    if asyncio.iscoroutinefunction(close_fn):
                        await close_fn()
                    else:
                        close_fn()
            except Exception:
                pass
        self._api = None
        self._processed_msg_count = 0

    async def wait_closed_candle(self, timeout: float = 310.0) -> Optional[Candle]:
        try:
            return await asyncio.wait_for(self._candle_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def get_dataframe(self) -> pd.DataFrame:
        return self.aggregator.to_dataframe()

    def seconds_to_candle_close(self) -> int:
        now = datetime.now(timezone.utc)
        epoch = int(now.timestamp())
        elapsed = epoch % self.timeframe_seconds
        return self.timeframe_seconds - elapsed

    async def refresh_balance(self) -> Optional[float]:
        if not self._api or not self.state.connected:
            return self.state.balance
        try:
            balance = await self._fetch_balance()
            self.state.balance = balance
            return balance
        except Exception as exc:
            logger.debug("Falha ao atualizar saldo: %s", exc)
            return self.state.balance

    async def _fetch_balance(self) -> Optional[float]:
        if self._api is None:
            return None
        balance = await self._api.get_balance()
        return float(balance.amount) if balance else None

    async def flush_cache(self) -> None:
        await asyncio.to_thread(self._cache.flush)

    async def stop(self) -> None:
        self._running = False
        await self.flush_cache()
        await self._cleanup()
        self.state.connected = False
        self.state.status_message = "Encerrado"

    async def test_connection(self) -> dict[str, Any]:
        """Teste rápido de conexão (usado no startup)."""
        result: dict[str, Any] = {
            "connected": False,
            "asset_ric": self.asset_ric,
            "balance": None,
            "error": None,
        }
        try:
            await self.connect()
            if self._api is None or not self.is_connected():
                result["error"] = "Falha na conexão — API não inicializada"
                return result
            balance = await self._api.get_balance()
            result["connected"] = True
            result["balance"] = balance.amount if balance else None
        except Exception as exc:
            result["error"] = classify_connection_error(exc)
            logger.error(result["error"])
            await self._cleanup()
        return result
