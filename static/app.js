const app = document.querySelector('#app');
let testData;
const answers = new Map();
let selectedDifficulty;
let selectedSubject = 'python';
let currentUser;
let statisticsData;
let timerInterval;
let timerDeadline;
const TEST_DURATION_SECONDS = 20 * 60;
const THEME_STORAGE_KEY = 'python-test-lab-theme';
const SOUND_STORAGE_KEY = 'python-test-lab-sounds';
let soundEnabled = localStorage.getItem(SOUND_STORAGE_KEY) !== 'off';
let soundContext;
const REMINDER_ENABLED_STORAGE_KEY = 'python-test-lab-reminder-enabled';
const REMINDER_TIME_STORAGE_KEY = 'python-test-lab-reminder-time';
const COMPILER_CODE_STORAGE_KEY = 'python-test-lab-compiler-code';
const IS_ANDROID_APP = Boolean(window.OFFLINE_MODE) || location.protocol === 'file:';
let reminderEnabled = localStorage.getItem(REMINDER_ENABLED_STORAGE_KEY) === 'true';
let reminderTime = localStorage.getItem(REMINDER_TIME_STORAGE_KEY) || '19:00';
let compilerWorker;
let compilerTimeout;

const escapeHtml = (value = '') => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[char]);

function getPreferredTheme() {
  const saved = localStorage.getItem(THEME_STORAGE_KEY);
  if (saved === 'light' || saved === 'dark') return saved;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(theme = getPreferredTheme()) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem(THEME_STORAGE_KEY, theme);
  const toggle = document.querySelector('#theme-toggle');
  const isDark = theme === 'dark';
  if (!toggle) return;
  toggle.textContent = isDark ? '☀️ Светлая тема' : '🌙 Тёмная тема';
  toggle.setAttribute('aria-label', isDark ? 'Переключить на светлую тему' : 'Переключить на тёмную тему');
}

function renderAccountBar(username) {
  if (window.OFFLINE_MODE) {
    return `<div class="account-bar"><span>Автономный режим · <strong>${escapeHtml(username)}</strong></span></div>`;
  }
  return `<div class="account-bar"><span>Вы вошли как <strong>${escapeHtml(username)}</strong></span><div class="account-bar-actions"><button class="tool-button" id="logout-button">Выйти</button></div></div>`;
}

function bindThemeToggle() {
  const toggle = document.querySelector('#theme-toggle');
  if (!toggle) return;
  toggle.onclick = () => {
    const currentTheme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    applyTheme(currentTheme === 'dark' ? 'light' : 'dark');
  };
}

function updateSoundToggle() {
  const toggle = document.querySelector('#sound-toggle');
  if (!toggle) return;
  if (!window.AudioContext) {
    toggle.disabled = true;
    toggle.textContent = 'Звук недоступен';
    toggle.setAttribute('aria-label', 'Звуковые эффекты не поддерживаются браузером');
    toggle.setAttribute('aria-pressed', 'false');
    return;
  }
  toggle.textContent = soundEnabled ? '🔊 Звук' : '🔇 Без звука';
  toggle.setAttribute('aria-label', soundEnabled ? 'Выключить звуковые эффекты' : 'Включить звуковые эффекты');
  toggle.setAttribute('aria-pressed', String(soundEnabled));
}

function setSoundEnabled(enabled) {
  soundEnabled = enabled;
  localStorage.setItem(SOUND_STORAGE_KEY, soundEnabled ? 'on' : 'off');
  updateSoundToggle();
}

function bindSoundToggle() {
  const toggle = document.querySelector('#sound-toggle');
  if (!toggle) return;
  toggle.addEventListener('click', () => {
    setSoundEnabled(!soundEnabled);
    if (soundEnabled) playUiSound('answer');
  });
}

function getSoundContext() {
  if (!soundEnabled || !window.AudioContext) return null;
  if (!soundContext) soundContext = new window.AudioContext();
  if (soundContext.state === 'suspended') {
    soundContext.resume().catch((error) => console.error('Не удалось запустить звуковые эффекты.', error));
  }
  return soundContext;
}

function playUiSound(type) {
  const context = getSoundContext();
  if (!context) return;
  const notes = type === 'success'
    ? [523.25, 659.25, 783.99]
    : type === 'result'
      ? [392]
      : [587.33];
  const start = context.currentTime;
  const duration = type === 'answer' ? 0.055 : 0.09;
  notes.forEach((frequency, index) => {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const noteStart = start + index * 0.075;
    const noteEnd = noteStart + duration;
    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(frequency, noteStart);
    gain.gain.setValueAtTime(0.0001, noteStart);
    gain.gain.exponentialRampToValueAtTime(0.025, noteStart + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, noteEnd);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start(noteStart);
    oscillator.stop(noteEnd);
  });
}

function updateReminderButton() {
  const button = document.querySelector('#reminder-toggle');
  if (!button) return;
  button.textContent = reminderEnabled ? '🔔' : '🔕';
  button.setAttribute(
    'aria-label',
    reminderEnabled ? `Напоминание включено на ${reminderTime}. Настроить` : 'Настроить ежедневное напоминание',
  );
}

function bindReminderControls() {
  const button = document.querySelector('#reminder-toggle');
  const dialog = document.querySelector('#reminder-dialog');
  if (!button || !dialog) return;
  if (!IS_ANDROID_APP) {
    button.hidden = true;
    return;
  }

  button.hidden = false;
  const enabledInput = document.querySelector('#reminder-enabled');
  const timeInput = document.querySelector('#reminder-time');
  const status = document.querySelector('#reminder-status');
  updateReminderButton();
  button.addEventListener('click', () => {
    enabledInput.checked = reminderEnabled;
    timeInput.value = reminderTime;
    status.textContent = reminderEnabled
      ? `Напоминание включено на ${reminderTime}.`
      : 'Напоминания выключены.';
    dialog.showModal();
  });
  document.querySelector('#close-reminder').addEventListener('click', () => dialog.close());
  document.querySelector('#save-reminder').addEventListener('click', () => {
    if (!timeInput.reportValidity()) return;
    const [hour, minute] = timeInput.value.split(':').map(Number);
    const parameters = new URLSearchParams({
      enabled: String(enabledInput.checked),
      hour: String(hour),
      minute: String(minute),
    });
    status.textContent = 'Сохраняем настройки напоминания...';
    location.href = `python-test-lab://reminder?${parameters}`;
  });
  window.addEventListener('android-reminder-result', (event) => {
    const result = event.detail;
    if (!result.success) {
      enabledInput.checked = reminderEnabled;
      timeInput.value = reminderTime;
      status.textContent = result.message || 'Не удалось сохранить напоминание.';
      return;
    }
    reminderEnabled = result.enabled;
    reminderTime = `${String(result.hour).padStart(2, '0')}:${String(result.minute).padStart(2, '0')}`;
    localStorage.setItem(REMINDER_ENABLED_STORAGE_KEY, String(reminderEnabled));
    localStorage.setItem(REMINDER_TIME_STORAGE_KEY, reminderTime);
    updateReminderButton();
    status.textContent = result.message;
    dialog.close();
  });
}

function setAppRoute(route, replace = false) {
  const currentRoute = window.history.state?.appRoute;
  if (!replace && JSON.stringify(currentRoute) === JSON.stringify(route)) return;
  const method = replace ? 'replaceState' : 'pushState';
  window.history[method]({ appRoute: route }, '', window.location.href);
}

function navigateApp(route, render, replace = false) {
  if (route.view !== 'compiler') stopPythonCompiler();
  setAppRoute(route, replace);
  render();
}

