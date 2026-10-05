-- 三层资料库统一存一张表，layer 区分 general / scene / realtime。
CREATE EXTENSION IF NOT EXISTS vector;

-- 维度取 1024：与 TRAVEL_EMBEDDING_DIM 默认值一致。
-- 无密钥时走本地 hash embedding（也是 1024 维），配了 TRAVEL_EMBEDDING_MODEL 则走
-- 远端 embedding 接口（ecnu-embedding-small 实测就是 1024 维）—— 两条路共用这一张表，
-- 换配置不需要改建表。改这个数字必须同时改 TRAVEL_EMBEDDING_DIM，tests 里有断言钉住。
-- 注意：建表只在空数据卷时执行，已有旧表需 `docker compose down -v` 重建。
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id      TEXT PRIMARY KEY,
    layer         TEXT        NOT NULL CHECK (layer IN ('general', 'scene', 'realtime')),
    scene         TEXT        NOT NULL DEFAULT 'general',
    destination   TEXT        NOT NULL DEFAULT '',
    text          TEXT        NOT NULL,
    source        TEXT        NOT NULL DEFAULT '',
    source_url    TEXT,
    effective_date DATE,
    fresh_until   DATE,
    metadata      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    embedding     vector(1024)
);

-- 元数据预过滤走 (layer, scene, destination)
CREATE INDEX IF NOT EXISTS chunks_layer_scene_destination_idx
    ON chunks (layer, scene, destination);

-- 向量召回走 HNSW + 余弦距离
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);
