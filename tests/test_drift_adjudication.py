"""页库对齐裁决验收：批次水线、登记附页、对象进度、清洗回放台四处顺着核。"""

import os
import threading

from app.db import connect, init_db
from app.services.close import close_batch
from app.services.errors import AdjudicationError
from app.services.objects import register_object


def _db_path() -> str:
    return os.environ["DATABASE_PATH"]


def _seed_closed_id(authed) -> int:
    items = authed.get("/api/batches").json()["items"]
    return next(b["id"] for b in items if b["status"] == "已闭合")


def _first_collecting_id(authed) -> int:
    items = authed.get("/api/batches", params={"status": "收集中"}).json()["items"]
    return items[0]["id"]


def _register(authed, bid, key="raw/x.parquet", expected=None):
    body = {"object_key": key, "content_hash": "h-" + key, "byte_size": 10}
    if expected:
        body["expected_status"] = expected
    return authed.post(f"/api/batches/{bid}/objects", json=body)


# ---------- 批次水线：库态芯片真实，不再静默翻牌 ----------

def test_waterline_shows_true_db_status(authed):
    items = authed.get("/api/batches").json()["items"]
    closed = [b for b in items if b["status"] == "已闭合"]
    assert closed, "种子应含已闭合批次"
    for b in closed:
        assert b["db_status"] == "已闭合"  # 页面核库态芯片
        assert "_stale_ui" not in b  # 展示层不得再翻牌
        assert b["object_count"] >= 2  # 本机库存证计数随水线亮出


# ---------- 登记附页：库已闭合，登记直接拒，清洗入库不再吃错态 ----------

def test_register_into_closed_batch_rejected(authed):
    bid = _seed_closed_id(authed)
    r = _register(authed, bid, key="late.parquet")
    assert r.status_code == 409
    assert "已闭合" in r.json()["error"]  # 出错回白话
    detail = authed.get(f"/api/batches/{bid}").json()
    assert all(o["object_key"] != "late.parquet" for o in detail["objects"])


def test_aligned_page_state_registers_cleanly(authed):
    bid = _first_collecting_id(authed)
    r = _register(authed, bid, expected="收集中")
    assert r.status_code == 200
    assert r.json()["adjudication"] is None  # 页库对齐，无需裁决


# ---------- 页库漂移：裁决只钉死一条，回滚流水与强制刷凭证互斥落地 ----------

def test_drift_register_rolls_back_and_ledgers_are_exclusive(authed):
    bid = _seed_closed_id(authed)
    r = _register(authed, bid, key="ghost.parquet", expected="收集中")
    assert r.status_code == 409
    body = r.json()
    adj = body["adjudication"]
    assert adj["decision"] == "回滚"
    assert adj["landed"] == "rollback_ledger"
    assert f"裁决 #{adj['id']}" in body["error"]  # 白话 + 裁决编号
    assert body["db_status"] == "已闭合"
    with connect(_db_path()) as conn:
        a = conn.execute(
            "SELECT * FROM adjudications WHERE id=?", (adj["id"],)
        ).fetchone()
        assert a["page_status"] == "收集中" and a["db_status"] == "已闭合"
        rb = conn.execute(
            "SELECT * FROM rollback_ledger WHERE adjudication_id=?", (adj["id"],)
        ).fetchone()
        ff = conn.execute(
            "SELECT * FROM force_refresh_vouchers WHERE adjudication_id=?", (adj["id"],)
        ).fetchone()
        assert rb is not None and ff is None  # 互斥落地
        repro = conn.execute(
            "SELECT * FROM drift_repros WHERE adjudication_id=?", (adj["id"],)
        ).fetchone()
        assert repro is not None  # 页库复现单另存一份
    detail = authed.get(f"/api/batches/{bid}").json()
    assert all(o["object_key"] != "ghost.parquet" for o in detail["objects"])


