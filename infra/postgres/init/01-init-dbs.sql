-- 首次初始化：创建测试库，并为两个库启用 pgvector 扩展。
-- 注意：此脚本只在数据卷第一次创建时执行一次；
-- 修改后需要 `docker compose down -v` 清空数据再启动才会重新执行。
CREATE DATABASE campusflow_test;
\connect campusflow_test
CREATE EXTENSION IF NOT EXISTS vector;
\connect campusflow
CREATE EXTENSION IF NOT EXISTS vector;
