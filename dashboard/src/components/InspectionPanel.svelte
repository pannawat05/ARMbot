<script>
  // From the receiving dashboard: inspection results (what was asked for, what it found, the
  // picture) and Unknown review requests, which we answer here as the Unknown review team.
  let { results = [], onreview } = $props();
  let open = $state(null); // item whose picture is shown large

  const isReview = (r) => r.kind === 'review';
  const pending = $derived(results.filter((r) => isReview(r) && !r.decision));
  const done = $derived(results.filter((r) => !(isReview(r) && !r.decision)));
  const latest = $derived(done[0]);
  const older = $derived(done.slice(1, 7));
  const names = (r) => [...new Set([...Object.keys(r.requested ?? {}), ...Object.keys(r.detected ?? {})])];
  const matches = (r) => names(r).every((n) => (r.requested?.[n] ?? 0) === (r.detected?.[n] ?? 0));
  const ok = (r) => (isReview(r) ? r.decision === 'approved' : r.status === 'completed' && matches(r));
  const label = (r) =>
    isReview(r)
      ? r.decision === 'approved' ? 'Unknown ถูกต้อง' : 'Unknown ไม่ถูกต้อง'
      : r.status === 'not_found' ? 'ไม่พบของ' : r.status === 'incomplete' ? 'ไม่ครบ' : matches(r) ? 'ครบ' : 'ไม่ตรง';
  const when = (r) => new Date((r.received_at ?? 0) * 1000).toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' });
</script>

{#if pending.length}
  <div class="head"><h2>รอทีมตรวจ Unknown</h2></div>
  {#each pending as r (r.key)}
    <article class="review">
      <button class="pic" onclick={() => (open = r)} aria-label="ดูรูปจากกล้องฝั่งรับของขนาดใหญ่">
        <img src={r.image} alt="รูปของ Unknown จากฝั่งรับของ {r.job_id}" />
      </button>
      <div class="body">
        <small>{r.job_id} · {when(r)}</small>
        <ul>
          {#each Object.entries(r.requested ?? {}) as [n, c] (n)}<li>{n}: {c} ชิ้น</li>{/each}
        </ul>
        <div class="actions">
          <button class="yes" onclick={() => onreview?.(r.key, 'approved')}>ถูกต้อง</button>
          <button class="no" onclick={() => onreview?.(r.key, 'rejected')}>ไม่ถูกต้อง</button>
        </div>
      </div>
    </article>
  {/each}
{/if}

<div class="head"><h2>ผลตรวจรับ</h2></div>

{#if latest}
  <article class:bad={!ok(latest)}>
    <button class="pic" onclick={() => latest.image && (open = latest)} aria-label="ดูรูปผลตรวจขนาดใหญ่">
      {#if latest.image}<img src={latest.image} alt="รูปยืนยันผลตรวจ {latest.job_id}" />{:else}<span class="none">ไม่มีรูป</span>{/if}
    </button>
    <div class="body">
      <span class="chip">{label(latest)}</span>
      <small>{latest.job_id} · {when(latest)}{latest.reason === 'camera_timeout' ? ' · กล้องหมดเวลา' : ''}</small>
      <ul>
        {#if isReview(latest)}
          {#each Object.entries(latest.requested ?? {}) as [n, c] (n)}<li>{n}: {c} ชิ้น</li>{/each}
        {:else}
          {#each names(latest) as n (n)}
            <li>{n}: ตรวจพบ {latest.detected?.[n] ?? 0} จาก {latest.requested?.[n] ?? 0}</li>
          {/each}
        {/if}
      </ul>
    </div>
  </article>
  {#if older.length}
    <div class="older">
      {#each older as r (r.key)}
        <button class:bad={!ok(r)} onclick={() => r.image && (open = r)} aria-label="{label(r)} {r.job_id}" title="{label(r)} · {r.job_id}">
          {#if r.image}<img src={r.image} alt="รูปผลตรวจ {r.job_id}" loading="lazy" />{:else}<span class="none">{label(r)}</span>{/if}
        </button>
      {/each}
    </div>
  {/if}
{:else}
  <p class="empty">ยังไม่มีผลตรวจ</p>
{/if}

{#if open}
  <div class="shade" role="presentation" onclick={() => (open = null)} onkeydown={(e) => e.key === 'Escape' && (open = null)}>
    <img src={open.image} alt="รูปจากฝั่งรับของ {open.job_id}" />
  </div>
{/if}

<style>
  .head { margin-top: 4px; }
  h2 { margin: 0; font-size: 1.05rem; }
  article { display: grid; grid-template-columns: 120px 1fr; gap: 10px; margin-top: 8px; padding: 8px; border-radius: 10px; background: var(--panel); border: 1px solid var(--line); }
  article.bad { border-color: var(--bad); }
  article.review { border-color: var(--accent); }
  .pic { padding: 0; border: 0; background: none; line-height: 0; min-height: 80px; }
  .pic img { width: 100%; height: 100%; max-height: 120px; object-fit: cover; border-radius: 8px; }
  .none { display: grid; place-items: center; width: 100%; height: 100%; min-height: 56px; border-radius: 8px; background: var(--muted-bg); color: var(--muted); font-size: 0.75rem; line-height: 1.2; text-align: center; }
  .body { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
  .body small { color: var(--muted); overflow-wrap: anywhere; }
  .chip { align-self: flex-start; font-size: 0.75rem; padding: 2px 8px; border-radius: 999px; background: var(--ok); color: #04210f; }
  .bad .chip { background: var(--bad); color: #fff; }
  ul { list-style: none; margin: 0; padding: 0; font-size: 0.9rem; }
  .actions { display: flex; gap: 8px; margin-top: 4px; }
  .actions button { flex: 1; padding: 8px; font-weight: 700; border: 0; }
  .yes { background: var(--ok); color: #04210f; }
  .no { background: var(--bad); color: #fff; }
  .older { display: flex; gap: 6px; margin-top: 6px; overflow-x: auto; }
  .older button { flex: 0 0 56px; height: 56px; padding: 0; border: 2px solid var(--line); border-radius: 8px; overflow: hidden; line-height: 0; background: none; }
  .older button.bad { border-color: var(--bad); }
  .older img { width: 100%; height: 100%; object-fit: cover; }
  .empty { margin: 8px 0 0; color: var(--muted); text-align: center; }
  .shade { position: fixed; inset: 0; z-index: 30; display: grid; place-items: center; padding: 16px; background: rgba(0, 0, 0, 0.8); }
  .shade img { max-width: 100%; max-height: 100%; border-radius: 10px; }
</style>
