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

const escapeHtml = (value = '') => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[char]);

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
  app.innerHTML = `<p class="eyebrow">${escapeHtml(testData.subject_label || 'Python')} / практика</p>
    <h1>${escapeHtml(testData.title)}</h1>
    <p class="lead">${escapeHtml(testData.subtitle)}. Письменное задание можно решить прямо здесь или передать готовым файлом.</p>
    <div class="meta"><span>${testData.question_count} заданий в попытке</span>${testData.is_topic_test ? '' : `<span>уровень: ${escapeHtml(testData.difficulty_label || testData.difficulty)}</span>`}<span>случайная выборка</span><span class="timer" id="timer">20:00</span></div>
    <section class="test-progress" aria-live="polite">
      <div><strong id="progress-count">0 / ${testData.question_count}</strong><span>заданий с ответом</span></div>
      <progress id="test-progress-bar" max="${testData.question_count}" value="0" aria-label="Прогресс выполнения теста"></progress>
    </section>
    <form id="test-form">${testData.questions.map(questionTemplate).join('')}
      <button class="submit-button" type="submit">Проверить тест →</button>
    </form>`;

  document.querySelectorAll('.question').forEach((section) => {
    section.querySelectorAll('input[type="radio"], input[type="checkbox"]').forEach((input) => input.addEventListener('change', () => collectAnswer(section)));
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

function renderDifficultyCards(difficulties, subject) {
  return difficulties.map((difficulty) => `
    <button class="difficulty-card" data-difficulty="${difficulty.id}" data-subject="${subject}" ${difficulty.question_count < 20 ? 'disabled' : ''}>
      <span class="difficulty-label">${escapeHtml(difficulty.label)}</span>
      <strong>${difficulty.question_count}</strong>
      <small>уникальных вопросов</small>
      <span class="start-label">${difficulty.question_count < 20 ? 'Недоступно' : 'Начать тест →'}</span>
    </button>`).join('');
}

function renderPythonBasicsTopics() {
  const topics = [
    ['py_conditions', 'Условный оператор', 'if, elif, else и логические условия'],
    ['py_strings', 'Строки', 'Индексы, срезы и строковые методы'],
    ['py_data', 'Массивы и структуры данных', 'Списки, кортежи, множества и словари'],
    ['py_loops', 'Циклы', 'for, while, range, break и continue'],
    ['py_oop', 'ООП', 'Классы, объекты, методы и наследование'],
    ['py_functions', 'Функции', 'Аргументы, параметры и возвращаемые значения'],
  ];
  return topics.map(([subject, title, description]) => `
    <button class="topic-card" data-subject="${subject}">
      <span class="difficulty-label">ТЕСТ ПО ТЕМЕ</span>
      <strong>${title}</strong>
      <small>${description}</small>
      <span class="start-label">Начать тест →</span>
    </button>`).join('');
}

function renderDifficultySelection(pythonDifficulties, fastapiDifficulties, aiogramDifficulties, threadingDifficulties, multiprocessingDifficulties, asyncioDifficulties, generatorDifficulties, decoratorDifficulties, regexDifficulties, pandasDifficulties, unittestDifficulties, pytestDifficulties, linterDifficulties, filesFormatsDifficulties, databasesDifficulties) {
  app.innerHTML = `<p class="eyebrow">Python Test Lab / практика</p>
    <div class="account-bar"><span>Вы вошли как <strong>${escapeHtml(currentUser.username)}</strong></span><button class="tool-button" id="logout-button">Выйти</button></div>
    ${renderWorkspaceTabs('tests')}
    <h1>Выберите тему и сложность</h1>
    <section class="difficulty-section">
      <h2>Python</h2>
      <div class="difficulty-grid">${renderDifficultyCards(pythonDifficulties, 'python')}</div>
    </section>
    <section class="difficulty-section">
      <h2>FastAPI</h2>
      <div class="difficulty-grid">${renderDifficultyCards(fastapiDifficulties, 'fastapi')}</div>
    </section>
    <section class="difficulty-section">
      <h2>Создание ботов на aiogram</h2>
      <div class="difficulty-grid">${renderDifficultyCards(aiogramDifficulties, 'aiogram')}</div>
    </section>`;
  app.querySelector('.difficulty-section:last-child').insertAdjacentHTML('afterend', `
    <section class="difficulty-section">
      <h2>Многопоточность</h2>
      <div class="difficulty-grid">${renderDifficultyCards(threadingDifficulties, 'threading')}</div>
    </section>
    <section class="difficulty-section">
      <h2>Многопроцессорность</h2>
      <div class="difficulty-grid">${renderDifficultyCards(multiprocessingDifficulties, 'multiprocessing')}</div>
    </section>
    <section class="difficulty-section">
      <h2>Асинхронность</h2>
      <div class="difficulty-grid">${renderDifficultyCards(asyncioDifficulties, 'asyncio')}</div>
    </section>
    <section class="difficulty-section">
      <h2>Генератор</h2>
      <div class="difficulty-grid">${renderDifficultyCards(generatorDifficulties, 'generator')}</div>
    </section>
    <section class="difficulty-section">
      <h2>Декоратор</h2>
      <div class="difficulty-grid">${renderDifficultyCards(decoratorDifficulties, 'decorator')}</div>
    </section>
    <section class="difficulty-section">
      <h2>Регулярные выражения</h2>
      <div class="difficulty-grid">${renderDifficultyCards(regexDifficulties, 'regex')}</div>
    </section>
    <section class="difficulty-section">
      <h2>Pandas</h2>
      <div class="difficulty-grid">${renderDifficultyCards(pandasDifficulties, 'pandas')}</div>
    </section>
    <section class="difficulty-section">
      <h2>unittest</h2>
      <div class="difficulty-grid">${renderDifficultyCards(unittestDifficulties, 'unittest')}</div>
    </section>
    <section class="difficulty-section">
      <h2>pytest</h2>
      <div class="difficulty-grid">${renderDifficultyCards(pytestDifficulties, 'pytest')}</div>
    </section>
    <section class="difficulty-section">
      <h2>Линтеры</h2>
      <div class="difficulty-grid">${renderDifficultyCards(linterDifficulties, 'linters')}</div>
    </section>
    <section class="difficulty-section">
      <h2>Работа с файлами и форматами</h2>
      <div class="difficulty-grid">${renderDifficultyCards(filesFormatsDifficulties, 'files_formats')}</div>
    </section>
    <section class="difficulty-section">
      <h2>Базы данных: PostgreSQL, SQLite и MySQL</h2>
      <div class="difficulty-grid">${renderDifficultyCards(databasesDifficulties, 'databases')}</div>
    </section>`);
  app.querySelector('.difficulty-section').insertAdjacentHTML('afterend', `
    <section class="difficulty-section">
      <h2>Основы Python</h2>
      <p class="lead">Условия, строки, списки и структуры данных, циклы, ООП и функции.</p>
      <div class="difficulty-grid topic-grid">${renderPythonBasicsTopics()}</div>
    </section>`);
  document.querySelectorAll('.difficulty-card:not([disabled])').forEach((button) => {
    button.addEventListener('click', () => {
      selectedSubject = button.dataset.subject;
      loadTest(button.dataset.difficulty, selectedSubject);
    });
  });
  document.querySelectorAll('.topic-card').forEach((button) => {
    button.addEventListener('click', () => {
      selectedSubject = button.dataset.subject;
      loadTest('medium', selectedSubject);
    });
  });
  bindWorkspaceTabs();
}

function renderWorkspaceTabs(activeView) {
  return `<nav class="view-tabs" aria-label="Разделы">
    <button class="view-tab ${activeView === 'tests' ? 'is-active' : ''}" data-view="tests" aria-pressed="${activeView === 'tests'}">Тесты</button>
    <button class="view-tab ${activeView === 'theory' ? 'is-active' : ''}" data-view="theory" aria-pressed="${activeView === 'theory'}">Теория</button>
    <button class="view-tab ${activeView === 'statistics' ? 'is-active' : ''}" data-view="statistics" aria-pressed="${activeView === 'statistics'}">Статистика</button>
  </nav>`;
}

function bindWorkspaceTabs() {
  document.querySelectorAll('.view-tab').forEach((button) => {
    button.addEventListener('click', () => {
      if (button.dataset.view === 'theory') loadTheoryIndex();
      else if (button.dataset.view === 'statistics') loadStatistics();
      else loadDifficulties('python');
    });
  });
  document.querySelector('#logout-button').addEventListener('click', async () => {
    await fetch('/api/auth/logout', { method: 'POST' });
    currentUser = null;
    renderAuth();
  });
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
  app.innerHTML = `<p class="eyebrow">Python Test Lab / материалы</p>
    <div class="account-bar"><span>Вы вошли как <strong>${escapeHtml(currentUser.username)}</strong></span><button class="tool-button" id="logout-button">Выйти</button></div>
    ${renderWorkspaceTabs('theory')}
    <h1>Теория</h1>
    <p class="lead">Краткие статьи помогут повторить ключевые понятия перед тестированием. Выберите тему, чтобы открыть материал.</p>
    <p class="progress-summary">Изучено материалов: <strong>${statisticsData.completed_material_count} / ${statisticsData.total_material_count}</strong></p>
    <div class="theory-grid">${THEORY_ARTICLES.map((article) => `
      <button class="theory-card ${completedArticles.has(article.id) ? 'is-completed' : ''}" data-article="${escapeHtml(article.id)}">
        <span class="difficulty-label">${escapeHtml(article.category)}</span>
        <strong>${escapeHtml(article.title)}</strong>
        <small>${escapeHtml(article.summary)}</small>
        <span class="start-label">${completedArticles.has(article.id) ? '✓ Изучено · Читать снова →' : 'Читать статью →'}</span>
      </button>`).join('')}
    </div>`;
  bindWorkspaceTabs();
  document.querySelectorAll('.theory-card').forEach((button) => {
    button.addEventListener('click', () => {
      const article = THEORY_ARTICLES.find((item) => item.id === button.dataset.article);
      if (article) renderTheoryArticle(article);
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
    <div class="account-bar"><span>Вы вошли как <strong>${escapeHtml(currentUser.username)}</strong></span><button class="tool-button" id="logout-button">Выйти</button></div>
    ${renderWorkspaceTabs('theory')}
    <button class="article-back" id="theory-back">← Все статьи</button>
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
        <button class="submit-button" id="article-test-button">Перейти к тесту →</button>
      </div>
    </article>`;
  bindWorkspaceTabs();
  document.querySelector('#theory-back').addEventListener('click', renderTheoryIndex);
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
  document.querySelector('#article-test-button').addEventListener('click', () => loadTest('medium', article.subject));
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
    <div class="account-bar"><span>Вы вошли как <strong>${escapeHtml(currentUser.username)}</strong></span><button class="tool-button" id="logout-button">Выйти</button></div>
    ${renderWorkspaceTabs('statistics')}
    <h1>Ваша статистика</h1>
    <p class="lead">История изученных материалов и результаты каждой попытки сохраняются в аккаунте.</p>
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
    <button class="auth-switch" id="auth-switch">${register ? 'Уже есть аккаунт? Войти' : 'Нет аккаунта? Зарегистрироваться'}</button>`;
  document.querySelector('#auth-switch').addEventListener('click', () => renderAuth(register ? 'login' : 'register'));
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
    if (succeeded) loadDifficulties('python');
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
    const response = await fetch(`/api/test?difficulty=${encodeURIComponent(difficulty)}&subject=${encodeURIComponent(subject)}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Не удалось загрузить тест этого уровня');
    testData = result;
    renderTest();
  } catch (error) {
    app.innerHTML = `<p class="error">${escapeHtml(error.message || 'Сервер недоступен')}</p><button class="auth-switch" id="retry-button">Вернуться к уровням</button>`;
    document.querySelector('#retry-button').addEventListener('click', () => loadDifficulties('python'));
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
    document.querySelector('#test-form').insertAdjacentHTML('afterend', `<div class="result">
      <h2>Результат: <strong>${result.score} / ${result.max_score}</strong></h2>
      <p class="result-grade">Оценка: <strong>${result.grade} · ${escapeHtml(result.grade_label)}</strong>${result.written_pending ? ' (предварительная, без проверки письменного ответа)' : ''}</p>
      <p>${result.written_pending ? 'Письменное задание сохранено и ожидает ручной проверки.' : 'Тест проверен.'}</p>
      <table class="stats-table">
        <thead><tr><th>Показатель</th><th>Количество</th><th>Процент</th></tr></thead>
        <tbody>
          <tr class="stats-correct"><td>Правильные ответы</td><td>${correctCount}</td><td>${correctPercent}%</td></tr>
          <tr class="stats-incorrect"><td>Неправильные ответы</td><td>${incorrectCount}</td><td>${incorrectPercent}%</td></tr>
          <tr><td>Результат по баллам</td><td>${result.score} из ${result.max_score}</td><td>${scorePercent}%</td></tr>
        </tbody>
      </table>
      <div class="review"><h3>Ответы для самопроверки</h3>${review}</div>
    </div>`);
    button.remove();
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (error) {
    button.disabled = false;
    app.insertAdjacentHTML('afterbegin', `<p class="error">${escapeHtml(error.message)}</p>`);
  }
}

renderLoadingSkeleton('workspace');
fetch('/api/auth/me').then(async (response) => {
  if (!response.ok) { renderAuth(); return; }
  currentUser = await response.json();
  loadDifficulties('python');
}).catch(() => { app.innerHTML = '<p class="error">Не удалось подключиться к серверу.</p>'; });
