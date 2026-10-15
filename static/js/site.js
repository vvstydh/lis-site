// Форма регистрации: прокрутка к ошибке, защита от повторных кликов и обратный отсчёт паузы
(() => {
  const firstError = document.querySelector('.has-error, .alert');
  if (firstError) firstError.scrollIntoView({ block: 'center' });

  const form = document.querySelector('form.form');
  if (!form) return;
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
  window.addEventListener('pageshow', (e) => { if (e.persisted) unlock(); });
})();
