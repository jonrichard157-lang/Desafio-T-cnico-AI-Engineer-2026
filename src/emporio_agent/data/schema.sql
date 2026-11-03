-- Schema do banco operacional da Empório da Música.
-- Gerado a partir dos CSVs em data/ pelo módulo emporio_agent.data.loader.

PRAGMA foreign_keys = ON;

CREATE TABLE categories (
    category_id   INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT
);

CREATE TABLE products (
    product_id     INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    category_id    INTEGER NOT NULL REFERENCES categories(category_id),
    price_brl      REAL NOT NULL,
    stock_quantity INTEGER NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('active', 'discontinued', 'coming_soon')),
    description    TEXT,
    specs_json     TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT,
    -- Texto normalizado (minúsculas, sem acento) de nome + categoria + descrição + specs,
    -- usado para busca por palavras-chave.
    search_text    TEXT NOT NULL
);

CREATE TABLE promotions (
    promotion_id     INTEGER PRIMARY KEY,
    product_id       INTEGER NOT NULL REFERENCES products(product_id),
    discount_percent REAL NOT NULL,
    description      TEXT,
    is_active        INTEGER NOT NULL CHECK (is_active IN (0, 1))
);

CREATE TABLE customers (
    customer_id  INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    phone        TEXT,
    phone_digits TEXT,           -- telefone só com dígitos, para comparação
    email        TEXT,
    email_norm   TEXT,           -- e-mail em minúsculas, para comparação
    city         TEXT
);

CREATE TABLE orders (
    order_id           INTEGER PRIMARY KEY,
    customer_id        INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date         TEXT NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'shipped', 'delivered', 'cancelled')),
    total_brl          REAL NOT NULL,
    payment_method     TEXT,
    tracking_code      TEXT,
    estimated_delivery TEXT,
    notes              TEXT
);

CREATE TABLE order_items (
    order_id   INTEGER NOT NULL REFERENCES orders(order_id),
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity   INTEGER NOT NULL CHECK (quantity > 0)
);

-- Problemas de qualidade encontrados durante a carga (documentação / auditoria).
CREATE TABLE data_quality_issues (
    entity    TEXT NOT NULL,
    entity_id TEXT,
    issue     TEXT NOT NULL
);

CREATE INDEX idx_products_category ON products(category_id);
CREATE INDEX idx_promotions_product ON promotions(product_id, is_active);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_order_items_order ON order_items(order_id);
CREATE INDEX idx_customers_phone ON customers(phone_digits);
CREATE INDEX idx_customers_email ON customers(email_norm);

-- Visão de catálogo: concentra as regras de negócio de preço e disponibilidade
-- num único lugar, para que as ferramentas do agente não as reimplementem.
--   * Disponível para venda = status 'active' E estoque > 0.
--   * Promoção vigente = maior desconto entre as promoções com is_active = 1.
--   * Preço no PIX = 5% de desconto, SOMENTE se não houver promoção vigente
--     (manual, seção 6.2: promoções não são cumulativas).
CREATE VIEW product_catalog AS
WITH best_promo AS (
    SELECT product_id,
           MAX(discount_percent) AS discount_percent
    FROM promotions
    WHERE is_active = 1
    GROUP BY product_id
)
SELECT
    p.product_id,
    p.name,
    p.category_id,
    c.name                                    AS category_name,
    p.price_brl,
    p.stock_quantity,
    p.status,
    p.description,
    p.specs_json,
    p.search_text,
    (p.status = 'active' AND p.stock_quantity > 0) AS is_available,
    bp.discount_percent                       AS promo_discount_percent,
    (SELECT description FROM promotions pr
      WHERE pr.product_id = p.product_id AND pr.is_active = 1
      ORDER BY pr.discount_percent DESC LIMIT 1) AS promo_description,
    ROUND(CASE WHEN bp.discount_percent IS NOT NULL
               THEN p.price_brl * (1 - bp.discount_percent / 100.0)
               ELSE p.price_brl END, 2)        AS final_price_brl,
    ROUND(CASE WHEN bp.discount_percent IS NULL
               THEN p.price_brl * 0.95 END, 2) AS pix_price_brl
FROM products p
JOIN categories c ON c.category_id = p.category_id
LEFT JOIN best_promo bp ON bp.product_id = p.product_id;

