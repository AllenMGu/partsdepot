-- 申请编号改为「APP-日期-当日流水」：序号每天从 0001 重新计数，并持久化到 reference 列。
-- 幂等：列/索引均使用 IF NOT EXISTS；回填只处理 reference 为空的行。
BEGIN;

ALTER TABLE requests ADD COLUMN IF NOT EXISTS reference varchar(50);
ALTER TABLE requests_archive ADD COLUMN IF NOT EXISTS reference varchar(50);

-- 存量回填：按创建日期分组、按 id 升序编号
WITH numbered AS (
    SELECT id,
           to_char(create_time, 'YYYYMMDD') AS d,
           row_number() OVER (PARTITION BY to_char(create_time, 'YYYYMMDD') ORDER BY id) AS rn
    FROM requests
    WHERE reference IS NULL
)
UPDATE requests r
SET reference = 'APP-' || n.d || '-' || lpad(n.rn::text, 4, '0')
FROM numbered n
WHERE r.id = n.id;

WITH numbered AS (
    SELECT id,
           to_char(create_time, 'YYYYMMDD') AS d,
           row_number() OVER (PARTITION BY to_char(create_time, 'YYYYMMDD') ORDER BY original_id) AS rn
    FROM requests_archive
    WHERE reference IS NULL
)
UPDATE requests_archive a
SET reference = 'APP-' || n.d || '-' || lpad(n.rn::text, 4, '0')
FROM numbered n
WHERE a.id = n.id;

-- 新申请唯一；归档表保留索引便于按编号检索
CREATE UNIQUE INDEX IF NOT EXISTS uq_requests_reference ON requests(reference);
CREATE INDEX IF NOT EXISTS ix_requests_archive_reference ON requests_archive(reference);

COMMIT;
