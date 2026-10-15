// Админка: поиск, фильтры, отметка «пришёл», подтверждение удаления
(() => {
  const root = document.getElementById('admin');
  if (!root) return;
  const csrf = root.dataset.csrf;
  const total = parseInt(root.dataset.total, 10) || 0;
  const canEdit = root.dataset.canEdit === '1';
  const q = document.getElementById('q');
  let mode = 'all';

  function applyFilter() {
    const text = q.value.toLowerCase();
    document.querySelectorAll('tbody tr[data-id]').forEach((tr) => {
      const came = tr.classList.contains('came');
      const okMode = mode === 'all' || (mode === 'came') === came;
      tr.hidden = !(okMode && tr.textContent.toLowerCase().includes(text));
    });
  }
  q.addEventListener('input', applyFilter);
  document.querySelectorAll('.seg button').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('.seg button').forEach((x) => x.classList.toggle('on', x === b));
    mode = b.dataset.f;
    applyFilter();
  }));

  document.querySelectorAll('form[data-confirm]').forEach((f) => f.addEventListener('submit', (e) => {
    if (!confirm(f.dataset.confirm)) e.preventDefault();
  }));

  if (!canEdit) return;
  document.querySelectorAll('.attend').forEach((box) => box.addEventListener('change', async () => {
    const tr = box.closest('tr');
    const when = tr.querySelector('.when');
    box.disabled = true;
    try {
      const body = new FormData();
      body.append('csrf', csrf);
      body.append('attended', box.checked ? '1' : '0');
      const res = await fetch(box.dataset.url, {
        method: 'POST', body, headers: { 'X-Requested-With': 'fetch' }, credentials: 'same-origin',
      });
      if (res.redirected || !res.ok) throw new Error(res.status);
      const data = await res.json();
      tr.classList.toggle('came', data.attended);
      when.textContent = data.attended ? new Date().toTimeString().slice(0, 5) : '';
      document.getElementById('came').textContent = data.came;
      document.getElementById('missing').textContent = total - data.came;
      applyFilter();
    } catch (e) {
      box.checked = !box.checked;
      alert('Не удалось сохранить отметку. Возможно, вход истёк — обновите страницу.');
    } finally {
      box.disabled = false;
    }
  }));
})();
