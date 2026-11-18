"""页库对齐裁决引擎。

页态（页面以为的批次状态）与库态（库内权威状态）漂移时，裁决只钉死一条：
  - 回滚：库态已是终态（已闭合/已作废），整笔回滚页面乐观态，写回滚流水；
  - 强制刷：库态仍可流转，原子复核钉死库态，落强制刷凭证，临界操作按刷新后库态重估。
回滚流水与强制刷凭证互斥落地：一次裁决只进其中一张表。
每次裁决另存一份页库复现单（drift_repros），供回看侧对照同一裁决编号。
"""

import json

from app.db import get_db

DECISION_ROLLBACK = "回滚"
DECISION_FORCE_REFRESH = "强制刷"
TERMINAL_STATUS = ("已闭合", "已作废")


def decide(page_status: str, db_status: str) -> str:
    """确定性裁决规则：库态为终态则回滚页面乐观态，否则强制刷库态。"""
    if db_status in TERMINAL_STATUS:
        return DECISION_ROLLBACK
    return DECISION_FORCE_REFRESH


def record_adjudication(conn, batch, operation: str, page_status: str) -> dict:
    """在调用方事务内落一次裁决：裁决行 + 互斥子表（回滚流水|强制刷凭证）+ 页库复现单。"""
    batch_id = batch["id"]
    db_status = batch["status"]
    decision = decide(page_status, db_status)
    detail = (
        f"页态「{page_status}」与库态「{db_status}」漂移，"
        f"{operation}操作裁决为{decision}"
    )
    cur = conn.execute(
        "INSERT INTO adjudications(batch_id, operation, page_status, db_status, decision, detail) "
        "VALUES (?,?,?,?,?,?)",
        (batch_id, operation, page_status, db_status, decision, detail),
    )
    adj_id = cur.lastrowid
    if decision == DECISION_ROLLBACK:
        conn.execute(
            "INSERT INTO rollback_ledger(adjudication_id, batch_id, operation, detail) "
            "VALUES (?,?,?,?)",
            (adj_id, batch_id, operation, f"整笔回滚页面乐观态：本次{operation}未写入库"),
        )
        landed = "rollback_ledger"
    else:
        conn.execute(
            "INSERT INTO force_refresh_vouchers("
            "adjudication_id, batch_id, operation, refreshed_status, detail) "
            "VALUES (?,?,?,?,?)",
            (
                adj_id,
                batch_id,
                operation,
                db_status,
                f"原子复核钉死库态为「{db_status}」，临界{operation}按刷新后库态重估",
            ),
        )
        landed = "force_refresh_vouchers"
    snapshot = json.dumps(
        {
            "batch": dict(batch),
            "object_count": conn.execute(
                "SELECT COUNT(*) AS c FROM landing_objects WHERE batch_id=?",
                (batch_id,),
            ).fetchone()["c"],
            "page_status": page_status,
            "db_status": db_status,
            "operation": operation,
            "decision": decision,
        },
        ensure_ascii=False,
    )
    conn.execute(
        "INSERT INTO drift_repros(adjudication_id, batch_id, operation, page_status, db_status, snapshot) "
        "VALUES (?,?,?,?,?,?)",
        (adj_id, batch_id, operation, page_status, db_status, snapshot),
    )
    return {
        "id": adj_id,
        "batch_id": batch_id,
        "operation": operation,
        "page_status": page_status,
        "db_status": db_status,
        "decision": decision,
        "landed": landed,
        "detail": detail,
    }


def list_adjudications(db_path: str | None = None) -> list[dict]:
    """清洗回放台：裁决与互斥落地、复现单的对照清单。"""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT a.*, r.id AS rollback_id, f.id AS voucher_id, p.id AS repro_id "
            "FROM adjudications a "
            "LEFT JOIN rollback_ledger r ON r.adjudication_id = a.id "
            "LEFT JOIN force_refresh_vouchers f ON f.adjudication_id = a.id "
            "LEFT JOIN drift_repros p ON p.adjudication_id = a.id "
            "ORDER BY a.id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
