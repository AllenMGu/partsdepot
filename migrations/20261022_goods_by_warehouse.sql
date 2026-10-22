-- PostgreSQL. Stop the application and back up the database before this one-time migration.
-- This transaction is intentionally fail-closed for unassigned legacy goods.
BEGIN;
LOCK TABLE goods, stock, inventory_records, check_records, check_order_item,
           inbound_order_item, outbound_order_item, requests, request_items IN ACCESS EXCLUSIVE MODE;

CREATE TEMP TABLE goods_manual_assignment (goods_id integer PRIMARY KEY, warehouse_id integer NOT NULL) ON COMMIT DROP;
-- Before running, insert mappings for old goods with no stock/order/history references, e.g.:
-- INSERT INTO goods_manual_assignment VALUES (123, 2);
-- Put those INSERT statements here, inside this transaction.

CREATE TEMP TABLE goods_scope (old_id integer NOT NULL, warehouse_id integer NOT NULL,
                               new_id integer, PRIMARY KEY(old_id, warehouse_id)) ON COMMIT DROP;
INSERT INTO goods_scope(old_id, warehouse_id)
SELECT DISTINCT goods_id, warehouse_id FROM (
    SELECT goods_id, warehouse_id FROM stock
    UNION ALL SELECT goods_id, warehouse_id FROM inventory_records
    UNION ALL SELECT goods_id, warehouse_id FROM check_records
    UNION ALL SELECT i.goods_id, h.warehouse_id FROM check_order_item i JOIN check_order_header h ON h.id=i.header_id
    UNION ALL SELECT i.goods_id, h.warehouse_id FROM inbound_order_item i JOIN inbound_order_header h ON h.id=i.header_id
    UNION ALL SELECT i.goods_id, h.warehouse_id FROM outbound_order_item i JOIN outbound_order_header h ON h.id=i.header_id
    -- Request items store barcode snapshots, not goods_id. Include pending requests
    -- even when the requested warehouse has never stocked the item.
    UNION ALL SELECT g.id, r.warehouse_id FROM request_items i
      JOIN requests r ON r.id=i.request_id JOIN goods g ON g.barcode=i.barcode
      WHERE r.status='pending' AND r.warehouse_id IS NOT NULL
    -- Match the legacy approval fallback if exactly one warehouse is active.
    UNION ALL SELECT g.id, w.id FROM request_items i
      JOIN requests r ON r.id=i.request_id JOIN goods g ON g.barcode=i.barcode
      CROSS JOIN warehouses w
      WHERE r.status='pending' AND r.warehouse_id IS NULL AND w.is_active IS TRUE
        AND (SELECT count(*) FROM warehouses WHERE is_active IS TRUE)=1
    UNION ALL SELECT goods_id, warehouse_id FROM goods_manual_assignment
) refs WHERE goods_id IS NOT NULL AND warehouse_id IS NOT NULL;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM goods g WHERE NOT EXISTS (SELECT 1 FROM goods_scope s WHERE s.old_id=g.id)) THEN
    RAISE EXCEPTION 'Unassigned goods exist. Add rows to goods_manual_assignment and retry.';
  END IF;
  IF EXISTS (SELECT 1 FROM goods_scope s LEFT JOIN warehouses w ON w.id=s.warehouse_id WHERE w.id IS NULL) THEN
    RAISE EXCEPTION 'Historical goods reference missing warehouses. Fix orphan data before migrating.';
  END IF;
  IF EXISTS (SELECT 1 FROM request_items i JOIN requests r ON r.id=i.request_id
             LEFT JOIN goods g ON g.barcode=i.barcode
             WHERE r.status='pending' AND r.warehouse_id IS NOT NULL AND g.id IS NULL) THEN
    RAISE EXCEPTION 'Pending requests reference deleted goods. Resolve those requests before migrating.';
  END IF;
END $$;

ALTER TABLE goods ADD COLUMN warehouse_id integer;
ALTER TABLE goods ADD CONSTRAINT goods_warehouse_id_fkey FOREIGN KEY (warehouse_id) REFERENCES warehouses(id);
-- Old SQLAlchemy schema has a globally unique index on barcode; remove it before cloning.
ALTER TABLE goods DROP CONSTRAINT IF EXISTS goods_barcode_key;
DROP INDEX IF EXISTS ix_goods_barcode;

