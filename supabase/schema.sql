-- Binomo Signal Generator — schema Supabase (PostgreSQL)
-- Execute no SQL Editor do Supabase: https://supabase.com/dashboard

-- ---------------------------------------------------------------------------
-- trading_logs — sinais e eventos operacionais
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trading_logs (
    id            BIGSERIAL PRIMARY KEY,
    instance_id   TEXT NOT NULL,
    instance_label TEXT,
    asset_ric     TEXT NOT NULL DEFAULT 'Z-CRY/IDX',
    event_type    TEXT NOT NULL DEFAULT 'SIGNAL',  -- SIGNAL | FILTER | ERROR | INFO
    signal        TEXT,                             -- COMPRA | VENDA | NULL
    confidence    SMALLINT,
    price         DOUBLE PRECISION,
    reason        TEXT,
    strategy_file TEXT,
    market_type   TEXT,
    metadata      JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trading_logs_created ON trading_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trading_logs_asset ON trading_logs (asset_ric, created_at DESC);

-- ---------------------------------------------------------------------------
-- strategy_performance — métricas agregadas por estratégia
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_performance (
    strategy_file   TEXT NOT NULL,
    asset_ric       TEXT NOT NULL DEFAULT 'Z-CRY/IDX',
    total_signals   INTEGER NOT NULL DEFAULT 0,
    wins            INTEGER NOT NULL DEFAULT 0,
    losses          INTEGER NOT NULL DEFAULT 0,
    last_signal_at  TIMESTAMPTZ,
    last_market_type TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (strategy_file, asset_ric)
);

-- ---------------------------------------------------------------------------
-- market_data_cache — velas M5 compartilhadas (fonte da verdade em nuvem)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_data_cache (
    asset_ric          TEXT NOT NULL,
    timeframe_seconds  INTEGER NOT NULL DEFAULT 300,
    candles            JSONB NOT NULL DEFAULT '[]',
    candle_count       INTEGER NOT NULL DEFAULT 0,
    last_candle_at     TIMESTAMPTZ,
    written_by         TEXT,
    version            BIGINT NOT NULL DEFAULT 1,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (asset_ric, timeframe_seconds)
);

-- ---------------------------------------------------------------------------
-- bot_status — estado global por ativo (coordenação AWS / PC)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bot_status (
    asset_ric            TEXT PRIMARY KEY,
    active_instance_id   TEXT NOT NULL,
    active_instance_label TEXT,
    instance_role        TEXT NOT NULL DEFAULT 'primary',  -- primary | secondary
    order_status         TEXT NOT NULL DEFAULT 'NONE',     -- NONE | OPEN | SIGNAL_LOCK
    active_strategy_file TEXT,
    market_type          TEXT,
    signal_lock_until    TIMESTAMPTZ,
    last_heartbeat       TIMESTAMPTZ NOT NULL DEFAULT now(),
    version              BIGINT NOT NULL DEFAULT 1,
    metadata             JSONB NOT NULL DEFAULT '{}',
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bot_status_heartbeat ON bot_status (last_heartbeat DESC);

-- ---------------------------------------------------------------------------
-- RPC: aquisição atômica de lock operacional (evita dupla entrada AWS+PC)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION try_acquire_signal_lock(
    p_asset_ric TEXT,
    p_instance_id TEXT,
    p_instance_label TEXT,
    p_lock_seconds INTEGER DEFAULT 300,
    p_peer_ttl_seconds INTEGER DEFAULT 120
) RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_row bot_status%ROWTYPE;
    v_now TIMESTAMPTZ := now();
BEGIN
    SELECT * INTO v_row FROM bot_status WHERE asset_ric = p_asset_ric FOR UPDATE;

    IF NOT FOUND THEN
        INSERT INTO bot_status (
            asset_ric, active_instance_id, active_instance_label,
            order_status, signal_lock_until, last_heartbeat, version
        ) VALUES (
            p_asset_ric, p_instance_id, p_instance_label,
            'SIGNAL_LOCK', v_now + (p_lock_seconds || ' seconds')::INTERVAL,
            v_now, 1
        );
        RETURN TRUE;
    END IF;

    -- Outra instância com lock ou ordem aberta e heartbeat recente
    IF v_row.active_instance_id <> p_instance_id
       AND v_row.last_heartbeat > v_now - (p_peer_ttl_seconds || ' seconds')::INTERVAL
       AND (
           v_row.order_status IN ('OPEN', 'SIGNAL_LOCK')
           OR (v_row.signal_lock_until IS NOT NULL AND v_row.signal_lock_until > v_now)
       ) THEN
        RETURN FALSE;
    END IF;

    UPDATE bot_status SET
        active_instance_id = p_instance_id,
        active_instance_label = p_instance_label,
        order_status = 'SIGNAL_LOCK',
        signal_lock_until = v_now + (p_lock_seconds || ' seconds')::INTERVAL,
        last_heartbeat = v_now,
        version = version + 1,
        updated_at = v_now
    WHERE asset_ric = p_asset_ric;

    RETURN TRUE;
END;
$$;

-- RLS (opcional): desative se usar service_role key no robô
ALTER TABLE trading_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_performance ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_data_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_status ENABLE ROW LEVEL SECURITY;

-- Política permissiva para service_role (ajuste conforme sua segurança)
-- CREATE POLICY "service_all" ON trading_logs FOR ALL USING (true) WITH CHECK (true);