async function restoreAppRoute(route) {
  if (route.view !== 'compiler') stopPythonCompiler();
  if (route.view === 'help') {
    renderHelp();
  } else if (route.view === 'compiler') {
    renderCompiler();
  } else if (route.view === 'theory') {
    loadTheoryIndex();
  } else if (route.view === 'article') {
    renderLoadingSkeleton('theory');
    try {
      await loadStatisticsData();
      const article = THEORY_ARTICLES.find((item) => item.id === route.articleId);
      if (article && getUnlockedTheoryArticleIds(statisticsData.passed_article_ids).has(article.id)) {
        renderTheoryArticle(article);
      } else {
        renderTheoryIndex();
      }
    } catch (error) {
      app.innerHTML = `<p class="error">${escapeHtml(error.message)}</p><button class="auth-switch" id="retry-statistics">Повторить</button>`;
      document.querySelector('#retry-statistics').addEventListener('click', () => restoreAppRoute(route));
    }
  } else if (route.view === 'statistics') {
    loadStatistics();
  } else if (route.view === 'test') {
    loadTest(route.difficulty, route.subject);
  } else if (route.view === 'auth') {
    renderAuth(route.mode);
  } else {
    loadDifficulties(route.subject || 'python');
  }
}

window.addEventListener('popstate', (event) => {
  restoreAppRoute(event.state?.appRoute || { view: 'tests', subject: 'python' });
});

applyTheme();
bindThemeToggle();
updateSoundToggle();
bindSoundToggle();
bindReminderControls();

function skeletonBlock(className = '') {
  return `<span class="skeleton-block ${className}" aria-hidden="true"></span>`;
}

function renderLoadingSkeleton(type) {
  const header = `<p class="eyebrow">${skeletonBlock('skeleton-eyebrow')}</p>
    <div class="skeleton-account">${skeletonBlock('skeleton-account-name')}${skeletonBlock('skeleton-account-button')}</div>
    <div class="skeleton-tabs">${skeletonBlock('skeleton-tab')}${skeletonBlock('skeleton-tab')}${skeletonBlock('skeleton-tab')}</div>`;
  let content;

  if (type === 'test') {
    content = `${skeletonBlock('skeleton-heading')}
      <div class="skeleton-meta">${skeletonBlock('skeleton-meta-item')}${skeletonBlock('skeleton-meta-item')}${skeletonBlock('skeleton-meta-item')}</div>
      ${Array.from({ length: 3 }, () => `<section class="skeleton-question">
        ${skeletonBlock('skeleton-question-label')}${skeletonBlock('skeleton-question-title')}
        ${skeletonBlock('skeleton-question-line')}${skeletonBlock('skeleton-option')}${skeletonBlock('skeleton-option')}${skeletonBlock('skeleton-option')}
      </section>`).join('')}`;
  } else if (type === 'theory') {
    content = `${skeletonBlock('skeleton-heading')}${skeletonBlock('skeleton-lead')}
      <div class="skeleton-grid">${Array.from({ length: 6 }, () => `<div class="skeleton-card">
        ${skeletonBlock('skeleton-card-label')}${skeletonBlock('skeleton-card-title')}
        ${skeletonBlock('skeleton-card-line')}${skeletonBlock('skeleton-card-line short')}
      </div>`).join('')}</div>`;
  } else if (type === 'statistics') {
    content = `${skeletonBlock('skeleton-heading')}${skeletonBlock('skeleton-lead')}
      <div class="skeleton-statistics">${Array.from({ length: 2 }, () => `<div class="skeleton-stat-card">
        ${skeletonBlock('skeleton-stat-label')}${skeletonBlock('skeleton-stat-value')}${skeletonBlock('skeleton-stat-line')}
      </div>`).join('')}</div>
      ${skeletonBlock('skeleton-section-heading')}
      <div class="skeleton-table">${Array.from({ length: 4 }, () => `<div class="skeleton-table-row">
        ${skeletonBlock('skeleton-table-cell')}${skeletonBlock('skeleton-table-cell wide')}${skeletonBlock('skeleton-table-cell')}
      </div>`).join('')}</div>`;
  } else {
    content = `${skeletonBlock('skeleton-heading')}${skeletonBlock('skeleton-lead')}
      ${Array.from({ length: 3 }, () => `<section class="skeleton-difficulty-section">
        ${skeletonBlock('skeleton-section-heading')}
        <div class="skeleton-grid">${Array.from({ length: 3 }, () => `<div class="skeleton-card">
          ${skeletonBlock('skeleton-card-label')}${skeletonBlock('skeleton-card-value')}
          ${skeletonBlock('skeleton-card-line')}${skeletonBlock('skeleton-card-line short')}
        </div>`).join('')}</div>
      </section>`).join('')}`;
  }

  app.innerHTML = `<div class="loading-skeleton" role="status" aria-label="Загрузка содержимого" aria-busy="true">
    ${header}${content}
  </div>`;
}

function questionTemplate(question, index) {
  const isWritten = question.question_type === 'written';
  const inputType = question.question_type === 'single' ? 'radio' : 'checkbox';
  const options = question.options.map((option) => `
    <label class="option">
      <input type="${inputType}" name="question-${question.id}" value="${option.id}">
      <span>${escapeHtml(option.label)}</span>
    </label>`).join('');

  if (isWritten) {
    return `<section class="question" data-question-id="${question.id}">
      <p class="question-number">Задание ${index + 1} / письменное · ${question.points} баллов</p>
      <h2>${escapeHtml(question.title)}</h2>
      <p>${escapeHtml(question.description)}</p>
      <div class="written-tools">
        <button type="button" class="tool-button" data-mode="text">Текстовый ответ</button>
        <button type="button" class="tool-button" data-mode="code">Редактор Python</button>
        <label class="tool-button">Загрузить .py <input class="file-input" type="file" accept=".py" hidden></label>
        <span class="file-name"></span>
      </div>
      <textarea class="written-answer" placeholder="Опишите идею решения или вставьте код..."></textarea>
      <textarea class="code-editor" hidden>${escapeHtml(question.starter_code || '')}</textarea>
    </section>`;
  }

  return `<section class="question" data-question-id="${question.id}">
    <p class="question-number">Задание ${index + 1} / ${question.question_type === 'single' ? 'один ответ' : 'несколько ответов'} · ${question.points} ${question.points === 1 ? 'балл' : 'балла'}</p>
    <h2>${escapeHtml(question.title)}</h2>
    <p>${escapeHtml(question.description)}</p>
    <div class="options">${options}</div>
  </section>`;
}