-- Keep the original identity for its lowest warehouse and clone the catalog row for each other warehouse.
UPDATE goods g SET warehouse_id = x.warehouse_id
FROM (SELECT old_id, MIN(warehouse_id) warehouse_id FROM goods_scope GROUP BY old_id) x
WHERE g.id=x.old_id;
UPDATE goods_scope s SET new_id=old_id WHERE s.warehouse_id=(SELECT MIN(t.warehouse_id) FROM goods_scope t WHERE t.old_id=s.old_id);

DO $$ DECLARE r record; clone_id integer; BEGIN
  FOR r IN SELECT old_id, warehouse_id FROM goods_scope WHERE new_id IS NULL ORDER BY old_id, warehouse_id LOOP
    INSERT INTO goods (warehouse_id, barcode, name, spec, unit, price, create_time)
    SELECT r.warehouse_id, barcode, name, spec, unit, price, create_time FROM goods WHERE id=r.old_id
    RETURNING id INTO clone_id;
    UPDATE goods_scope SET new_id=clone_id WHERE old_id=r.old_id AND warehouse_id=r.warehouse_id;
  END LOOP;
END $$;

UPDATE stock t SET goods_id=s.new_id FROM goods_scope s WHERE t.goods_id=s.old_id AND t.warehouse_id=s.warehouse_id;
UPDATE inventory_records t SET goods_id=s.new_id FROM goods_scope s WHERE t.goods_id=s.old_id AND t.warehouse_id=s.warehouse_id;
UPDATE check_records t SET goods_id=s.new_id FROM goods_scope s WHERE t.goods_id=s.old_id AND t.warehouse_id=s.warehouse_id;
UPDATE check_order_item t SET goods_id=s.new_id FROM check_order_header h, goods_scope s
WHERE t.header_id=h.id AND t.goods_id=s.old_id AND h.warehouse_id=s.warehouse_id;
UPDATE inbound_order_item t SET goods_id=s.new_id FROM inbound_order_header h, goods_scope s
WHERE t.header_id=h.id AND t.goods_id=s.old_id AND h.warehouse_id=s.warehouse_id;
UPDATE outbound_order_item t SET goods_id=s.new_id FROM outbound_order_header h, goods_scope s
WHERE t.header_id=h.id AND t.goods_id=s.old_id AND h.warehouse_id=s.warehouse_id;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM stock t JOIN goods g ON g.id=t.goods_id WHERE t.warehouse_id <> g.warehouse_id)
     OR EXISTS (SELECT 1 FROM inventory_records t JOIN goods g ON g.id=t.goods_id WHERE t.warehouse_id <> g.warehouse_id)
     OR EXISTS (SELECT 1 FROM check_records t JOIN goods g ON g.id=t.goods_id WHERE t.warehouse_id <> g.warehouse_id)
     OR EXISTS (SELECT 1 FROM check_order_item t JOIN goods g ON g.id=t.goods_id JOIN check_order_header h ON h.id=t.header_id WHERE h.warehouse_id <> g.warehouse_id)
     OR EXISTS (SELECT 1 FROM inbound_order_item t JOIN goods g ON g.id=t.goods_id JOIN inbound_order_header h ON h.id=t.header_id WHERE h.warehouse_id <> g.warehouse_id)
     OR EXISTS (SELECT 1 FROM outbound_order_item t JOIN goods g ON g.id=t.goods_id JOIN outbound_order_header h ON h.id=t.header_id WHERE h.warehouse_id <> g.warehouse_id) THEN
    RAISE EXCEPTION 'Cross-warehouse historical references remain. Migration rolled back.';
  END IF;
END $$;

ALTER TABLE goods ALTER COLUMN warehouse_id SET NOT NULL;
CREATE INDEX ix_goods_barcode ON goods(barcode);
CREATE INDEX ix_goods_warehouse_id ON goods(warehouse_id);
ALTER TABLE goods ADD CONSTRAINT uq_goods_warehouse_barcode UNIQUE(warehouse_id, barcode);
COMMIT;
