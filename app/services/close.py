import sqlite3

from app.db import write_db
from app.services.adjudication import (
    DECISION_ROLLBACK,
    record_adjudication,
)
from app.services.errors import AdjudicationError, ServiceError


def close_batch(
    batch_id: int,
    expected_status: str | None = None,
    db_path: str | None = None,
) -> dict:
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
            adjudication = record_adjudication(conn, batch, "闭合", page_status)
            if adjudication["decision"] == DECISION_ROLLBACK:
                pending = AdjudicationError(
                    f"页面显示「{page_status}」，但库里这批已是「{db_status}」："
                    f"本次闭合已整笔回滚（裁决 #{adjudication['id']}），请刷新后核对库态。",
                    409,
                    adjudication=adjudication,
                    db_status=db_status,
                )
            # 强制刷：库态已原子复核钉死，临界闭合按刷新后库态重估
        if pending is None:
            if db_status == "已闭合":
                raise ServiceError("批次已闭合，请勿重复闭合", 409)
            if db_status != "收集中":
                raise ServiceError(f"批次当前为「{db_status}」，仅收集中批次可闭合", 409)
            objs = conn.execute(
                "SELECT id FROM landing_objects WHERE batch_id=? AND status='已登记'",
                (batch_id,),
            ).fetchall()
            if not objs:
                raise ServiceError("空批不可闭合：请先登记对象", 400)
            n = len(objs)
            # 原子比较并交换：仅当库态仍是收集中才翻牌，丢掉竞态的那一格直接判负
            cur = conn.execute(
                "UPDATE landing_batches SET status='已闭合', closed_object_count=? "
                "WHERE id=? AND status='收集中'",
                (n, batch_id),
            )
            if cur.rowcount != 1:
                raise ServiceError("闭合未生效：批次状态刚被其他席位变更，请刷新后重试", 409)
            conn.execute(
                "UPDATE landing_objects SET status='已闭合' WHERE batch_id=? AND status='已登记'",
                (batch_id,),
            )
            try:
                conn.execute(
                    "INSERT INTO close_runs(batch_id, object_count, ok, detail) VALUES (?,?,1,?)",
                    (batch_id, n, f"闭合 {n} 个对象"),
                )
            except sqlite3.IntegrityError:
                raise ServiceError("库里已存在该批闭合流水，已拒绝重复写入", 409)
            run = conn.execute(
                "SELECT * FROM close_runs WHERE batch_id=?", (batch_id,)
            ).fetchone()
    if pending is not None:
        raise pending
    return {"run": dict(run), "adjudication": adjudication}