function renderTest() {
  app.innerHTML = `<button class="article-back" id="test-back"><span class="button-arrow" aria-hidden="true">←</span> Назад</button>
    <p class="eyebrow">${escapeHtml(testData.subject_label || 'Python')} / практика</p>
    <h1>${escapeHtml(testData.title)}</h1>
    <p class="lead">${escapeHtml(testData.subtitle)}. Письменное задание можно решить прямо здесь или передать готовым файлом.</p>
    <div class="meta"><span>${testData.question_count} заданий в попытке</span>${testData.is_topic_test ? '' : `<span>уровень: ${escapeHtml(testData.difficulty_label || testData.difficulty)}</span>`}<span>случайная выборка</span><span class="timer" id="timer">20:00</span></div>
    <section class="test-progress" aria-live="polite">
      <div><strong id="progress-count">0 / ${testData.question_count}</strong><span>заданий с ответом</span></div>
      <progress id="test-progress-bar" max="${testData.question_count}" value="0" aria-label="Прогресс выполнения теста"></progress>
    </section>
    <form id="test-form">${testData.questions.map(questionTemplate).join('')}
      <button class="submit-button" type="submit">Проверить тест <span class="button-arrow" aria-hidden="true">→</span></button>
    </form>`;

  window.scrollTo(0, 0);
  document.querySelector('#test-back').addEventListener('click', () => {
    if (window.history.state?.appRoute?.view === 'test' && window.history.length > 1) {
      window.history.back();
      return;
    }
    navigateApp(
      { view: 'tests', subject: 'python' },
      () => loadDifficulties('python'),
      true,
    );
  });
  document.querySelectorAll('.question').forEach((section) => {
    section.querySelectorAll('input[type="radio"], input[type="checkbox"]').forEach((input) => input.addEventListener('change', () => {
      playUiSound('answer');
      collectAnswer(section);
    }));
    section.querySelector('.written-answer')?.addEventListener('input', () => collectAnswer(section));
    section.querySelector('.code-editor')?.addEventListener('input', () => collectAnswer(section));
    section.querySelectorAll('[data-mode]').forEach((button) => button.addEventListener('click', () => {
      const text = section.querySelector('.written-answer');
      const code = section.querySelector('.code-editor');
      text.hidden = button.dataset.mode !== 'text';
      code.hidden = button.dataset.mode !== 'code';
    }));
    section.querySelector('.file-input')?.addEventListener('change', (event) => loadPythonFile(section, event.target.files[0]));
  });
  updateTestProgress();
  document.querySelector('#test-form').addEventListener('submit', submitTest);
  startTimer();
}

function updateTestProgress() {
  const count = [...document.querySelectorAll('.question')].filter((section) => {
    if (section.querySelector('input:checked')) return true;
    if (section.querySelector('.written-answer')?.value.trim()) return true;
    const codeEditor = section.querySelector('.code-editor');
    if (!codeEditor?.value.trim()) return false;
    const question = testData.questions.find((item) => item.id === Number(section.dataset.questionId));
    return codeEditor.value !== (question?.starter_code || '');
  }).length;
  const counter = document.querySelector('#progress-count');
  const progress = document.querySelector('#test-progress-bar');
  if (counter && progress) {
    counter.textContent = `${count} / ${testData.question_count}`;
    progress.value = count;
  }
}

function startTimer() {
  clearInterval(timerInterval);
  timerDeadline = Date.now() + TEST_DURATION_SECONDS * 1000;
  updateTimer();
  timerInterval = setInterval(updateTimer, 1000);
}

function updateTimer() {
  const timer = document.querySelector('#timer');
  if (!timer) {
    clearInterval(timerInterval);
    return;
  }
  const secondsLeft = Math.max(0, Math.ceil((timerDeadline - Date.now()) / 1000));
  const minutes = Math.floor(secondsLeft / 60).toString().padStart(2, '0');
  const seconds = (secondsLeft % 60).toString().padStart(2, '0');
  timer.textContent = `${minutes}:${seconds}`;
  timer.classList.toggle('timer-warning', secondsLeft <= 60);
  if (secondsLeft === 0) {
    clearInterval(timerInterval);
    const form = document.querySelector('#test-form');
    if (form) submitTest({ preventDefault: () => {}, currentTarget: form }, true);
  }
}

function renderDifficultyCards(difficulties, subject, unlockedArticleIds) {
  const article = findTheoryArticle(subject);
  const isLocked = article && !unlockedArticleIds.has(article.id);
  const lockLabel = isLocked ? getTopicLockLabel(article.id) : '';
  return difficulties.map((difficulty) => `
    <button class="difficulty-card ${isLocked ? 'is-locked' : ''}" data-difficulty="${difficulty.id}" data-subject="${subject}" ${isLocked || difficulty.question_count < 20 ? 'disabled aria-disabled="true"' : ''}>
      <span class="difficulty-label">${escapeHtml(difficulty.label)}</span>
      <strong>${difficulty.question_count}</strong>
      <small>уникальных вопросов</small>
      <span class="start-label">${lockLabel || (difficulty.question_count < 20 ? 'Недоступно' : 'Начать тест <span class="button-arrow" aria-hidden="true">→</span>')}</span>
    </button>`).join('');
}

function renderPythonBasicsTopics(unlockedArticleIds) {
  const topics = [
    ['py_conditions', 'Условный оператор', 'if, elif, else и логические условия'],
    ['py_strings', 'Строки', 'Индексы, срезы и строковые методы'],
    ['py_data', 'Массивы и структуры данных', 'Списки, кортежи, множества и словари'],
    ['py_loops', 'Циклы', 'for, while, range, break и continue'],
    ['py_oop', 'ООП', 'Классы, объекты, методы и наследование'],
    ['py_functions', 'Функции', 'Аргументы, параметры и возвращаемые значения'],
  ];
  return topics.map(([subject, title, description]) => {
    const isLocked = !unlockedArticleIds.has(subject);
    const lockLabel = isLocked ? getTopicLockLabel(subject) : '';
    return `<button class="topic-card ${isLocked ? 'is-locked' : ''}" data-subject="${subject}" ${isLocked ? 'disabled aria-disabled="true"' : ''}>
      <span class="difficulty-label">ТЕСТ ПО ТЕМЕ</span>
      <strong>${title}</strong>
      <small>${description}</small>
      <span class="start-label">${lockLabel || 'Начать тест <span class="button-arrow" aria-hidden="true">→</span>'}</span>
    </button>`;
  }).join('');
}

