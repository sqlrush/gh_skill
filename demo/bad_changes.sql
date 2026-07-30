-- demo/bad_changes.sql —— 一份「上线前评审」的变更脚本，故意踩满规范红线。
-- 用途：给 gaussdb-sqlreview 做静态文本审查的样例（不连库）。
--
--   python3 skills/gaussdb-sqlreview/scripts/sqlreview.py --file demo/bad_changes.sql
--
-- 下面这条 DELETE 写在注释里，不应该被报出来 —— 用来验证 lexer 不会误伤注释：
--   DELETE FROM orders WHERE 1 = 1;

/* ---------------------------------------------------------------
   1. 建表：无主键 + 外键 + 驼峰表名 + 驼峰列名 + CHAR 定长
   --------------------------------------------------------------- */
CREATE TABLE OrderItems (
    Id          int,
    order_no    varchar(32),
    customer_id int REFERENCES customers(id),
    status      char(2),
    Qty         int,
    price       numeric(12, 2),
    created_at  timestamp
);

/* ---------------------------------------------------------------
   2. 索引：命名不合规 + 列数超上限
   --------------------------------------------------------------- */
CREATE INDEX orderitems_status ON order_items (status);

CREATE INDEX idx_oi_toowide
    ON order_items (order_no, customer_id, status, qty, price, created_at);

/* ---------------------------------------------------------------
   3. DML：物理删除 + 无 WHERE 的全表更新
   --------------------------------------------------------------- */
DELETE FROM order_items WHERE created_at < '2024-01-01';

UPDATE order_items SET status = '9';

/* ---------------------------------------------------------------
   4. DQL：SELECT * + 前置模糊匹配
   --------------------------------------------------------------- */
SELECT * FROM order_items WHERE order_no LIKE '%2024';

-- 这条是合规的对照组：后置匹配可以走索引，且显式列出了列
SELECT id, order_no FROM order_items WHERE order_no LIKE '2024%';

-- 这条也是对照组：字符串字面量里出现的 '%abc' 不该被当成前置模糊匹配
INSERT INTO audit_log (actor, action) VALUES ('etl', 'pattern is %abc');
