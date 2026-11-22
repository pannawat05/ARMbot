<script>
  // Inspection results from the receiving dashboard: what was asked for, what it found, and the picture.
  let { results = [] } = $props();
  let open = $state(null); // result whose picture is shown large

  const latest = $derived(results[0]);
  const older = $derived(results.slice(1, 7));
  const names = (r) => [...new Set([...Object.keys(r.requested ?? {}), ...Object.keys(r.detected ?? {})])];
  const matches = (r) => names(r).every((n) => (r.requested?.[n] ?? 0) === (r.detected?.[n] ?? 0));
  const when = (r) => new Date((r.received_at ?? 0) * 1000).toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' });
</script>

<div class="head"><h2>ผลตรวจรับ</h2></div>

{#if latest}
  <article class:bad={!matches(latest)}>
    <button class="pic" onclick={() => (open = latest)} aria-label="ดูรูปผลตรวจขนาดใหญ่">
      <img src={latest.image} alt="รูปยืนยันผลตรวจ {latest.job_id}" />
    </button>
    <div class="body">
      <span class="chip">{matches(latest) ? 'ครบ' : 'ไม่ตรง'}</span>
      <small>{latest.job_id} · {when(latest)}</small>
      <ul>
        {#each names(latest) as n (n)}
          <li>{n}: ตรวจพบ {latest.detected?.[n] ?? 0} จาก {latest.requested?.[n] ?? 0}</li>
        {/each}
      </ul>
    </div>
  </article>
  {#if older.length}
    <div class="older">
      {#each older as r (r.event_id)}
        <button class:bad={!matches(r)} onclick={() => (open = r)} aria-label="ผลตรวจ {r.job_id}">
          <img src={r.image} alt="รูปผลตรวจ {r.job_id}" loading="lazy" />
        </button>
      {/each}
    </div>
  {/if}
{:else}
  <p class="empty">ยังไม่มีผลตรวจ</p>
{/if}

{#if open}
  <div class="shade" role="presentation" onclick={() => (open = null)} onkeydown={(e) => e.key === 'Escape' && (open = null)}>
    <img src={open.image} alt="รูปยืนยันผลตรวจ {open.job_id}" />
  </div>
{/if}

<style>
  .head { margin-top: 4px; }
  h2 { margin: 0; font-size: 1.05rem; }
  article { display: grid; grid-template-columns: 120px 1fr; gap: 10px; margin-top: 8px; padding: 8px; border-radius: 10px; background: var(--panel); border: 1px solid var(--line); }
  article.bad { border-color: var(--bad); }
  .pic { padding: 0; border: 0; background: none; line-height: 0; }
  .pic img { width: 100%; height: 100%; max-height: 120px; object-fit: cover; border-radius: 8px; }
  .body { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
  .body small { color: var(--muted); overflow-wrap: anywhere; }
  .chip { align-self: flex-start; font-size: 0.75rem; padding: 2px 8px; border-radius: 999px; background: var(--ok); color: #04210f; }
  .bad .chip { background: var(--bad); color: #fff; }
  ul { list-style: none; margin: 0; padding: 0; font-size: 0.9rem; }
  .older { display: flex; gap: 6px; margin-top: 6px; overflow-x: auto; }
  .older button { flex: 0 0 56px; height: 56px; padding: 0; border: 2px solid var(--line); border-radius: 8px; overflow: hidden; line-height: 0; background: none; }
  .older button.bad { border-color: var(--bad); }
  .older img { width: 100%; height: 100%; object-fit: cover; }
  .empty { margin: 8px 0 0; color: var(--muted); text-align: center; }
  .shade { position: fixed; inset: 0; z-index: 30; display: grid; place-items: center; padding: 16px; background: rgba(0, 0, 0, 0.8); }
  .shade img { max-width: 100%; max-height: 100%; border-radius: 10px; }
</style>
