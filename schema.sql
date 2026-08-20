CREATE TABLE IF NOT EXISTS radio_themes (
  id BIGSERIAL PRIMARY KEY,
  broadcast_date DATE NOT NULL,
  station_id TEXT NOT NULL,
  station_name TEXT NOT NULL,
  program_name TEXT NOT NULL,
  start_at TIMESTAMPTZ NOT NULL,
  end_at TIMESTAMPTZ NOT NULL,
  theme TEXT,
  description TEXT,
  program_url TEXT,
  message_url TEXT,
  source_url TEXT NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (broadcast_date, station_id, start_at, program_name)
);

CREATE INDEX IF NOT EXISTS radio_themes_date_start_idx
  ON radio_themes (broadcast_date, start_at);