function renderDifficultySelection(pythonDifficulties, fastapiDifficulties, aiogramDifficulties, threadingDifficulties, multiprocessingDifficulties, asyncioDifficulties, generatorDifficulties, decoratorDifficulties, regexDifficulties, pandasDifficulties, unittestDifficulties, pytestDifficulties, linterDifficulties, filesFormatsDifficulties, databasesDifficulties) {
  const unlockedArticleIds = getUnlockedTheoryArticleIds(statisticsData?.passed_article_ids || []);
  app.innerHTML = `<p class="eyebrow">Python Test Lab / практика</p>
    ${renderAccountBar(currentUser.username)}
    ${renderWorkspaceTabs('tests')}
    <section class="welcome-hero">
      <div class="welcome-hero-copy">
        <p class="hero-kicker">Твоя практика начинается здесь</p>
        <h1>Учись. Практикуйся. <span>Расти.</span></h1>
        <p>Выбери тему и подходящий уровень — короткий шаг сегодня приблизит к большой цели.</p>
      </div>
      <svg class="hero-illustration" viewBox="0 0 300 230" role="img" aria-labelledby="hero-title hero-description">
        <title id="hero-title">Рабочее место разработчика</title>
        <desc id="hero-description">Ноутбук с цветным кодом, звёзды и листочки.</desc>
        <circle class="hero-orbit" cx="153" cy="112" r="91" />
        <path class="hero-spark" d="M47 64v20m-10-10h20M252 65v15m-8-7h16M70 177v12m-6-6h12" />
        <path class="hero-leaf" d="M232 157c4-26 20-38 44-37-4 23-17 37-44 37Zm0 0c-1-15-8-24-20-29 0 15 6 24 20 29Z" />
        <rect class="hero-screen" x="61" y="58" width="170" height="116" rx="17" />
        <path class="hero-screen-top" d="M61 75h170" />
        <circle class="hero-window-dot" cx="77" cy="67" r="3" />
        <circle class="hero-window-dot hero-window-dot-warm" cx="89" cy="67" r="3" />
        <path class="hero-code hero-code-purple" d="m93 101-10 8 10 8m16-16 10 8-10 8" />
        <path class="hero-code hero-code-green" d="M137 102h45m-45 14h33" />
        <path class="hero-code hero-code-warm" d="M94 139h24m12 0h55m-91 14h58" />
        <path class="hero-base" d="M49 180h194l-16 15H65l-16-15Z" />
        <rect class="hero-sticker" x="188" y="112" width="45" height="45" rx="15" transform="rotate(10 188 112)" />
        <path class="hero-sticker-mark" d="m205 131 5 5 10-12" />
      </svg>
    </section>
    ${window.OFFLINE_MODE ? '<p class="offline-notice">Приложение работает без интернета. Тесты, история и прогресс хранятся на этом устройстве.</p>' : ''}
    <section class="difficulty-section">
      <h2>Python</h2>
      <div class="difficulty-grid">${renderDifficultyCards(pythonDifficulties, 'python', unlockedArticleIds)}</div>
    </section>
    <section class="difficulty-section">
      <h2>FastAPI</h2>
      <div class="difficulty-grid">${renderDifficultyCards(fastapiDifficulties, 'fastapi', unlockedArticleIds)}</div>
    </section>
    <section class="difficulty-section">
      <h2>Создание ботов на aiogram</h2>
      <div class="difficulty-grid">${renderDifficultyCards(aiogramDifficulties, 'aiogram', unlockedArticleIds)}</div>
    </section>`;
  app.querySelector('.difficulty-section:last-child').insertAdjacentHTML('afterend', `
    <section class="difficulty-section">
      <h2>Многопоточность</h2>
      <div class="difficulty-grid">${renderDifficultyCards(threadingDifficulties, 'threading', unlockedArticleIds)}</div>
    </section>
    <section class="difficulty-section">
      <h2>Многопроцессорность</h2>
      <div class="difficulty-grid">${renderDifficultyCards(multiprocessingDifficulties, 'multiprocessing', unlockedArticleIds)}</div>
    </section>
    <section class="difficulty-section">
      <h2>Асинхронность</h2>
      <div class="difficulty-grid">${renderDifficultyCards(asyncioDifficulties, 'asyncio', unlockedArticleIds)}</div>
    </section>
    <section class="difficulty-section">
      <h2>Генератор</h2>
      <div class="difficulty-grid">${renderDifficultyCards(generatorDifficulties, 'generator', unlockedArticleIds)}</div>
    </section>
    <section class="difficulty-section">
      <h2>Декоратор</h2>
      <div class="difficulty-grid">${renderDifficultyCards(decoratorDifficulties, 'decorator', unlockedArticleIds)}</div>
    </section>
    <section class="difficulty-section">
      <h2>Регулярные выражения</h2>
      <div class="difficulty-grid">${renderDifficultyCards(regexDifficulties, 'regex', unlockedArticleIds)}</div>
    </section>
    <section class="difficulty-section">
      <h2>Pandas</h2>
      <div class="difficulty-grid">${renderDifficultyCards(pandasDifficulties, 'pandas', unlockedArticleIds)}</div>
    </section>
    <section class="difficulty-section">
      <h2>unittest</h2>
      <div class="difficulty-grid">${renderDifficultyCards(unittestDifficulties, 'unittest', unlockedArticleIds)}</div>
    </section>
    <section class="difficulty-section">
      <h2>pytest</h2>
      <div class="difficulty-grid">${renderDifficultyCards(pytestDifficulties, 'pytest', unlockedArticleIds)}</div>
    </section>
    <section class="difficulty-section">
      <h2>Линтеры</h2>
      <div class="difficulty-grid">${renderDifficultyCards(linterDifficulties, 'linters', unlockedArticleIds)}</div>
    </section>
    <section class="difficulty-section">
      <h2>Работа с файлами и форматами</h2>
      <div class="difficulty-grid">${renderDifficultyCards(filesFormatsDifficulties, 'files_formats', unlockedArticleIds)}</div>
    </section>
    <section class="difficulty-section">
      <h2>Базы данных: PostgreSQL, SQLite и MySQL</h2>
      <div class="difficulty-grid">${renderDifficultyCards(databasesDifficulties, 'databases', unlockedArticleIds)}</div>
    </section>`);
  app.querySelector('.difficulty-section').insertAdjacentHTML('afterend', `
    <section class="difficulty-section">
      <h2>Основы Python</h2>
      <p class="lead">Условия, строки, списки и структуры данных, циклы, ООП и функции.</p>
      <div class="difficulty-grid topic-grid">${renderPythonBasicsTopics(unlockedArticleIds)}</div>
    </section>`);
  document.querySelectorAll('.difficulty-card:not([disabled])').forEach((button) => {
    button.addEventListener('click', () => {
      selectedSubject = button.dataset.subject;
      navigateApp(
        { view: 'test', difficulty: button.dataset.difficulty, subject: selectedSubject },
        () => loadTest(button.dataset.difficulty, selectedSubject),
      );
    });
  });
  document.querySelectorAll('.topic-card:not([disabled])').forEach((button) => {
    button.addEventListener('click', () => {
      selectedSubject = button.dataset.subject;
      navigateApp(
        { view: 'test', difficulty: 'medium', subject: selectedSubject },
        () => loadTest('medium', selectedSubject),
      );
    });
  });
  bindWorkspaceTabs();
}

function renderWorkspaceTabs(activeView) {
  return `<nav class="view-tabs" aria-label="Разделы">
    <button class="view-tab ${activeView === 'tests' ? 'is-active' : ''}" data-view="tests" aria-pressed="${activeView === 'tests'}">
      <svg class="view-tab-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h12M8 12h12M8 18h12M3 6h.01M3 12h.01M3 18h.01"/></svg>
      <span>Тесты</span>
    </button>
    <button class="view-tab ${activeView === 'theory' ? 'is-active' : ''}" data-view="theory" aria-pressed="${activeView === 'theory'}">
      <svg class="view-tab-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v17H6.5A2.5 2.5 0 0 1 4 17.5zM4 17.5A2.5 2.5 0 0 1 6.5 15H20M8 7h8M8 10h8"/></svg>
      <span>Теория</span>
    </button>
    <button class="view-tab ${activeView === 'statistics' ? 'is-active' : ''}" data-view="statistics" aria-pressed="${activeView === 'statistics'}">
      <svg class="view-tab-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19V5M4 19h17M8 15v-4M13 15V7M18 15V3"/></svg>
      <span>Статистика</span>
    </button>
    <button class="view-tab ${activeView === 'help' ? 'is-active' : ''}" data-view="help" aria-pressed="${activeView === 'help'}">
      <svg class="view-tab-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.6 9a2.5 2.5 0 1 1 4.2 1.8c-1.1 1-1.8 1.4-1.8 3M12 17h.01"/></svg>
      <span>Справка</span>
    </button>
    <button class="view-tab ${activeView === 'compiler' ? 'is-active' : ''}" data-view="compiler" aria-label="Компилятор Python" aria-pressed="${activeView === 'compiler'}">
      <svg class="view-tab-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m8 8-4 4 4 4m8-8 4 4-4 4M14 5l-4 14"/></svg>
      <span>Код</span>
    </button>
  </nav>`;
}

const COMPILER_STARTER_CODE = `name = "мир"
print("Привет,", name)

for number in range(1, 4):
    print(number, "в квадрате =", number ** 2)`;

