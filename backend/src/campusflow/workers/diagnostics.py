"""只读运维入口：不回收作业、不重投消息，不输出连接口令和资料文本。"""

import argparse
import json
import os
from pathlib import Path

import redis
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from rq import Queue
from sqlalchemy import text
from sqlalchemy.orm import Session

from campusflow.core.config import Settings
from campusflow.infrastructure.db.engine import create_db_engine
from campusflow.infrastructure.db.migrations import build_alembic_config
from campusflow.infrastructure.queue.rq_queue import QUEUE_NAME
from campusflow.infrastructure.repositories import build_repositories


def collect_diagnostics(settings: Settings, *, job_id: int | None = None) -> dict:
    """汇总数据库迁移头、队列连通、存储可写与可选作业状态。

    全程只读：不领取/回收作业、不重投消息、不调用模型，也不输出连接串、
    资料文本或异常原文；任何检查失败都折算成受控错误码。
    """
    report = {"database": {"ok": False}, "redis": {"ok": False}}
    engine = create_db_engine(settings)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current = set(MigrationContext.configure(connection).get_current_heads())
            config = build_alembic_config(settings.migrations_dir)
            expected = set(ScriptDirectory.from_config(config).get_heads())
            # 只有数据库迁移头与代码期望完全一致才算就绪，落后或领先都不冒充可用。
            report["database"] = {
                "ok": current == expected,
                "schema_current": current == expected,
            }
            if job_id is not None:
                with Session(bind=connection) as session:
                    job = build_repositories(session).jobs.get(job_id)
                    # 只回显作业编号与状态字段，不返回负载、错误详情或资料内容。
                    report["job"] = {"id": job_id, "found": job is not None}
                    if job is not None:
                        report["job"].update(
                            stage=job.stage,
                            status=job.status,
                            attempts=job.attempts,
                            retryable=job.retryable,
                            delivered=job.enqueued_at is not None,
                        )
    except Exception:
        report["database"] = {"ok": False, "code": "database_or_schema_unavailable"}
    finally:
        engine.dispose()

    connection = None
    try:
        connection = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=3, socket_timeout=3
        )
        connection.ping()
        queue = Queue(QUEUE_NAME, connection=connection)
        report["redis"] = {"ok": True, "queued": queue.count}
    except Exception:
        report["redis"] = {"ok": False, "code": "redis_unavailable"}
    finally:
        if connection is not None:
            connection.close()

    storage = Path(settings.storage_dir)
    # 只检查目录可读写，不创建目录、不写入探针文件，避免诊断改变运行状态。
    report["storage"] = {
        "ok": storage.is_dir() and os.access(storage, os.R_OK | os.W_OK),
    }
    report["vision"] = {"configured": bool(settings.zhipu_api_key)}
    # 密钥存在不代表账号可用；诊断从不发送资料、不消费模型调用额度。
    report["ready"] = all(report[key]["ok"] for key in ("database", "redis", "storage"))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="只读检查数据库、队列、存储和指定作业")
    parser.add_argument("--job-id", type=int, help="可选：仅输出此作业的状态字段")
    args = parser.parse_args()
    if args.job_id is not None and args.job_id <= 0:
        parser.error("job-id 必须是正整数")
    try:
        report = collect_diagnostics(Settings(), job_id=args.job_id)
    except Exception:
        # 配置解析失败时不回显 Settings 内容，防止把密钥带进终端或日志。
        report = {"ready": False, "code": "configuration_invalid"}
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["ready"] else 1)


if __name__ == "__main__":
    main()
