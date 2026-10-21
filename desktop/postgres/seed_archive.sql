-- 跨系统归档验收夹具：资料、任务、候选与证据、计划、引用各一条。
-- 由 .github/workflows/desktop-archive-compat.yml 在两个方向分别调用：
--   psql -v ON_ERROR_STOP=1 -v ws=... -v sem=... -v course=... -v code=... \
--        -v material_title=... -v chunk_text=... -v task_title=... -v candidate_title=... \
--        -f desktop/postgres/seed_archive.sql
BEGIN;

INSERT INTO workspaces (name, timezone) VALUES (:'ws', 'Asia/Shanghai');

INSERT INTO semesters (workspace_id, name, start_date, end_date, archived)
SELECT id, :'sem', DATE '2026-09-01', DATE '2027-01-15', false
FROM workspaces WHERE name = :'ws';

INSERT INTO courses (workspace_id, semester_id, name, code)
SELECT w.id, s.id, :'course', :'code'
FROM workspaces w
JOIN semesters s ON s.workspace_id = w.id
WHERE w.name = :'ws' AND s.name = :'sem';

INSERT INTO materials (workspace_id, semester_id, course_id, title, source_type, status, archived)
SELECT w.id, s.id, c.id, :'material_title', 'text', 'ready', false
FROM workspaces w
JOIN semesters s ON s.workspace_id = w.id
JOIN courses c ON c.semester_id = s.id
WHERE w.name = :'ws' AND s.name = :'sem' AND c.name = :'course';

INSERT INTO material_versions (material_id, version_no, checksum, source_metadata)
SELECT m.id, 1, repeat('a', 64), '{"note": "cross-os fixture"}'::jsonb
FROM materials m
JOIN workspaces w ON w.id = m.workspace_id
WHERE w.name = :'ws' AND m.title = :'material_title';

INSERT INTO source_chunks (version_id, seq, locator_type, locator_value, paragraph, text)
SELECT v.id, 0, 'paragraph', 'P1', 1, :'chunk_text'
FROM material_versions v
JOIN materials m ON m.id = v.material_id
JOIN workspaces w ON w.id = m.workspace_id
WHERE w.name = :'ws' AND m.title = :'material_title' AND v.version_no = 1;

INSERT INTO tasks (workspace_id, course_id, title, due_date, progress, priority, estimated_minutes)
SELECT w.id, c.id, :'task_title', DATE '2026-12-20', 'in_progress', 'high', 90
FROM workspaces w
JOIN courses c ON c.workspace_id = w.id
WHERE w.name = :'ws' AND c.name = :'course';

INSERT INTO extracted_items (workspace_id, source_version_id, kind, review_status, semester_id, course_id)
SELECT w.id, v.id, 'task', 'pending', s.id, c.id
FROM workspaces w
JOIN semesters s ON s.workspace_id = w.id
JOIN courses c ON c.semester_id = s.id
JOIN materials m ON m.workspace_id = w.id AND m.course_id = c.id
JOIN material_versions v ON v.material_id = m.id
WHERE w.name = :'ws' AND c.name = :'course' AND m.title = :'material_title' AND v.version_no = 1;

INSERT INTO extracted_fields (candidate_id, name, value, origin)
SELECT i.id, 'title', to_jsonb(:'candidate_title'::text), 'document'
FROM extracted_items i
JOIN workspaces w ON w.id = i.workspace_id
WHERE w.name = :'ws' AND i.kind = 'task'
ORDER BY i.id DESC
LIMIT 1;

INSERT INTO candidate_field_evidence (field_id, source_chunk_id, quote)
SELECT f.id, ch.id, :'chunk_text'
FROM extracted_fields f
JOIN extracted_items i ON i.id = f.candidate_id
JOIN workspaces w ON w.id = i.workspace_id
JOIN material_versions v ON v.id = i.source_version_id
JOIN source_chunks ch ON ch.version_id = v.id
WHERE w.name = :'ws' AND f.name = 'title';

INSERT INTO plan_drafts (
  workspace_id, semester_id, range_start, range_end, status, version,
  input_snapshot, input_fingerprint, assumptions, gaps, unscheduled, conflicts, coverage
)
SELECT w.id, s.id, DATE '2026-11-30', DATE '2026-12-13', 'accepted', 1,
  '{"tasks": []}'::jsonb, repeat('b', 64), '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
  '[]'::jsonb, '{}'::jsonb
FROM workspaces w
JOIN semesters s ON s.workspace_id = w.id
WHERE w.name = :'ws' AND s.name = :'sem';

INSERT INTO plan_blocks (
  draft_id, task_id, kind, title, starts_at, ends_at, minutes, seq, locked, reason, constraint_source
)
SELECT d.id, t.id, 'task', :'task_title', TIMESTAMPTZ '2026-12-01 10:00:00+08',
  TIMESTAMPTZ '2026-12-01 11:30:00+08', 90, 0, true, '跨系统归档验收', '[]'::jsonb
FROM plan_drafts d
JOIN workspaces w ON w.id = d.workspace_id
JOIN tasks t ON t.workspace_id = w.id
WHERE w.name = :'ws' AND d.status = 'accepted' AND t.title = :'task_title'
ORDER BY d.id DESC
LIMIT 1;

INSERT INTO qa_conversations (workspace_id, semester_id, course_id, mode)
SELECT w.id, s.id, c.id, 'keyword'
FROM workspaces w
JOIN semesters s ON s.workspace_id = w.id
JOIN courses c ON c.semester_id = s.id
WHERE w.name = :'ws' AND c.name = :'course';

INSERT INTO qa_turns (
  conversation_id, question, answer, insufficient, intent, scope, citations
)
SELECT q.id, '这门课的作业要求是什么？', '现有资料写明了提交时间。', false, 'query',
  jsonb_build_object('workspace', :'ws', 'semester', :'sem', 'course', :'course'),
  jsonb_build_array(jsonb_build_object('chunk_id', ch.id, 'quote', ch.text))
FROM qa_conversations q
JOIN workspaces w ON w.id = q.workspace_id
JOIN materials m ON m.workspace_id = w.id AND m.title = :'material_title'
JOIN material_versions v ON v.material_id = m.id AND v.version_no = 1
JOIN source_chunks ch ON ch.version_id = v.id
WHERE w.name = :'ws'
LIMIT 1;

COMMIT;
