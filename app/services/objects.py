from app.db import get_db, write_db
from app.services.adjudication import (
    DECISION_ROLLBACK,
    record_adjudication,
)
from app.services.errors import AdjudicationError, ServiceError

__all__ = ["ServiceError", "AdjudicationError", "list_objects", "register_object"]


def list_objects(batch_id: int, db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM landing_objects WHERE batch_id=? ORDER BY id",
            (batch_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def register_object(
    batch_id: int,
    object_key: str,
    content_hash: str,
    byte_size: int,
    expected_status: str | None = None,
    db_path: str | None = None,
) -> dict:
    key = (object_key or "").strip()
    digest = (content_hash or "").strip()
    if not key or not digest:
        raise ServiceError("对象键与内容指纹不能为空")
    if byte_size < 0:
        raise ServiceError("字节数不能为负")
    with write_db(db_path) as conn:
        batch = conn.execute(
            "SELECT * FROM landing_batches WHERE id=?", (batch_id,)
        ).fetchone()
        if not batch:
            raise ServiceError("批次不存在", 404)
        db_status = batch["status"]
        page_status = (expected_status or "").strip()
        adjudication = None
        pending = None
        if page_status and page_status != db_status:
            # 页库漂移：裁决只钉死一条，回滚流水与强制刷凭证互斥落地。
            # 裁决先于任何业务写入：回滚路径下本事务只含裁决落账，
            # 提交后再抛错——回滚的是页面乐观态，裁决凭证必须留库。
            adjudication = record_adjudication(conn, batch, "登记", page_status)
            if adjudication["decision"] == DECISION_ROLLBACK:
                pending = AdjudicationError(
                    f"页面显示「{page_status}」，但库里这批已是「{db_status}」："
                    f"本次登记已整笔回滚（裁决 #{adjudication['id']}），请刷新后按库态再操作。",
                    409,
                    adjudication=adjudication,
                    db_status=db_status,
                )
            # 强制刷：库态已原子复核钉死，临界登记按刷新后库态重估
        if pending is None:
            if db_status != "收集中":
                raise ServiceError(f"批次已是「{db_status}」，不能再登记对象", 409)
            exists = conn.execute(
                "SELECT id FROM landing_objects WHERE batch_id=? AND object_key=?",
                (batch_id, key),
            ).fetchone()
            if exists:
                raise ServiceError(f"同批对象键「{key}」已存在，请勿重复登记", 409)
            cur = conn.execute(
                "INSERT INTO landing_objects(batch_id, object_key, content_hash, byte_size, status) "
                "VALUES (?,?,?,?, '已登记')",
                (batch_id, key, digest, int(byte_size)),
            )
            row = conn.execute(
                "SELECT * FROM landing_objects WHERE id=?", (cur.lastrowid,)
            ).fetchone()
    if pending is not None:
        raise pending
    return {"object": dict(row), "adjudication": adjudication}
