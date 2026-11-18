from app.db import get_db


def _row(r) -> dict:
    return dict(r) if r is not None else {}


# 批次水线：库态芯片（db_status 与库内一致，不做任何展示层翻牌）、
# 实时库存（本机库存证）、最近裁决编号（与回看侧对照同一编号）。
_LIST_SELECT = (
    "SELECT b.*, b.status AS db_status, "
    "(SELECT COUNT(*) FROM landing_objects o WHERE o.batch_id = b.id) AS object_count, "
    "(SELECT MAX(a.id) FROM adjudications a WHERE a.batch_id = b.id) AS adjudication_id "
    "FROM landing_batches b"
)


def list_batches(db_path: str | None = None, status: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        if status:
            rows = conn.execute(
                _LIST_SELECT + " WHERE b.status=? ORDER BY b.id",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(_LIST_SELECT + " ORDER BY b.id").fetchall()
        return [dict(r) for r in rows]


def get_batch(batch_id: int, db_path: str | None = None) -> dict | None:
    with get_db(db_path) as conn:
        row = conn.execute(
            _LIST_SELECT + " WHERE b.id=?", (batch_id,)
        ).fetchone()
        return _row(row) or None


def inventory_proof(batch_id: int, db_path: str | None = None) -> dict:
    """本机库存证：对象分态计数 + 闭合流水 + 裁决记录，供页面核库态芯片。"""
    with get_db(db_path) as conn:
        counts = {
            r["status"]: r["c"]
            for r in conn.execute(
                "SELECT status, COUNT(*) AS c FROM landing_objects "
                "WHERE batch_id=? GROUP BY status",
                (batch_id,),
            ).fetchall()
        }
        run = conn.execute(
            "SELECT * FROM close_runs WHERE batch_id=?", (batch_id,)
        ).fetchone()
        adjudications = conn.execute(
            "SELECT id, operation, page_status, db_status, decision, created_at "
            "FROM adjudications WHERE batch_id=? ORDER BY id DESC",
            (batch_id,),
        ).fetchall()
        return {
            "object_status_counts": counts,
            "close_run": dict(run) if run else None,
            "adjudications": [dict(a) for a in adjudications],
        }