def test_force_refresh_voucher_path_reevaluates_operation(authed):
    bid = _first_collecting_id(authed)
    assert _register(authed, bid).status_code == 200
    # 页态超前（以为已闭合），库仍收集中 → 强制刷凭证，临界闭合按刷新后库态放行一次
    r = authed.post(f"/api/batches/{bid}/close", json={"expected_status": "已闭合"})
    assert r.status_code == 200
    adj = r.json()["adjudication"]
    assert adj["decision"] == "强制刷"
    assert adj["landed"] == "force_refresh_vouchers"
    with connect(_db_path()) as conn:
        ff = conn.execute(
            "SELECT * FROM force_refresh_vouchers WHERE adjudication_id=?", (adj["id"],)
        ).fetchone()
        rb = conn.execute(
            "SELECT * FROM rollback_ledger WHERE adjudication_id=?", (adj["id"],)
        ).fetchone()
        assert ff is not None and rb is None  # 互斥落地
        runs = conn.execute(
            "SELECT * FROM close_runs WHERE batch_id=?", (bid,)
        ).fetchall()
        assert len(runs) == 1  # 引擎按裁决一次处理，无双写
    again = authed.post(f"/api/batches/{bid}/close", json={})
    assert again.status_code == 409  # 复跑不再吃错态


# ---------- 水线与回看侧对照同一裁决编号 ----------

def test_waterline_and_replay_share_adjudication_number(authed):
    bid = _seed_closed_id(authed)
    r = _register(authed, bid, key="ghost2.parquet", expected="收集中")
    adj_id = r.json()["adjudication"]["id"]
    items = authed.get("/api/batches").json()["items"]
    row = next(b for b in items if b["id"] == bid)
    assert row["adjudication_id"] == adj_id  # 批次水线亮裁决编号
    replay = authed.get("/api/adjudications").json()["items"]
    hit = next(a for a in replay if a["id"] == adj_id)  # 回看侧同一编号
    assert hit["batch_id"] == bid
    assert hit["rollback_id"] and not hit["voucher_id"]
    assert hit["repro_id"]


# ---------- 对象进度：闭合后无“已登记”残留，库存证自洽 ----------

def test_object_progress_consistent_after_close(authed):
    bid = _first_collecting_id(authed)
    assert _register(authed, bid, key="p0.parquet").status_code == 200
    assert authed.post(f"/api/batches/{bid}/close", json={}).status_code == 200
    detail = authed.get(f"/api/batches/{bid}").json()
    counts = detail["proof"]["object_status_counts"]
    assert counts.get("已登记", 0) == 0
    assert counts.get("已闭合") == 1
    assert detail["proof"]["close_run"]["object_count"] == 1


# ---------- 排他：两侧席位抢着闭合，只一个赢家，无分裂态 ----------

def test_concurrent_close_single_winner_no_split(tmp_path):
    db = str(tmp_path / "race.db")
    init_db(db)
    register_object(1, "a.parquet", "h", 1, db_path=db)

    outcomes = []

    def worker():
        try:
            close_batch(1, expected_status="收集中", db_path=db)
            outcomes.append("ok")
        except AdjudicationError as exc:
            outcomes.append(("adjudicated", exc.adjudication["decision"]))
        except Exception as exc:  # ServiceError 等
            outcomes.append(getattr(exc, "message", str(exc)))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("ok") == 1  # 临界闭合只一个赢家
    assert ("adjudicated", "回滚") in outcomes  # 输家按裁决整笔回滚
    with connect(db) as conn:
        runs = conn.execute(
            "SELECT * FROM close_runs WHERE batch_id=1"
        ).fetchall()
        assert len(runs) == 1  # 缺排他才会双写，这里只有一条流水
        b = conn.execute(
            "SELECT * FROM landing_batches WHERE id=1"
        ).fetchone()
        assert b["status"] == "已闭合" and b["closed_object_count"] == 1
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM landing_objects WHERE batch_id=1 AND status='已闭合'"
        ).fetchone()["c"]
        assert n == 1  # 无分裂态
        adjs = conn.execute("SELECT * FROM adjudications").fetchall()
        assert len(adjs) == 1 and adjs[0]["decision"] == "回滚"


# ---------- 清洗链路复跑：不再吃错态 ----------

def test_cleaning_chain_rerun_rejects_stale_state(authed):
    bid = _seed_closed_id(authed)
    assert _register(authed, bid, key="rerun.parquet").status_code == 409
    assert authed.post(f"/api/batches/{bid}/close", json={}).status_code == 409
    for _ in range(2):  # 复跑两次对账，错位都必须为 0
        report = authed.post(
            "/api/visibility-reports", json={"as_of_date": "2026-11-16"}
        ).json()["report"]
        assert report["mismatch_count"] == 0