function renderCompiler() {
  stopPythonCompiler();
  app.innerHTML = `<p class="eyebrow">Python Test Lab / практика</p>
    ${currentUser ? `${renderAccountBar(currentUser.username)}${renderWorkspaceTabs('compiler')}` : '<button class="article-back" id="compiler-back"><span class="button-arrow" aria-hidden="true">←</span> Ко входу</button>'}
    <h1>Python-компилятор</h1>
    <p class="lead">Пишите и запускайте Python прямо в приложении. Код выполняется на устройстве, без отправки на сервер.</p>
    <section class="compiler-panel" aria-label="Редактор Python">
      <div class="compiler-toolbar">
        <label for="compiler-editor">Редактор кода</label>
        <div class="compiler-actions">
          <button type="button" class="tool-button" id="compiler-example">Пример</button>
          <button type="button" class="submit-button" id="compiler-run">▶ Запустить</button>
        </div>
      </div>
      <textarea class="compiler-editor" id="compiler-editor" aria-label="Исходный код на Python" autocapitalize="off" autocomplete="off" autocorrect="off" spellcheck="false" wrap="off"></textarea>
      <div class="compiler-output-panel">
        <div class="compiler-output-heading">
          <strong>Консоль</strong>
          <button type="button" class="tool-button" id="compiler-clear">Очистить</button>
        </div>
        <pre class="compiler-output" id="compiler-output" role="status" aria-live="polite">Здесь появится результат выполнения.</pre>
      </div>
      <p class="compiler-note">Ограничение запуска — 2,5 секунды. Поддерживаются основные возможности Python; некоторые стандартные модули CPython могут отсутствовать.</p>
    </section>`;

  const editor = document.querySelector('#compiler-editor');
  const output = document.querySelector('#compiler-output');
  const savedCode = localStorage.getItem(COMPILER_CODE_STORAGE_KEY);
  editor.value = savedCode === null ? COMPILER_STARTER_CODE : savedCode;
  editor.addEventListener('input', () => {
    localStorage.setItem(COMPILER_CODE_STORAGE_KEY, editor.value);
  });
  editor.addEventListener('keydown', (event) => {
    if (event.key === 'Tab') {
      event.preventDefault();
      const start = editor.selectionStart;
      const end = editor.selectionEnd;
      editor.setRangeText('    ', start, end, 'end');
      editor.dispatchEvent(new Event('input', { bubbles: true }));
    } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      runPythonCode();
    }
  });
  document.querySelector('#compiler-example').addEventListener('click', () => {
    editor.value = COMPILER_STARTER_CODE;
    editor.dispatchEvent(new Event('input', { bubbles: true }));
    output.textContent = 'Пример загружен. Нажмите «Запустить».';
  });
  document.querySelector('#compiler-clear').addEventListener('click', () => {
    output.textContent = '';
  });
  document.querySelector('#compiler-run').addEventListener('click', runPythonCode);
  const backButton = document.querySelector('#compiler-back');
  if (backButton) {
    backButton.addEventListener('click', () => {
      navigateApp({ view: 'auth', mode: 'login' }, () => renderAuth());
    });
  }
}

function stopPythonCompiler() {
  if (compilerTimeout) {
    clearTimeout(compilerTimeout);
    compilerTimeout = null;
  }
  if (compilerWorker) {
    compilerWorker.terminate();
    compilerWorker = null;
  }
}

function runPythonCode() {
  const editor = document.querySelector('#compiler-editor');
  const output = document.querySelector('#compiler-output');
  const runButton = document.querySelector('#compiler-run');
  if (!editor || !output || !runButton) return;
  if (!editor.value.trim()) {
    output.textContent = 'Введите код Python перед запуском.';
    editor.focus();
    return;
  }
  if (editor.value.length > 10_000) {
    output.textContent = 'Код слишком длинный. Максимальный размер — 10 000 символов.';
    return;
  }

  stopPythonCompiler();
  const workerUrl = IS_ANDROID_APP
    ? new URL('python-runner-worker.js', location.href)
    : new URL('/static/python-runner-worker.js', location.origin);
  try {
    compilerWorker = new Worker(workerUrl);
  } catch (error) {
    output.textContent = `Не удалось запустить интерпретатор: ${error.message}`;
    return;
  }

  const worker = compilerWorker;
  runButton.disabled = true;
  output.textContent = 'Выполняется...';
  worker.addEventListener('message', (event) => {
    if (compilerWorker !== worker) return;
    output.textContent = event.data.error
      ? `${event.data.output}${event.data.output ? '\n' : ''}${event.data.error}`
      : event.data.output || 'Код выполнен без вывода.';
    stopPythonCompiler();
    runButton.disabled = false;
  });
  worker.addEventListener('error', (event) => {
    if (compilerWorker !== worker) return;
    output.textContent = `Ошибка интерпретатора: ${event.message || 'неизвестная ошибка'}`;
    stopPythonCompiler();
    runButton.disabled = false;
  });
  compilerTimeout = setTimeout(() => {
    if (compilerWorker !== worker) return;
    stopPythonCompiler();
    output.textContent = 'Превышено время запуска (2,5 секунды). Проверьте код и попробуйте снова.';
    runButton.disabled = false;
  }, 3000);
  worker.postMessage({ code: editor.value });
}

function renderHelp() {
  const isOffline = Boolean(window.OFFLINE_MODE);
  const onlineMode = isOffline
    ? 'Android-приложение хранит профиль, результаты тестов и прогресс на этом устройстве. Для работы не нужен интернет; данные веб-аккаунта с ним не синхронизируются.'
    : 'Веб-версия работает в браузере на компьютере или телефоне. Войдите в аккаунт, чтобы сохранять результаты и прогресс; для работы нужен доступ к серверу.';
  app.innerHTML = `<p class="eyebrow">Python Test Lab / справка</p>
    ${currentUser ? `${renderAccountBar(currentUser.username)}${renderWorkspaceTabs('help')}` : '<button class="article-back" id="help-back"><span class="button-arrow" aria-hidden="true">←</span> Ко входу</button>'}
    <h1>Как пользоваться приложением</h1>
    <p class="lead">Краткие инструкции для веб-версии и мобильного приложения.</p>
    <section class="help-section">
      <h2>Веб-версия</h2>
      <ol>
        <li>Войдите в аккаунт или зарегистрируйтесь. Попытки тестов и изученные материалы сохраняются в аккаунте.</li>
        <li>Во вкладке «Тесты» выберите тему и уровень, затем отметьте ответы или заполните письменное задание.</li>
        <li>Для письменного задания можно переключаться между текстом и редактором Python, а также загрузить файл <code>.py</code>.</li>
        <li>Во вкладке «Код» можно запускать свои программы Python; черновик редактора сохраняется в браузере.</li>
        <li>Нажмите «Проверить тест», чтобы увидеть результат, разбор ответов и оценку.</li>
        <li>Во вкладке «Теория» изучайте материалы по порядку. Следующие темы открываются после прохождения предыдущих тестов; результаты доступны в «Статистике».</li>
      </ol>
    </section>
    <section class="help-section">
      <h2>Мобильное приложение</h2>
      <ol>
        <li>Внизу экрана находятся разделы «Тесты», «Теория», «Статистика» и «Справка».</li>
        <li>Тесты, материалы и проверка ответов работают без интернета. Прогресс хранится локально на устройстве.</li>
        <li>Письменное решение можно набрать в приложении или выбрать файл <code>.py</code> на устройстве.</li>
        <li>Во вкладке «Код» можно редактировать и запускать Python-программы; черновик остаётся на устройстве.</li>
        <li>Нажмите значок колокольчика, чтобы включить ежедневное напоминание и выбрать время. Разрешите уведомления в системном запросе Android.</li>
        <li>Мобильные результаты хранятся отдельно от веб-аккаунта и не синхронизируются между устройствами.</li>
      </ol>
    </section>
    <section class="help-section help-controls">
      <h2>Настройки интерфейса</h2>
      <ul>
        <li>Кнопка «Тема» переключает светлый и тёмный режим; выбор запоминается.</li>
        <li>Кнопка «Звук» включает или выключает звуковые эффекты.</li>
        <li>В разделе «Код» можно набрать программу Python и запустить её локально.</li>
        ${isOffline ? '<li>Напоминания — только локальные уведомления Android; в веб-браузере эта настройка недоступна.</li>' : '<li>В браузере доступны тема и звук; мобильные напоминания настраиваются только в Android-приложении.</li>'}
      </ul>
    </section>
    <p class="offline-notice">${onlineMode}</p>`;
  if (currentUser) {
    bindWorkspaceTabs();
    return;
  }
  document.querySelector('#help-back').addEventListener('click', () => {
    navigateApp({ view: 'auth', mode: 'login' }, () => renderAuth());
  });
}

