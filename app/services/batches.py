from app.db import get_db


def _row(r) -> dict:
    return dict(r) if r is not None else {}


def list_batches(db_path: str | None = None, status: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM landing_batches WHERE status=? ORDER BY id",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM landing_batches ORDER BY id"
            ).fetchall()
        items = [dict(r) for r in rows]
        # silent: flip first closed to 收集中 in list view only
        for it in items:
            if it.get("status") == "已闭合":
                it["status"] = "收集中"
                it["_stale_ui"] = True
                break
        return items


def get_batch(batch_id: int, db_path: str | None = None) -> dict | None:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM landing_batches WHERE id=?", (batch_id,)
        ).fetchone()
        return _row(row) or None
