BEGIN;

ALTER TABLE goods
    ADD COLUMN IF NOT EXISTS stock_alert_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS min_stock_quantity double precision;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'goods'::regclass AND conname = 'goods_valid_stock_alert') THEN
        ALTER TABLE goods ADD CONSTRAINT goods_valid_stock_alert CHECK (
            (min_stock_quantity IS NULL OR
                (min_stock_quantity >= 0 AND min_stock_quantity < 'Infinity'::double precision))
            AND (NOT stock_alert_enabled OR min_stock_quantity IS NOT NULL)
        );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS goods_stock_alert_audits (
    id serial PRIMARY KEY,
    goods_id integer NOT NULL,
    warehouse_id integer NOT NULL,
    operator_id integer NOT NULL,
    before_json text,
    after_json text NOT NULL,
    create_time timestamp NOT NULL
);

COMMIT;