function bindWorkspaceTabs() {
  bindThemeToggle();
  document.querySelectorAll('.view-tab').forEach((button) => {
    button.addEventListener('click', () => {
      if (button.dataset.view === 'theory') {
        navigateApp({ view: 'theory' }, loadTheoryIndex);
      } else if (button.dataset.view === 'statistics') {
        navigateApp({ view: 'statistics' }, loadStatistics);
      } else if (button.dataset.view === 'help') {
        navigateApp({ view: 'help' }, renderHelp);
      } else if (button.dataset.view === 'compiler') {
        navigateApp({ view: 'compiler' }, renderCompiler);
      } else {
        navigateApp({ view: 'tests', subject: 'python' }, () => loadDifficulties('python'));
      }
    });
  });
  const logoutButton = document.querySelector('#logout-button');
  if (logoutButton) {
    logoutButton.addEventListener('click', async () => {
      await fetch('/api/auth/logout', { method: 'POST' });
      currentUser = null;
      setAppRoute({ view: 'auth', mode: 'login' }, true);
      renderAuth();
    });
  }
}

async function loadStatisticsData() {
  const response = await fetch('/api/statistics');
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Не удалось загрузить статистику.');
  statisticsData = result;
  return result;
}

async function loadTheoryIndex() {
  renderLoadingSkeleton('theory');
  try {
    await loadStatisticsData();
    renderTheoryIndex();
  } catch (error) {
    app.innerHTML = `<p class="error">${escapeHtml(error.message)}</p><button class="auth-switch" id="retry-statistics">Повторить</button>`;
    document.querySelector('#retry-statistics').addEventListener('click', loadTheoryIndex);
  }
}

function renderTheoryIndex() {
  const completedArticles = new Set(statisticsData?.completed_article_ids || []);
  const passedArticles = new Set(statisticsData?.passed_article_ids || []);
  const unlockedArticles = getUnlockedTheoryArticleIds(passedArticles);
  let passedTopicCount = 0;
  while (passedTopicCount < THEORY_ARTICLES.length
    && passedArticles.has(THEORY_ARTICLES[passedTopicCount].id)) {
    passedTopicCount += 1;
  }
  app.innerHTML = `<p class="eyebrow">Python Test Lab / материалы</p>
    ${renderAccountBar(currentUser.username)}
    ${renderWorkspaceTabs('theory')}
    <h1>Теория</h1>
    <p class="lead">Краткие статьи помогут повторить ключевые понятия перед тестированием. Выберите тему, чтобы открыть материал.</p>
    <section class="course-progress" aria-label="Общий прогресс по темам">
      <div><span>Общий прогресс</span><strong>${passedTopicCount} / ${THEORY_ARTICLES.length} тем</strong></div>
      <progress max="${THEORY_ARTICLES.length}" value="${passedTopicCount}" aria-label="Пройденные темы"></progress>
    </section>
    <p class="progress-summary">Изучено материалов: <strong>${statisticsData.completed_material_count} / ${statisticsData.total_material_count}</strong></p>
    <div class="theory-grid">${THEORY_ARTICLES.map((article, index) => {
      const isLocked = !unlockedArticles.has(article.id);
      const isPassed = passedArticles.has(article.id);
      const isCompleted = completedArticles.has(article.id);
      const previousArticle = THEORY_ARTICLES[index - 1];
      const actionLabel = isLocked
        ? `🔒 Сначала пройдите «${escapeHtml(previousArticle.title)}»`
        : isPassed
          ? '✓ Тема пройдена · Читать снова'
          : isCompleted
            ? '✓ Изучено · Читать снова'
            : 'Читать статью';
      return `<button class="theory-card ${isCompleted ? 'is-completed' : ''} ${isPassed ? 'is-passed' : ''} ${isLocked ? 'is-locked' : ''}" data-article="${escapeHtml(article.id)}" ${isLocked ? 'disabled aria-disabled="true"' : ''}>
        <span class="difficulty-label">${escapeHtml(article.category)}</span>
        <strong>${escapeHtml(article.title)}</strong>
        <small>${escapeHtml(article.summary)}</small>
        <span class="start-label">${actionLabel}${isLocked ? '' : ' <span class="button-arrow" aria-hidden="true">→</span>'}</span>
      </button>`;
    }).join('')}
    </div>`;
  bindWorkspaceTabs();
  document.querySelectorAll('.theory-card:not(.is-locked)').forEach((button) => {
    button.addEventListener('click', () => {
      const article = THEORY_ARTICLES.find((item) => item.id === button.dataset.article);
      if (article) {
        navigateApp({ view: 'article', articleId: article.id }, () => renderTheoryArticle(article));
      }
    });
  });
}

function renderTheoryArticle(article) {
  const sections = article.sections.map((section) => `
    <section class="theory-section">
      <h2>${escapeHtml(section.heading)}</h2>
      ${section.text ? `<p>${escapeHtml(section.text)}</p>` : ''}
      ${section.points ? `<ul>${section.points.map((point) => `<li>${escapeHtml(point)}</li>`).join('')}</ul>` : ''}
      ${section.code ? `<pre><code>${escapeHtml(section.code)}</code></pre>` : ''}
    </section>`).join('');
  app.innerHTML = `<p class="eyebrow">Python Test Lab / материалы</p>
    ${renderAccountBar(currentUser.username)}
    ${renderWorkspaceTabs('theory')}
    <button class="article-back" id="theory-back"><span class="button-arrow" aria-hidden="true">←</span> Назад</button>
    <article class="theory-article">
      <p class="difficulty-label">${escapeHtml(article.category)}</p>
      <h1>${escapeHtml(article.title)}</h1>
      <p class="lead">${escapeHtml(article.summary)}</p>
      ${sections}
      <div class="article-cta">
        <h2>Готовы проверить знания?</h2>
        <p>Пройдите тест по теме «${escapeHtml(article.title)}».</p>
        <button class="tool-button" id="mark-material-button" ${statisticsData?.completed_article_ids.includes(article.id) ? 'disabled' : ''}>
          ${statisticsData?.completed_article_ids.includes(article.id) ? 'Материал изучен ✓' : 'Отметить материал изученным'}
        </button>
        <button class="submit-button" id="article-test-button">Перейти к тесту <span class="button-arrow" aria-hidden="true">→</span></button>
      </div>
    </article>`;
  bindWorkspaceTabs();
  document.querySelector('#theory-back').addEventListener('click', () => {
    if (window.history.state?.appRoute?.view === 'article') window.history.back();
    else renderTheoryIndex();
  });
  document.querySelector('#mark-material-button').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const response = await fetch('/api/statistics/materials', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ article_id: article.id }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Не удалось сохранить прогресс.');
      await loadStatisticsData();
      renderTheoryArticle(article);
    } catch (error) {
      button.disabled = false;
      button.insertAdjacentHTML('afterend', `<p class="error">${escapeHtml(error.message)}</p>`);
    }
  });
  document.querySelector('#article-test-button').addEventListener('click', () => {
    navigateApp(
      { view: 'test', difficulty: 'medium', subject: article.subject },
      () => loadTest('medium', article.subject),
    );
  });
}

