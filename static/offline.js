(() => {
  if (location.protocol !== 'file:') return;

  window.OFFLINE_MODE = true;
  const STORAGE_KEY = 'python-test-lab-offline-data';
  const DIFFICULTIES = [
    { id: 'easy', label: 'Легкий' },
    { id: 'medium', label: 'Средний' },
    { id: 'hard', label: 'Сложный' },
    { id: 'advanced', label: 'Углубленный' },
    { id: 'expert', label: 'Эксперт' },
  ];
  const FASTAPI_DIFFICULTIES = [...DIFFICULTIES, { id: 'master', label: 'Мастер' }];
  const SUBJECTS = {
    python: 'Python',
    python_bases: 'Основы Python',
    py_conditions: 'Условный оператор',
    py_strings: 'Строки',
    py_data: 'Массивы и структуры данных',
    py_loops: 'Циклы',
    py_oop: 'ООП',
    py_functions: 'Функции',
    fastapi: 'FastAPI',
    aiogram: 'Создание ботов на aiogram',
    threading: 'Многопоточность',
    multiprocessing: 'Многопроцессорность',
    asyncio: 'Асинхронность',
    generator: 'Генератор',
    decorator: 'Декоратор',
    regex: 'Регулярные выражения',
    pandas: 'Pandas',
    unittest: 'unittest',
    pytest: 'pytest',
    linters: 'Линтеры',
    files_formats: 'Работа с файлами и форматами',
    databases: 'Базы данных: PostgreSQL, SQLite и MySQL',
  };
  const TOPIC_SUBJECTS = new Set([
    'py_conditions', 'py_strings', 'py_data', 'py_loops', 'py_oop', 'py_functions',
  ]);
  const ARTICLE_IDS = [
    'python', 'py_conditions', 'py_strings', 'py_data', 'py_loops', 'py_oop',
    'py_functions', 'fastapi', 'aiogram', 'threading', 'multiprocessing',
    'asyncio', 'generator', 'decorator', 'regex', 'pandas', 'unittest', 'pytest',
    'linters', 'files_formats', 'databases',
  ];
  const GRADE_LABELS = {
    2: 'Нужно повторить',
    3: 'Удовлетворительно',
    4: 'Хорошо',
    5: 'Отлично',
  };
  let storageError;
  let store;

  try {
    store = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null') || {
      attempts: [],
      completedArticleIds: [],
    };
  } catch (error) {
    storageError = error;
    store = { attempts: [], completedArticleIds: [] };
  }

  const saveStore = () => {
    if (storageError) throw new Error(`Не удалось открыть локальное хранилище: ${storageError.message}`);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  };

  const response = (data, status = 200) => new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });

  const gradeForScore = (score, maxScore) => {
    const percentage = maxScore ? score / maxScore * 100 : 0;
    if (percentage >= 85) return 5;
    if (percentage >= 70) return 4;
    if (percentage >= 50) return 3;
    return 2;
  };

  const getQuestions = () => {
    if (!Array.isArray(window.OFFLINE_QUESTION_BANK)) {
      throw new Error('В APK не найден встроенный банк вопросов.');
    }
    return window.OFFLINE_QUESTION_BANK;
  };

  const getStatistics = () => {
    const attempts = store.attempts.slice(0, 30);
    const scoredAttempts = store.attempts.filter((attempt) => attempt.max_score);
    return {
      completed_article_ids: store.completedArticleIds,
      completed_material_count: store.completedArticleIds.length,
      total_material_count: ARTICLE_IDS.length,
      completed_test_count: store.attempts.length,
      average_score_percent: scoredAttempts.length
        ? Number((scoredAttempts.reduce((sum, attempt) => sum + attempt.score_percent, 0) / scoredAttempts.length).toFixed(1))
        : 0,
      attempts,
    };
  };

  const readBody = async (input, init) => {
    if (init?.body != null) {
      if (typeof init.body === 'string') return JSON.parse(init.body);
      if (init.body instanceof FormData) return init.body;
    }
    if (input instanceof Request) {
      const contentType = input.headers.get('content-type') || '';
      return contentType.includes('application/json') ? input.clone().json() : input.clone().formData();
    }
    return {};
  };

  const handleRequest = async (url, input, init) => {
    const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const path = url.pathname;
    const body = method === 'GET' ? {} : await readBody(input, init);

    if (storageError) return response({ detail: `Не удалось открыть локальные данные: ${storageError.message}` }, 500);
    if (path === '/api/auth/me') {
      return response({ id: 1, username: 'Локальный профиль', email: 'offline@device.local' });
    }
    if (path === '/api/auth/logout') return response({ ok: true });
    if (path === '/api/auth/login' || path === '/api/auth/register') {
      return response({ id: 1, username: 'Локальный профиль', email: 'offline@device.local' });
    }
    if (path === '/api/difficulties/batch' || path === '/api/difficulties') {
      const params = new URLSearchParams(url.search);
      const subjects = path.endsWith('/batch') ? params.getAll('subjects') : [params.get('subject') || 'python'];
      const questions = getQuestions();
      const difficulties = {};
      for (const subject of subjects) {
        const available = subject === 'fastapi' ? FASTAPI_DIFFICULTIES : DIFFICULTIES;
        difficulties[subject] = available.map((difficulty) => ({
          ...difficulty,
          question_count: questions.filter((question) => (
            question.subject === subject && question.difficulty === difficulty.id
          )).length,
        }));
      }
      return response(path.endsWith('/batch') ? { difficulties } : { difficulties: difficulties[subjects[0]] });
    }
    if (path === '/api/test') {
      const subject = url.searchParams.get('subject') || 'python';
      const difficulty = url.searchParams.get('difficulty') || 'medium';
      const available = subject === 'fastapi' ? FASTAPI_DIFFICULTIES : DIFFICULTIES;
      if (!available.some((item) => item.id === difficulty)) {
        return response({ detail: 'Этот уровень недоступен для выбранной темы' }, 400);
      }
      const candidates = getQuestions().filter((question) => (
        question.subject === subject && question.difficulty === difficulty
      ));
      if (candidates.length < 20) {
        return response({ detail: 'Для этого уровня пока меньше 20 заданий' }, 400);
      }
      const shuffled = candidates.slice();
      for (let index = shuffled.length - 1; index > 0; index -= 1) {
        const swapIndex = Math.floor(Math.random() * (index + 1));
        [shuffled[index], shuffled[swapIndex]] = [shuffled[swapIndex], shuffled[index]];
      }
      const questions = shuffled.slice(0, 20).map((question) => ({
        ...question,
        options: question.options.map(({ id, label }) => ({ id, label })),
      }));
      const difficultyLabel = available.find((item) => item.id === difficulty).label;
      const subjectLabel = SUBJECTS[subject] || subject;
      return response({
        title: subject === 'python' ? 'Python foundations' : subjectLabel,
        subtitle: TOPIC_SUBJECTS.has(subject)
          ? `Тест по теме «${subjectLabel}»`
          : `Уровень: ${difficultyLabel}. Проверьте знания ${subjectLabel} и объясните ход решения`,
        difficulty,
        difficulty_label: difficultyLabel,
        is_topic_test: TOPIC_SUBJECTS.has(subject),
        subject,
        subject_label: subjectLabel,
        question_count: questions.length,
        questions,
      });
    }
    if (path === '/api/submit') {
      const answers = Array.isArray(body.answers) ? body.answers : [];
      if (answers.length !== 20 || new Set(answers.map((answer) => answer.question_id)).size !== 20) {
        return response({ detail: 'В каждой попытке должно быть 20 заданий' }, 400);
      }
      const answerById = new Map(answers.map((answer) => [Number(answer.question_id), answer]));
      const questions = getQuestions().filter((question) => (
        question.subject === body.subject
        && question.difficulty === body.difficulty
        && answerById.has(question.id)
      ));
      if (questions.length !== 20) return response({ detail: 'Некоторые задания не найдены' }, 400);

      let score = 0;
      const maxScore = questions.reduce((sum, question) => sum + question.points, 0);
      const results = questions.map((question) => {
        const submitted = answerById.get(question.id);
        const selectedIds = [...new Set(submitted.selected_option_ids || [])].map(Number).sort((a, b) => a - b);
        const correctIds = question.options.filter((option) => option.is_correct).map((option) => option.id).sort((a, b) => a - b);
        const isWritten = question.question_type === 'written';
        const isCorrect = isWritten
          ? Boolean((submitted.answer_text || '').trim())
          : JSON.stringify(selectedIds) === JSON.stringify(correctIds);
        const earnedPoints = isWritten || !isCorrect ? 0 : question.points;
        score += earnedPoints;
        return {
          question_id: question.id,
          is_correct: isCorrect,
          earned_points: earnedPoints,
          max_points: question.points,
          is_written: isWritten,
          question_title: question.title,
          correct_answers: question.options.filter((option) => option.is_correct).map((option) => option.label),
          reference_answer: question.reference_answer,
          your_answer: submitted.answer_text || question.options
            .filter((option) => selectedIds.includes(option.id))
            .map((option) => option.label)
            .join(', ') || 'Нет ответа',
        };
      });
      const grade = gradeForScore(score, maxScore);
      const correctCount = results.filter((item) => item.is_correct).length;
      const incorrectCount = results.length - correctCount;
      const result = {
        submission_id: Date.now(),
        score,
        max_score: maxScore,
        correct_count: correctCount,
        incorrect_count: incorrectCount,
        correct_percent: Number((correctCount / results.length * 100).toFixed(1)),
        incorrect_percent: Number((incorrectCount / results.length * 100).toFixed(1)),
        score_percent: maxScore ? Number((score / maxScore * 100).toFixed(1)) : 0,
        grade,
        grade_label: GRADE_LABELS[grade],
        written_pending: results.some((item) => item.is_written),
        results,
      };
      store.attempts.unshift({
        id: result.submission_id,
        subject: body.subject,
        subject_label: SUBJECTS[body.subject] || body.subject,
        difficulty: body.difficulty,
        difficulty_label: TOPIC_SUBJECTS.has(body.subject)
          ? 'Тематический тест'
          : (DIFFICULTIES.find((item) => item.id === body.difficulty)
            || FASTAPI_DIFFICULTIES.find((item) => item.id === body.difficulty))?.label || body.difficulty,
        score,
        max_score: maxScore,
        score_percent: result.score_percent,
        grade,
        grade_label: GRADE_LABELS[grade],
        written_pending: result.written_pending,
        created_at: new Date().toISOString(),
      });
      saveStore();
      return response(result);
    }
    if (path === '/api/statistics') return response(getStatistics());
    if (path === '/api/statistics/materials') {
      if (!ARTICLE_IDS.includes(body.article_id)) return response({ detail: 'Материал не найден' }, 404);
      if (!store.completedArticleIds.includes(body.article_id)) {
        store.completedArticleIds.push(body.article_id);
        saveStore();
      }
      return response({ article_id: body.article_id, completed: true });
    }
    if (path === '/api/submit-file') {
      const file = body.get('file');
      if (!file || !file.name.toLowerCase().endsWith('.py')) {
        return response({ detail: 'Загрузите файл с расширением .py' }, 400);
      }
      if (file.size > 100_000) return response({ detail: 'Файл не должен быть больше 100 КБ' }, 400);
      try {
        return response({
          question_id: Number(body.get('question_id')),
          filename: file.name,
          content: await file.text(),
        });
      } catch {
        return response({ detail: 'Не удалось прочитать файл. Используйте кодировку UTF-8.' }, 400);
      }
    }
    return response({ detail: 'Локальный API не поддерживает этот запрос' }, 404);
  };

  window.fetch = async (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (!url.pathname.startsWith('/api/')) return response({ detail: 'Сетевые запросы недоступны в офлайн-режиме' }, 503);
    try {
      return await handleRequest(url, input, init);
    } catch (error) {
      return response({ detail: error.message || 'Ошибка локального хранилища' }, 500);
    }
  };
})();
