(() => {
  const orb = document.querySelector('.hero-orb');
  if (!orb) return;

  function finishRetreat(event) {
    if (event.target !== orb) return;
    orb.classList.remove('is-retreating');
    orb.removeAttribute('aria-disabled');
  }

  orb.addEventListener('click', () => {
    if (orb.classList.contains('is-retreating')) return;
    orb.setAttribute('aria-disabled', 'true');
    orb.classList.add('is-retreating');
  });
  orb.addEventListener('animationend', finishRetreat);
  orb.addEventListener('animationcancel', finishRetreat);
  orb.disabled = false;
})();

// Форма регистрации: прокрутка к ошибке, защита от повторных кликов и обратный отсчёт паузы
(() => {
  const form = document.querySelector('form.form');
  if (!form) return;
  const size = form.querySelector('#team_size');
  const roster = form.querySelector('#team-roster');
  const members = Array.from(roster.querySelectorAll('[data-member-position]'));

  function updateRoster() {
    const count = Number(size.value);
    roster.hidden = count <= 1;
    members.forEach((member) => {
      const active = Number(member.dataset.memberPosition) <= count;
      member.hidden = !active;
      member.disabled = !active;
      member.querySelectorAll('input').forEach((input) => { input.required = active; });
    });
  }

  size.addEventListener('change', updateRoster);
  updateRoster();
  const firstError = form.querySelector('.has-error, .alert');
  if (firstError) firstError.scrollIntoView({ block: 'center' });

  const btn = form.querySelector('button[type=submit]');
  const label = btn.textContent;
  let wait = parseInt(form.dataset.retryAfter || '0', 10) || 0;
  let timer = null;

  function unlock() { btn.disabled = false; btn.textContent = label; }

  function countdown() {
    if (wait <= 0) { unlock(); return; }
    btn.disabled = true;
    btn.textContent = `Подождите ${wait} с`;
    wait -= 1;
    timer = setTimeout(countdown, 1000);
  }
  countdown();

  form.addEventListener('submit', (e) => {
    if (btn.disabled) { e.preventDefault(); return; }
    // блокируем кнопку сразу: второй клик уже ничего не отправит
    btn.disabled = true;
    btn.textContent = 'Отправляем…';
    // если ответа долго нет (плохая сеть), даём попробовать снова через 15 с
    clearTimeout(timer);
    timer = setTimeout(unlock, 15000);
  });

  // вернулись кнопкой «назад» — страница из кеша, кнопку надо разблокировать
  window.addEventListener('pageshow', (e) => {
    updateRoster();
    if (e.persisted) unlock();
  });
})();