async function loadStatistics() {
  renderLoadingSkeleton('statistics');
  try {
    await loadStatisticsData();
    renderStatistics();
  } catch (error) {
    app.innerHTML = `<p class="error">${escapeHtml(error.message)}</p><button class="auth-switch" id="retry-statistics">Повторить</button>`;
    document.querySelector('#retry-statistics').addEventListener('click', loadStatistics);
  }
}

function renderStatistics() {
  const attempts = statisticsData.attempts;
  const attemptRows = attempts.length ? attempts.map((attempt) => `
    <tr>
      <td>${escapeHtml(new Date(attempt.created_at).toLocaleString('ru-RU'))}</td>
      <td>${escapeHtml(attempt.subject_label)} · ${escapeHtml(attempt.difficulty_label)}</td>
      <td>${attempt.score} / ${attempt.max_score} (${attempt.score_percent}%)</td>
      <td><strong class="grade grade-${attempt.grade}">${attempt.grade} · ${escapeHtml(attempt.grade_label)}</strong>${attempt.written_pending ? '<small class="pending-grade">Предварительная</small>' : ''}</td>
    </tr>`).join('') : '<tr><td colspan="4">Пока нет завершенных тестов.</td></tr>';
  app.innerHTML = `<p class="eyebrow">Python Test Lab / результаты</p>
    ${renderAccountBar(currentUser.username)}
    ${renderWorkspaceTabs('statistics')}
    <h1>Ваша статистика</h1>
    <p class="lead">${window.OFFLINE_MODE ? 'История тестов и прогресс изучения сохраняются на этом устройстве.' : 'История изученных материалов и результаты каждой попытки сохраняются в аккаунте.'}</p>
    <section class="statistics-cards">
      <article class="statistics-card"><span>Изучено материалов</span><strong>${statisticsData.completed_material_count} / ${statisticsData.total_material_count}</strong>
        <progress max="${statisticsData.total_material_count}" value="${statisticsData.completed_material_count}" aria-label="Изученные материалы"></progress></article>
      <article class="statistics-card"><span>Пройдено тестов</span><strong>${statisticsData.completed_test_count}</strong><small>Средний результат: ${statisticsData.average_score_percent}%</small></article>
    </section>
    <section class="statistics-history">
      <h2>Результаты тестов</h2>
      <div class="table-scroll"><table class="stats-table">
        <thead><tr><th>Дата</th><th>Тема · уровень</th><th>Баллы</th><th>Оценка</th></tr></thead>
        <tbody>${attemptRows}</tbody>
      </table></div>
      ${attempts.length > 30 ? '<p>Показаны последние 30 попыток.</p>' : ''}
    </section>`;
  bindWorkspaceTabs();
}

function findTheoryArticle(subject) {
  if (subject === 'python_bases') subject = 'python';
  return THEORY_ARTICLES.find((article) => article.subject === subject || article.id === subject);
}

function getUnlockedTheoryArticleIds(passedArticleIds = []) {
  const passedArticles = new Set(passedArticleIds);
  const unlockedArticles = new Set();
  for (const article of THEORY_ARTICLES) {
    if (unlockedArticles.size && !passedArticles.has(THEORY_ARTICLES[unlockedArticles.size - 1].id)) break;
    unlockedArticles.add(article.id);
  }
  return unlockedArticles;
}

function getTopicLockLabel(articleId) {
  const articleIndex = THEORY_ARTICLES.findIndex((article) => article.id === articleId);
  if (articleIndex <= 0) return '';
  return `🔒 Сначала пройдите «${escapeHtml(THEORY_ARTICLES[articleIndex - 1].title)}»`;
}

function renderAuth(mode = 'login', error = '') {
  const register = mode === 'register';
  app.innerHTML = `<p class="eyebrow">Python Test Lab / аккаунт</p>
    <h1>${register ? 'Создайте аккаунт' : 'Войдите в аккаунт'}</h1>
    <p class="lead">Ваши попытки и результаты будут сохраняться в базе данных.</p>
    <form id="auth-form" class="auth-form">
      ${register ? '<label>Имя пользователя<input name="username" minlength="3" maxlength="80" required></label><label>Email<input name="email" type="email" required></label>' : '<label>Имя пользователя или email<input name="login" required></label>'}
      <label>Пароль<input name="password" type="password" minlength="6" required></label>
      ${error ? `<p class="error">${escapeHtml(error)}</p>` : ''}
      <button class="submit-button" type="submit">${register ? 'Зарегистрироваться' : 'Войти'}</button>
    </form>
    <button class="auth-switch" id="auth-switch">${register ? 'Уже есть аккаунт? Войти' : 'Нет аккаунта? Зарегистрироваться'}</button>
    <button class="auth-switch" id="open-help">Справка: как пользоваться</button>
    <button class="auth-switch" id="open-compiler">Открыть Python-компилятор</button>`;
  document.querySelector('#auth-switch').addEventListener('click', () => {
    const mode = register ? 'login' : 'register';
    navigateApp({ view: 'auth', mode }, () => renderAuth(mode));
  });
  document.querySelector('#open-help').addEventListener('click', () => {
    navigateApp({ view: 'help' }, renderHelp);
  });
  document.querySelector('#open-compiler').addEventListener('click', () => {
    navigateApp({ view: 'compiler' }, renderCompiler);
  });
  bindThemeToggle();
  document.querySelector('#auth-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const submitButton = formElement.querySelector('button[type="submit"]');
    if (submitButton.disabled) return;
    submitButton.disabled = true;
    submitButton.textContent = 'Подождите...';
    let succeeded = false;
    try {
      const form = new FormData(formElement);
      const payload = Object.fromEntries(form.entries());
      const response = await fetch(register ? '/api/auth/register' : '/api/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) {
        const message = typeof result.detail === 'string' ? result.detail : 'Проверьте введённые данные.';
        let error = formElement.querySelector('.error');
        if (!error) {
          error = document.createElement('p');
          error.className = 'error';
          error.setAttribute('role', 'alert');
          formElement.insertBefore(error, submitButton);
        }
        error.textContent = message;
        return;
      }
      currentUser = result;
      succeeded = true;
    } catch {
      let error = formElement.querySelector('.error');
      if (!error) {
        error = document.createElement('p');
        error.className = 'error';
        error.setAttribute('role', 'alert');
        formElement.insertBefore(error, submitButton);
      }
      error.textContent = 'Не удалось связаться с сервером. Проверьте подключение и попробуйте снова.';
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = register ? 'Зарегистрироваться' : 'Войти';
    }
    if (succeeded) {
      setAppRoute({ view: 'tests', subject: 'python' }, true);
      loadDifficulties('python');
    }
  });
}

