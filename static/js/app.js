(() => {
  const view = document.getElementById("view");
  const msgEl = document.getElementById("msg");
  let tab = "batches";
  let meta = null;
  let batches = [];
  let selected = null;
  let detail = null;
  let reports = [];
  let adjudications = [];
  let inFlight = false;
  let form = { object_key: "", content_hash: "", byte_size: "100" };

  async function api(path, opts = {}) {
    const r = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    if (r.status === 401) {
      location.href = "/login";
      throw new Error("未登录");
    }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      const err = new Error(j.error || j.detail || ("HTTP " + r.status));
      err.payload = j;
      throw err;
    }
    return j;
  }

  function setMsg(t) {
    msgEl.textContent = t || "";
  }

  async function refresh() {
    meta = await api("/api/meta");
    document.getElementById("lab-brand").textContent = meta.lab_name || "湖区清单台";
    batches = (await api("/api/batches")).items || [];
    reports = (await api("/api/visibility-reports")).items || [];
    adjudications = (await api("/api/adjudications")).items || [];
  }

  async function openBatch(id) {
    selected = id;
    detail = await api("/api/batches/" + id);
    tab = "detail";
    syncNav();
    render();
  }

  // 页态对齐库态：操作失败后重新拉库内权威状态，漂了的页面状态直接丢弃
  async function resyncFromDb() {
    await refresh();
    if (selected) {
      try {
        detail = await api("/api/batches/" + selected);
      } catch (_) {
        /* 批次可能已不可见，保持现状 */
      }
    }
    render();
  }

  function adjNote(adj) {
    if (!adj) return "";
    const landed = adj.landed === "rollback_ledger" ? "回滚流水" : "强制刷凭证";
    return `裁决 #${adj.id}（${adj.decision}，已落${landed}）`;
  }

  async function doRegister() {
    if (!selected || !detail || inFlight) return;
    inFlight = true;
    setMsg("");
    try {
      const res = await api("/api/batches/" + selected + "/objects", {
        method: "POST",
        body: JSON.stringify({
          object_key: form.object_key,
          content_hash: form.content_hash,
          byte_size: Number(form.byte_size || 0),
          expected_status: detail.batch.status,
        }),
      });
      form = { object_key: "", content_hash: "", byte_size: "100" };
      await resyncFromDb();
      setMsg(
        res.adjudication
          ? `已登记对象；期间页库漂移，${adjNote(res.adjudication)}，本次按刷新后库态处理。`
          : "已登记对象"
      );
      render();
    } catch (e) {
      await resyncFromDb();
      setMsg(e.message);
    } finally {
      inFlight = false;
    }
  }

  async function doClose() {
    if (!selected || !detail || inFlight) return;
    inFlight = true;
    setMsg("");
    try {
      const res = await api("/api/batches/" + selected + "/close", {
        method: "POST",
        body: JSON.stringify({ expected_status: detail.batch.status }),
      });
      await resyncFromDb();
      setMsg(
        res.adjudication
          ? `清单已闭合；期间页库漂移，${adjNote(res.adjudication)}，本次按刷新后库态处理。`
          : "清单已闭合"
      );
      render();
    } catch (e) {
      await resyncFromDb();
      setMsg(e.message);
    } finally {
      inFlight = false;
    }
  }

  async function doReport() {
    if (inFlight) return;
    inFlight = true;
    setMsg("");
    try {
      const r = await api("/api/visibility-reports", {
        method: "POST",
        body: JSON.stringify({ as_of_date: "2026-11-16" }),
      });
      await refresh();
      tab = "reports";
      syncNav();
      setMsg(r.report.summary);
      render();
    } catch (e) {
      setMsg(e.message);
    } finally {
      inFlight = false;
    }
  }

  function syncNav() {
    document.querySelectorAll("nav [data-tab]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tab);
    });
  }

  function adjChip(b) {
    return b.adjudication_id ? `<span class="chip warn">裁决 #${b.adjudication_id}</span>` : "";
  }

  function renderBatches() {
    return `
      <h2>落地批次</h2>
      <p class="muted">主链：选批 → 登记对象 → 闭合清单 → 可见性对账；水线亮库态芯片与裁决编号</p>
      <table>
        <thead><tr><th>编号</th><th>湖区</th><th>业务日</th><th>状态</th><th>库态芯片</th><th>库存</th><th></th></tr></thead>
        <tbody>
          ${batches
            .map(
              (b) => `<tr>
            <td>${b.id}</td>
            <td>${b.zone_name}<div class="muted">${b.zone_code}</div></td>
            <td>${b.biz_date}</td>
            <td><span class="pill">${b.status}</span></td>
            <td><span class="chip">库态·${b.db_status}</span>${adjChip(b)}</td>
            <td class="muted">${b.object_count} 件</td>
            <td><button type="button" class="secondary" data-open="${b.id}">打开</button></td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function renderDetail() {
    if (!detail) {
      return `<h2>批次详情</h2><p class="muted">请先从落地批次打开一条。</p>`;
    }
    const b = detail.batch;
    const objs = detail.objects || [];
    const proof = detail.proof || {};
    const counts = proof.object_status_counts || {};
    const collecting = b.status === "收集中";
    const proofLine = `库存证：已登记 ${counts["已登记"] || 0} · 已闭合 ${counts["已闭合"] || 0} · 已作废 ${counts["已作废"] || 0}`;
    const closeRun = proof.close_run
      ? ` · 闭合流水 #${proof.close_run.id}（${proof.close_run.object_count} 件）`
      : "";
    const registerPanel = collecting
      ? `<div class="panel">
          <h3>登记对象</h3>
          <label>对象键<input id="f-key" value="${form.object_key}" /></label>
          <label>内容指纹<input id="f-hash" value="${form.content_hash}" /></label>
          <label>字节数<input id="f-size" value="${form.byte_size}" /></label>
          <button type="button" id="btn-register">写入登记</button>
        </div>`
      : `<div class="panel">
          <h3>登记对象</h3>
          <p class="muted">库态已是「${b.status}」，登记已锁定；页面核库态芯片一致，不再放写。</p>
        </div>`;
    return `
      <h2>批次详情 #${b.id}</h2>
      <p>${b.zone_name} · ${b.biz_date} · <span class="pill">${b.status}</span>
        <span class="chip">库态·${b.db_status}</span>${adjChip(b)}</p>
      <p class="muted">${proofLine}${closeRun}</p>
      <div class="row-actions">
        <button type="button" id="btn-close" ${collecting ? "" : "disabled"}>闭合清单</button>
        <button type="button" class="secondary" id="btn-report-from-detail">跑可见性对账</button>
      </div>
      ${registerPanel}
      <h3>对象进度</h3>
      <table>
        <thead><tr><th>键</th><th>指纹</th><th>字节</th><th>状态</th></tr></thead>
        <tbody>
          ${objs
            .map(
              (o) => `<tr>
            <td>${o.object_key}</td><td>${o.content_hash}</td><td>${o.byte_size}</td><td>${o.status}</td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function renderReports() {
    return `
      <h2>可见性对账 · 清洗回放台</h2>
      <button type="button" id="btn-report">生成对账</button>
      <table>
        <thead><tr><th>编号</th><th>截至</th><th>错位</th><th>摘要</th></tr></thead>
        <tbody>
          ${reports
            .map(
              (r) => `<tr>
            <td>${r.id}</td><td>${r.as_of_date}</td><td>${r.mismatch_count}</td><td>${r.summary}</td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>
      <h3>裁决回放</h3>
      <p class="muted">与水线裁决编号同一编号；回滚流水与强制刷凭证互斥落地，复现单另存。</p>
      <table>
        <thead><tr><th>裁决编号</th><th>批次</th><th>操作</th><th>页态</th><th>库态</th><th>决策</th><th>落地</th><th>复现单</th><th>时间</th></tr></thead>
        <tbody>
          ${adjudications
            .map(
              (a) => `<tr>
            <td>#${a.id}</td><td>${a.batch_id}</td><td>${a.operation}</td>
            <td>${a.page_status}</td><td>${a.db_status}</td><td>${a.decision}</td>
            <td>${a.rollback_id ? `回滚流水 #${a.rollback_id}` : `强制刷凭证 #${a.voucher_id}`}</td>
            <td>${a.repro_id ? `复现单 #${a.repro_id}` : "—"}</td>
            <td class="muted">${a.created_at}</td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function render() {
    if (tab === "batches") view.innerHTML = renderBatches();
    else if (tab === "detail") view.innerHTML = renderDetail();
    else view.innerHTML = renderReports();

    view.querySelectorAll("[data-open]").forEach((btn) => {
      btn.onclick = () => openBatch(Number(btn.dataset.open));
    });
    const reg = document.getElementById("btn-register");
    if (reg) {
      const key = document.getElementById("f-key");
      const hash = document.getElementById("f-hash");
      const size = document.getElementById("f-size");
      key.oninput = () => (form.object_key = key.value);
      hash.oninput = () => (form.content_hash = hash.value);
      size.oninput = () => (form.byte_size = size.value);
      reg.onclick = doRegister;
    }
    const closeBtn = document.getElementById("btn-close");
    if (closeBtn) closeBtn.onclick = doClose;
    const r1 = document.getElementById("btn-report");
    const r2 = document.getElementById("btn-report-from-detail");
    if (r1) r1.onclick = doReport;
    if (r2) r2.onclick = doReport;
  }

  document.querySelectorAll("nav [data-tab]").forEach((btn) => {
    btn.onclick = () => {
      tab = btn.dataset.tab;
      syncNav();
      render();
    };
  });
  document.getElementById("btn-logout").onclick = () => {
    fetch("/api/logout", { method: "POST" }).then(() => (location.href = "/login"));
  };

  refresh()
    .then(render)
    .catch((e) => setMsg(e.message));
})();
