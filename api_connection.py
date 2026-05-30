"""
Utilitários de conexão Binomo — validação defensiva antes de operações de mercado.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from BinomoAPI.api import BinomoAPI
from BinomoAPI.exceptions import BinomoAPIException, ConnectionError as BinomoConnectionError

logger = logging.getLogger(__name__)


def classify_connection_error(exc: BaseException) -> str:
    """Mensagem clara para log/UI."""
    text = str(exc).lower()
    if "auth" in text or "token" in text or "401" in text or "403" in text:
        return "Erro de autenticação — verifique AUTH_TOKEN e DEVICE_ID no .env"
    if "websocket" in text or "connect" in text or "network" in text:
        return f"Falha na conexão — {exc}"
    if isinstance(exc, BinomoAPIException):
        return f"Erro da API Binomo — {exc}"
    return f"Falha na conexão — {exc}"


def is_api_initialized(api: Any) -> bool:
    return api is not None


def check_connect(api: Any) -> bool:
    """
    Verifica se a API existe e o WebSocket está ativo.
    Compatível com BinomoAPI (atributo _ws_client._connected).
    """
    if api is None:
        return False

    raw = api.raw if hasattr(api, "raw") else api
    ws = getattr(raw, "_ws_client", None)
    if ws is None:
        return False

    if hasattr(ws, "is_connected") and callable(ws.is_connected):
        try:
            return bool(ws.is_connected())
        except Exception:
            pass

    return bool(getattr(ws, "_connected", False))


async def connect_api(
    auth_token: str,
    device_id: str,
    *,
    demo: bool = True,
    enable_logging: bool = False,
) -> tuple[Optional[BinomoAPI], Optional[str]]:
    """
    Cria BinomoAPI e conecta. Retorna (api, erro).
    Nunca deixa api None sem mensagem de erro quando falha.
    """
    if not auth_token or not device_id:
        return None, "Erro de autenticação — AUTH_TOKEN ou DEVICE_ID ausentes no .env"

    api: Optional[BinomoAPI] = None
    try:
        api = BinomoAPI(
            auth_token=auth_token,
            device_id=device_id,
            demo=demo,
            enable_logging=enable_logging,
        )
        ok = await api.connect()
        if not ok:
            await _safe_close(api)
            return None, "Falha na conexão — WebSocket não estabelecido"

        if not check_connect(api):
            await _safe_close(api)
            return None, "Falha na conexão — sessão não ficou ativa após connect()"

        return api, None

    except (BinomoConnectionError, BinomoAPIException, ConnectionError, OSError) as exc:
        await _safe_close(api)
        return None, classify_connection_error(exc)
    except Exception as exc:
        await _safe_close(api)
        logger.exception("Erro inesperado ao conectar BinomoAPI")
        return None, classify_connection_error(exc)


async def _safe_close(api: Optional[BinomoAPI]) -> None:
    if api is None:
        return
    try:
        await api.close()
    except Exception:
        try:
            closer = getattr(api, "close_sync", None)
            if callable(closer):
                closer()
        except Exception:
            pass


async def safe_get_candles(engine: Any, asset_ric: str | None = None) -> Any:
    """
    Obtém velas de forma segura (nunca chama método em api None).
    Usa o DataFrame do motor de dados (stream + cache).
    """
    import pandas as pd

    if engine is None:
        logger.error("safe_get_candles: engine é None")
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    if hasattr(engine, "get_candles_safe"):
        return await engine.get_candles_safe()

    if not check_connect(getattr(engine, "_api", None)):
        logger.warning(
            "safe_get_candles: conexão inativa — retornando dados já em memória (se houver)"
        )

    if hasattr(engine, "get_dataframe"):
        return engine.get_dataframe()

    logger.error("safe_get_candles: engine sem método get_dataframe")
    return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