async function loadDifficulties(subject = selectedSubject) {
  selectedSubject = subject;
  const subjects = ['python', 'fastapi', 'aiogram', 'threading', 'multiprocessing', 'asyncio', 'generator', 'decorator', 'regex', 'pandas', 'unittest', 'pytest', 'linters', 'files_formats', 'databases'];
  const params = new URLSearchParams();
  subjects.forEach((id) => params.append('subjects', id));
  renderLoadingSkeleton('workspace');
  try {
    const response = await fetch(`/api/difficulties/batch?${params}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Не удалось загрузить уровни сложности.');
    await loadStatisticsData();
    renderDifficultySelection(
      ...subjects.map((id) => data.difficulties[id]),
    );
  } catch (error) {
    app.innerHTML = `<p class="error">${escapeHtml(error.message || 'Не удалось загрузить уровни сложности.')}</p><button class="auth-switch" id="retry-button">Повторить</button>`;
    document.querySelector('#retry-button').addEventListener('click', () => loadDifficulties(subject));
  }
}

async function loadTest(difficulty, subject = selectedSubject) {
  selectedDifficulty = difficulty;
  selectedSubject = subject;
  answers.clear();
  renderLoadingSkeleton('test');
  try {
    const article = findTheoryArticle(subject);
    if (article) {
      await loadStatisticsData();
      const unlockedArticleIds = getUnlockedTheoryArticleIds(statisticsData.passed_article_ids);
      if (!unlockedArticleIds.has(article.id)) {
        app.innerHTML = `<p class="error">Сначала пройдите предыдущую тему, чтобы открыть «${escapeHtml(article.title)}».</p>
          <button class="auth-switch" id="return-to-theory">Перейти к теории</button>`;
        document.querySelector('#return-to-theory').addEventListener('click', () => {
          navigateApp({ view: 'theory' }, loadTheoryIndex);
        });
        return;
      }
    }
    const response = await fetch(`/api/test?difficulty=${encodeURIComponent(difficulty)}&subject=${encodeURIComponent(subject)}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Не удалось загрузить тест этого уровня');
    testData = result;
    renderTest();
  } catch (error) {
    app.innerHTML = `<p class="error">${escapeHtml(error.message || 'Сервер недоступен')}</p><button class="auth-switch" id="retry-button">Вернуться к уровням</button>`;
    document.querySelector('#retry-button').addEventListener('click', () => {
      navigateApp(
        { view: 'tests', subject: 'python' },
        () => loadDifficulties('python'),
        true,
      );
    });
  }
}

function collectAnswer(section) {
  const questionId = Number(section.dataset.questionId);
  const selectedOptionIds = [...section.querySelectorAll('input:checked')].map((input) => Number(input.value));
  const text = section.querySelector('.code-editor')?.hidden === false
    ? section.querySelector('.code-editor').value
    : section.querySelector('.written-answer')?.value;
  answers.set(questionId, { question_id: questionId, selected_option_ids: selectedOptionIds, answer_text: text || null });
  updateTestProgress();
}

async function loadPythonFile(section, file) {
  if (!file) return;
  const formData = new FormData();
  formData.append('question_id', section.dataset.questionId);
  formData.append('file', file);
  const response = await fetch('/api/submit-file', { method: 'POST', body: formData });
  const result = await response.json();
  if (!response.ok) { section.querySelector('.file-name').textContent = result.detail; return; }
  section.querySelector('.code-editor').value = result.content;
  section.querySelector('.written-answer').hidden = true;
  section.querySelector('.code-editor').hidden = false;
  section.querySelector('.file-name').textContent = `Загружен: ${result.filename}`;
  collectAnswer(section);
}

async function submitTest(event, timedOut = false) {
  event.preventDefault();
  const button = event.currentTarget.querySelector('.submit-button');
  if (!button || button.disabled) return;
  getSoundContext();
  clearInterval(timerInterval);
  document.querySelectorAll('.question').forEach(collectAnswer);
  button.disabled = true;
  if (timedOut) button.textContent = 'Время вышло, проверяем...';
  try {
    const response = await fetch('/api/submit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ difficulty: selectedDifficulty, subject: selectedSubject, answers: [...answers.values()] }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Не удалось проверить тест');
    const review = result.results.map((item, index) => {
      const correct = item.is_written ? item.reference_answer : item.correct_answers.join(', ');
      return `<article class="review-item ${item.is_correct ? 'is-correct' : 'is-wrong'}">
        <p class="review-title">${index + 1}. ${escapeHtml(item.question_title)} <strong>${item.is_correct ? 'Верно' : 'Проверьте'}</strong></p>
        <p>Ваш ответ: <span>${escapeHtml(item.your_answer)}</span></p>
        <p>Правильный ответ: <span>${escapeHtml(correct || 'Будет проверено преподавателем')}</span></p>
      </article>`;
    }).join('');
    const correctCount = Number(result.correct_count ?? result.results.filter((item) => item.is_correct).length);
    const incorrectCount = Number(result.incorrect_count ?? result.results.length - correctCount);
    const questionCount = correctCount + incorrectCount || 1;
    const correctPercent = result.correct_percent ?? (correctCount / questionCount * 100).toFixed(1);
    const incorrectPercent = result.incorrect_percent ?? (incorrectCount / questionCount * 100).toFixed(1);
    const scorePercent = result.score_percent ?? 0;
    playUiSound(result.grade >= 4 ? 'success' : 'result');
    const passed = correctCount > 18;
    const currentArticle = findTheoryArticle(selectedSubject);
    const currentArticleIndex = currentArticle
      ? THEORY_ARTICLES.findIndex((article) => article.id === currentArticle.id)
      : -1;
    const nextArticle = currentArticleIndex >= 0
      ? THEORY_ARTICLES[currentArticleIndex + 1]
      : null;
    document.querySelector('#test-form').insertAdjacentHTML('afterend', `<div class="result">
      <h2>Результат: <strong>${result.score} / ${result.max_score}</strong></h2>
      <p class="result-grade">Оценка: <strong>${result.grade} · ${escapeHtml(result.grade_label)}</strong>${result.written_pending ? ' (предварительная, без проверки письменного ответа)' : ''}</p>
      <p>${result.written_pending ? 'Письменное задание сохранено и ожидает ручной проверки.' : 'Тест проверен.'}</p>
      <dl class="result-breakdown" aria-label="Показатели результата">
        <div class="result-breakdown-row stats-correct"><dt>Правильные ответы</dt><dd><strong>${correctCount}</strong><span>${correctPercent}%</span></dd></div>
        <div class="result-breakdown-row stats-incorrect"><dt>Неправильные ответы</dt><dd><strong>${incorrectCount}</strong><span>${incorrectPercent}%</span></dd></div>
        <div class="result-breakdown-row"><dt>Результат по баллам</dt><dd><strong>${result.score} из ${result.max_score}</strong><span>${scorePercent}%</span></dd></div>
      </dl>
      <div class="review"><h3>Ответы для самопроверки</h3>${review}</div>
      <div class="result-actions">
        <button class="submit-button" id="result-next-button" type="button">
          ${passed ? 'Далее' : 'Повторить теорию'} <span class="button-arrow" aria-hidden="true">→</span>
        </button>
      </div>
    </div>`);
    document.querySelector('#result-next-button').addEventListener('click', () => {
      if (passed && nextArticle) {
        navigateApp(
          { view: 'article', articleId: nextArticle.id },
          () => renderTheoryArticle(nextArticle),
        );
      } else if (!passed && currentArticle) {
        navigateApp(
          { view: 'article', articleId: currentArticle.id },
          () => renderTheoryArticle(currentArticle),
        );
      } else {
        navigateApp({ view: 'theory' }, loadTheoryIndex);
      }
    });
    button.remove();
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (error) {
    button.disabled = false;
    app.insertAdjacentHTML('afterbegin', `<p class="error">${escapeHtml(error.message)}</p>`);
  }
}

renderLoadingSkeleton('workspace');
setAppRoute({ view: 'tests', subject: 'python' }, true);
fetch('/api/auth/me').then(async (response) => {
  if (!response.ok) {
    setAppRoute({ view: 'auth', mode: 'login' }, true);
    renderAuth();
    return;
  }
  currentUser = await response.json();
  loadDifficulties('python');
}).catch(() => { app.innerHTML = '<p class="error">Не удалось подключиться к серверу.</p>'; });
