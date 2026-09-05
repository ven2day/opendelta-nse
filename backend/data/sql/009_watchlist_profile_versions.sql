CREATE TABLE IF NOT EXISTS watchlist_profiles (
    profile_id uuid PRIMARY KEY,
    market text NOT NULL CHECK (market IN ('NSE', 'CRYPTO')),
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS watchlist_profiles_market_name
    ON watchlist_profiles (market, lower(name));

CREATE TABLE IF NOT EXISTS watchlist_profile_versions (
    profile_version_id uuid PRIMARY KEY,
    profile_id uuid NOT NULL REFERENCES watchlist_profiles(profile_id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    filters jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_kind text NOT NULL CHECK (source_kind IN ('MARKET', 'PRESET', 'CUSTOM')),
    preset_id text,
    symbols jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (profile_id, version),
    CHECK (
        (source_kind = 'MARKET' AND preset_id IS NULL AND symbols = '[]'::jsonb)
        OR (source_kind = 'PRESET' AND preset_id IS NOT NULL AND symbols = '[]'::jsonb)
        OR (source_kind = 'CUSTOM' AND preset_id IS NULL AND jsonb_array_length(symbols) > 0)
    )
);

CREATE INDEX IF NOT EXISTS watchlist_profile_versions_recent
    ON watchlist_profile_versions (profile_id, version DESC);

ALTER TABLE screener_runs
    ADD COLUMN IF NOT EXISTS profile_version_id uuid REFERENCES watchlist_profile_versions(profile_version_id) ON DELETE SET NULL;
