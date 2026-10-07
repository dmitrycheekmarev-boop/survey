/* =========================================================================
 *  static/app.js — клиентский интерфейс «Конструктор опросников»
 *  ------------------------------------------------------------------------
 *  SPA на vanilla JS (без сборщиков и фреймворков).
 *
 *  Роли:
 *    • Методист (role: methodologist | admin)
 *        - Конструктор тестов: создание опросника, добавление вопросов
 *          и вариантов ответа.
 *        - Последовательности тестов: создание последовательности
 *          (POST /api/sequences) и назначение её студентам (…/assign).
 *    • Студент (role: student)
 *        - Просмотр назначений (GET /api/my/assignments).
 *        - Прохождение теста: старт попытки, выбор ответов, завершение
 *          и просмотр результата.
 *
 *  Ожидаемый контракт API (строки заданы в ENDPOINTS — при необходимости
 *  правьте только их):
 *
 *    POST /api/login                     {username,password} -> user
 *    POST /api/logout                                         -> 204
 *    GET  /api/me                                             -> user
 *    GET  /api/users?role=student                             -> [user]
 *
 *    GET  /api/questionnaires                                 -> [questionnaire]
 *    POST /api/questionnaires            {title,description}  -> questionnaire
 *    GET  /api/questionnaires/{id}                            -> questionnaire(+questions[+options])
 *    DELETE /api/questionnaires/{id}                          -> 204
 *    POST /api/questionnaires/{id}/questions  {text,question_type} -> question
 *    DELETE /api/questions/{id}                               -> 204
 *    POST /api/questions/{id}/options    {text,is_correct}    -> option
 *    DELETE /api/options/{id}                                 -> 204
 *
 *    GET  /api/sequences                                      -> [sequence]
 *    POST /api/sequences   {title,description,items:[{questionnaire_id,order}]} -> sequence
 *    GET  /api/sequences/{id}                                 -> sequence(+items)
 *    DELETE /api/sequences/{id}                               -> 204
 *    POST /api/sequences/{id}/assign      {user_ids:[],start_at,end_at} -> ok
 *
 *    GET  /api/my/assignments                                 -> [assignment]
 *    POST /api/attempts                   {assignment_id}     -> attempt(+questions)
 *    GET  /api/attempts/{id}                                  -> attempt
 *    POST /api/attempts/{id}/answer       {question_id,option_ids,text} -> ok
 *    POST /api/attempts/{id}/finish                           -> result
 *    GET  /api/attempts/{id}/result                           -> result
 * ========================================================================= */
(function () {
  'use strict';

  /* ---------------------------------------------------------------------- *
   *  Конфигурация эндпоинтов
   * ---------------------------------------------------------------------- */
  const ENDPOINTS = {
    login: '/api/login',
    logout: '/api/logout',
    me: '/api/me',
    users: (q) => `/api/users${q ? '?' + q : ''}`,

    questionnaires: '/api/questionnaires',
    questionnaire: (id) => `/api/questionnaires/${id}`,
    questions: (id) => `/api/questionnaires/${id}/questions`,
    question: (id) => `/api/questions/${id}`,
    options: (qid) => `/api/questions/${qid}/options`,
    option: (id) => `/api/options/${id}`,

    sequences: '/api/sequences',
    sequence: (id) => `/api/sequences/${id}`,
    assign: (id) => `/api/sequences/${id}/assign`,

    myAssignments: '/api/my/assignments',
    startAttempt: '/api/attempts',
    attempt: (id) => `/api/attempts/${id}`,
    answer: (id) => `/api/attempts/${id}/answer`,
    finish: (id) => `/api/attempts/${id}/finish`,
    result: (id) => `/api/attempts/${id}/result`,
  };

  /* ---------------------------------------------------------------------- *
   *  Состояние приложения
   * ---------------------------------------------------------------------- */
  const state = {
    user: null,            // текущий пользователь
    view: 'login',         // текущий экран
    params: {},            // параметры экрана (id, attempt и т.п.)
    data: {},              // кэш загруженных списков
    answers: {},           // ответы текущей попытки { [questionId]: {option_ids, text} }
    saving: {},            // таймеры автосохранения
  };

  /* ---------------------------------------------------------------------- *
   *  Универсальные хелперы
   * ---------------------------------------------------------------------- */
  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

  function esc(v) {
    if (v === null || v === undefined) return '';
    return String(v)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function fmtDate(v) {
    if (!v) return '—';
    const d = new Date(v);
    if (isNaN(d.getTime())) return esc(v);
    return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  function toIso(v) {
    if (!v) return null;
    const d = new Date(v);
    return isNaN(d.getTime()) ? v : d.toISOString();
  }

  /** Нормализация типа вопроса -> single | multiple | text */
  function normType(t) {
    t = String(t || '').toLowerCase();
    if (t.includes('multi') || t.includes('checkbox')) return 'multiple';
    if (t.includes('text') || t.includes('open') || t.includes('free') || t.includes('textarea')) return 'text';
    return 'single';
  }

  const TYPE_LABEL = { single: 'Один вариант', multiple: 'Несколько вариантов', text: 'Свободный ответ' };

  function spinner() { return `<div class="p-6 text-center text-slate-500">Загрузка…</div>`; }
  function errorBox(msg) { return `<div class="p-4 rounded-lg bg-red-50 text-red-700 border border-red-200 text-sm">${esc(msg)}</div>`; }
  function emptyBox(msg) { return `<div class="p-8 text-center text-slate-500 border border-dashed border-slate-300 rounded-xl">${esc(msg)}</div>`; }

  /* ---------------------------------------------------------------------- *
   *  Уведомления (toast)
   * ---------------------------------------------------------------------- */
  function toastHost() {
    let host = document.getElementById('toast-host');
    if (!host) {
      host = document.createElement('div');
      host.id = 'toast-host';
      host.className = 'fixed top-4 right-4 space-y-2 z-50 max-w-xs';
      document.body.appendChild(host);
    }
    return host;
  }
  function toast(msg, type = 'info') {
    const colors = { info: 'bg-slate-800', success: 'bg-emerald-600', error: 'bg-red-600' };
    const el = document.createElement('div');
    el.className = `${colors[type] || colors.info} text-white px-4 py-2 rounded-lg shadow-lg text-sm`;
    el.textContent = msg;
    toastHost().appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity .3s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 300);
    }, 3000);
  }

  /* ---------------------------------------------------------------------- *
   *  API-клиент
   * ---------------------------------------------------------------------- */
  async function api(path, { method = 'GET', body } = {}) {
    const init = { method, headers: { Accept: 'application/json' }, credentials: 'same-origin' };
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }

    let res;
    try {
      res = await fetch(path, init);
    } catch (e) {
      throw new Error('Нет соединения с сервером');
    }

    if (res.status === 204) return null;

    const ct = res.headers.get('content-type') || '';
    let data = null;
    try {
      data = ct.includes('application/json') ? await res.json() : await res.text();
    } catch (_) { data = null; }

    if (!res.ok) {
      if (res.status === 401 && state.view !== 'login') {
        state.user = null;
        navigate('login');
      }
      let msg = (data && (data.detail || data.message || data.error)) || res.statusText;
      if (Array.isArray(msg)) msg = msg.map((m) => m.msg || JSON.stringify(m)).join('; ');
      throw new Error(typeof msg === 'string' ? msg : 'Ошибка запроса');
    }
    return data;
  }

  /* ---------------------------------------------------------------------- *
   *  Навигация / рендер
   * ---------------------------------------------------------------------- */
  function navigate(view, params = {}) {
    state.view = view;
    state.params = params || {};
    if (view !== 'attempt') state.answers = {};
    render();
    window.scrollTo(0, 0);
  }

  function roleLabel(role) {
    return { methodologist: 'методист', admin: 'администратор', student: 'студент' }[role] || role || '';
  }
  function isStudent() { return state.user && state.user.role === 'student'; }

  function layout(content) {
    return `
      <div class="min-h-screen bg-slate-100 text-slate-800">
        ${renderHeader()}
        <main class="max-w-5xl mx-auto p-4">${content}</main>
      </div>`;
  }

  function renderHeader() {
    const u = state.user || {};
    const link = (view, label) => {
      const active = state.view === view;
      const cls = active
        ? 'bg-indigo-600 text-white'
        : 'text-slate-600 hover:bg-slate-200';
      return `<button data-action="nav" data-view="${view}" class="px-3 py-1.5 rounded-lg text-sm ${cls}">${esc(label)}</button>`;
    };

    const nav = isStudent()
      ? link('my_assignments', 'Мои назначения')
      : link('questionnaires', 'Опросники') + link('sequences', 'Последовательности');

    return `
      <header class="bg-white border-b border-slate-200 shadow-sm">
        <div class="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
          <div class="font-semibold text-slate-800 whitespace-nowrap">📋 Конструктор опросников</div>
          <nav class="flex items-center gap-1 flex-1">${nav}</nav>
          <div class="flex items-center gap-3 text-sm">
            <span class="text-slate-600 hidden sm:inline">
              ${esc(u.full_name || u.username || '')}
              <span class="text-slate-400">(${esc(roleLabel(u.role))})</span>
            </span>
            <button data-action="logout" class="text-red-600 hover:underline">Выйти</button>
          </div>
        </div>
      </header>`;
  }

  function render() {
    const app = $('#app');
    if (!app) return;

    switch (state.view) {
      case 'login':
        app.innerHTML = viewLogin();
        bindLogin();
        break;
      case 'questionnaires':
        app.innerHTML = layout(viewQuestionnairesShell());
        bindQuestionnaires();
        loadQuestionnaires();
        break;
      case 'questionnaire':
        app.innerHTML = layout(viewQuestionnaireShell());
        loadQuestionnaire(state.params.id);
        break;
      case 'sequences':
        app.innerHTML = layout(viewSequencesShell());
        bindSequences();
        loadSequences();
        break;
      case 'sequence':
        app.innerHTML = layout(viewSequenceShell());
        loadSequence(state.params.id);
        break;
      case 'my_assignments':
        app.innerHTML = layout(viewAssignmentsShell());
        loadAssignments();
        break;
      case 'attempt':
        app.innerHTML = layout(viewAttemptShell());
        bindAttempt();
        renderAttempt();
        break;
      case 'result':
        app.innerHTML = layout(viewResultShell());
        loadResult(state.params.id);
        break;
      default:
        app.innerHTML = layout(emptyBox('Экран не найден'));
    }
  }

  /* ====================================================================== *
   *  ЭКРАН: ВХОД
   * ====================================================================== */
  function viewLogin() {
    return `
      <div class="min-h-screen flex items-center justify-center bg-slate-100 p-4">
        <form id="login-form" class="bg-white rounded-xl shadow p-6 w-full max-w-sm space-y-4">
          <h1 class="text-xl font-semibold text-center">Вход в систему</h1>
          <div>
            <label class="block text-sm mb-1">Логин</label>
            <input name="username" autocomplete="username" required
                   class="w-full border border-slate-300 rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-indigo-400" />
          </div>
          <div>
            <label class="block text-sm mb-1">Пароль</label>
            <input name="password" type="password" autocomplete="current-password" required
                   class="w-full border border-slate-300 rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-indigo-400" />
          </div>
          <button type="submit"
                  class="w-full bg-indigo-600 text-white rounded-lg py-2 hover:bg-indigo-700 transition">
            Войти
          </button>
          <p id="login-error" class="text-sm text-red-600 hidden text-center"></p>
        </form>
      </div>`;
  }

  function bindLogin() {
    const form = $('#login-form');
    if (!form) return;
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = $('#login-error');
      err.classList.add('hidden');
      const fd = new FormData(form);
      try {
        const user = await api(ENDPOINTS.login, {
          method: 'POST',
          body: { username: fd.get('username'), password: fd.get('password') },
        });
        state.user = user || (await api(ENDPOINTS.me));
        toast('Добро пожаловать!', 'success');
        navigate(isStudent() ? 'my_assignments' : 'questionnaires');
      } catch (ex) {
        err.textContent = ex.message;
        err.classList.remove('hidden');
      }
    });
  }

  /* ====================================================================== *
   *  МЕТОДИСТ: ОПРОСНИКИ (конструктор тестов)
   * ====================================================================== */
  function viewQuestionnairesShell() {
    return `
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold">Опросники</h2>
        <button id="toggle-new-q"
                class="bg-indigo-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-indigo-700 transition">
          + Создать опросник
        </button>
      </div>

      <form id="new-q-form" class="hidden bg-white rounded-xl shadow p-4 mb-4 space-y-3">
        <div>
          <label class="block text-sm mb-1">Название</label>
          <input name="title" required class="w-full border border-slate-300 rounded-lg px-3 py-2" />
        </div>
        <div>
          <label class="block text-sm mb-1">Описание</label>
          <textarea name="description" rows="2" class="w-full border border-slate-300 rounded-lg px-3 py-2"></textarea>
        </div>
        <button type="submit" class="bg-emerald-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-emerald-700 transition">
          Сохранить
        </button>
      </form>

      <div id="q-list">${spinner()}</div>`;
  }

  function bindQuestionnaires() {
    const toggle = $('#toggle-new-q');
    const form = $('#new-q-form');
    if (toggle) {
      toggle.addEventListener('click', () => {
        form.classList.toggle('hidden');
        if (!form.classList.contains('hidden')) form.querySelector('[name="title"]').focus();
      });
    }
    if (form) {
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        try {
          await api(ENDPOINTS.questionnaires, {
            method: 'POST',
            body: { title: fd.get('title'), description: fd.get('description') || null },
          });
          toast('Опросник создан', 'success');
          form.reset();
          form.classList.add('hidden');
          loadQuestionnaires();
        } catch (ex) { toast(ex.message, 'error'); }
      });
    }
  }

  async function loadQuestionnaires() {
    const box = $('#q-list');
    if (!box) return;
    box.innerHTML = spinner();
    try {
      const list = await api(ENDPOINTS.questionnaires);
      state.data.questionnaires = Array.isArray(list) ? list : (list && list.items) || [];
      box.innerHTML = renderQuestionnaireList(state.data.questionnaires);
    } catch (ex) {
      box.innerHTML = errorBox(ex.message);
    }
  }

  function renderQuestionnaireList(list) {
    if (!list.length) return emptyBox('Опросников пока нет. Создайте первый.');
    return `<div class="grid gap-3">${list.map((q) => `
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4 flex items-center justify-between gap-3">
        <div class="min-w-0">
          <button data-action="open-questionnaire" data-id="${q.id}"
                  class="font-medium text-indigo-700 hover:underline text-left truncate">
            ${esc(q.title)}
          </button>
          ${q.description ? `<p class="text-sm text-slate-500 truncate">${esc(q.description)}</p>` : ''}
          <p class="text-xs text-slate-400 mt-1">
            Вопросов: ${esc(q.questions_count ?? q.questions?.length ?? 0)}
          </p>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button data-action="open-questionnaire" data-id="${q.id}"
                  class="text-sm px-3 py-1.5 rounded-lg border border-slate-300 hover:bg-slate-50">Открыть</button>
          <button data-action="delete-questionnaire" data-id="${q.id}"
                  class="text-sm px-2 py-1.5 rounded-lg text-red-600 hover:bg-red-50">Удалить</button>
        </div>
      </div>`).join('')}</div>`;
  }

  /* --- Детальный экран опросника (редактирование вопросов/вариантов) ---- */
  function viewQuestionnaireShell() {
    return `
      <button data-action="nav" data-view="questionnaires"
              class="text-sm text-slate-600 hover:underline mb-3">← К списку опросников</button>
      <div id="q-detail">${spinner()}</div>`;
  }

  async function loadQuestionnaire(id) {
    const box = $('#q-detail');
    if (!box) return;
    box.innerHTML = spinner();
    let q = state.data.currentQuestionnaire;
    if (!q || q.id !== id) {
      try {
        q = await api(ENDPOINTS.questionnaire(id));
        state.data.currentQuestionnaire = q;
      } catch (ex) {
        box.innerHTML = errorBox(ex.message);
        return;
      }
    }
    if (!q) { box.innerHTML = errorBox('Опросник не найден'); return; }
    box.innerHTML = renderQuestionnaireDetail(q);
    bindQuestionnaireDetail(id);
  }

  function renderQuestionnaireDetail(q) {
    const questions = q.questions || [];
    return `
      <div class="bg-white rounded-xl shadow p-4 mb-4">
        <h2 class="text-lg font-semibold">${esc(q.title)}</h2>
        ${q.description ? `<p class="text-sm text-slate-500 mt-1">${esc(q.description)}</p>` : ''}
      </div>

      <div class="space-y-3 mb-6" id="questions">
        ${questions.length
          ? questions.map((qq, i) => renderQuestionCard(qq, i)).join('')
          : emptyBox('Вопросов пока нет. Добавьте первый ниже.')}
      </div>

      <form id="add-question-form" class="bg-white rounded-xl shadow p-4 space-y-3">
        <h3 class="font-medium">Добавить вопрос</h3>
        <div>
          <label class="block text-sm mb-1">Текст вопроса</label>
          <textarea name="text" rows="2" required
                    class="w-full border border-slate-300 rounded-lg px-3 py-2"></textarea>
        </div>
        <div>
          <label class="block text-sm mb-1">Тип вопроса</label>
          <select name="question_type" class="border border-slate-300 rounded-lg px-3 py-2">
            <option value="single">Один вариант</option>
            <option value="multiple">Несколько вариантов</option>
            <option value="text">Свободный ответ</option>
          </select>
        </div>
        <button type="submit"
                class="bg-emerald-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-emerald-700 transition">
          Добавить вопрос
        </button>
      </form>`;
  }

  function renderQuestionCard(qq, index) {
    const type = normType(qq.question_type || qq.type);
    const options = qq.options || [];
    const optionsHtml = type === 'text'
      ? `<p class="text-sm text-slate-400 italic">Свободный ответ (без вариантов)</p>`
      : (options.length
        ? `<ul class="space-y-1">${options.map((o) => `
            <li class="flex items-center gap-2 text-sm">
              <span class="${o.is_correct ? 'text-emerald-600' : 'text-slate-300'}">●</span>
              <span class="flex-1 ${o.is_correct ? 'font-medium' : ''}">${esc(o.text)}</span>
              <button data-action="delete-option" data-id="${o.id}" data-question-id="${qq.id}"
                      class="text-red-500 hover:text-red-700 px-1 text-xs">✕</button>
            </li>`).join('')}</ul>`
        : `<p class="text-sm text-slate-400 italic">Вариантов пока нет</p>`);

    const addOptionForm = type === 'text' ? '' : `
      <form class="add-option-form mt-3 flex items-center gap-2" data-question-id="${qq.id}">
        <input name="text" required placeholder="Текст варианта"
               class="flex-1 border border-slate-300 rounded-lg px-3 py-1.5 text-sm" />
        <label class="flex items-center gap-1 text-xs text-slate-600 whitespace-nowrap">
          <input type="checkbox" name="is_correct" /> верный
        </label>
        <button type="submit" class="text-sm px-3 py-1.5 rounded-lg bg-slate-800 text-white hover:bg-slate-700">+</button>
      </form>`;

    return `
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="font-medium">${index + 1}. ${esc(qq.text)}</p>
            <span class="inline-block text-xs mt-1 px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">
              ${esc(TYPE_LABEL[type])}
            </span>
          </div>
          <button data-action="delete-question" data-id="${qq.id}"
                  class="text-red-600 hover:bg-red-50 rounded-lg px-2 py-1 text-sm shrink-0">Удалить</button>
        </div>
        <div class="mt-3">${optionsHtml}</div>
        ${addOptionForm}
      </div>`;
  }

  function bindQuestionnaireDetail(qid) {
    const addQ = $('#add-question-form');
    if (addQ) {
      addQ.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fd = new FormData(addQ);
        try {
          await api(ENDPOINTS.questions(qid), {
            method: 'POST',
            body: { text: fd.get('text'), question_type: fd.get('question_type') },
          });
          toast('Вопрос добавлен', 'success');
          state.data.currentQuestionnaire = null;
          loadQuestionnaire(qid);
        } catch (ex) { toast(ex.message, 'error'); }
      });
    }

    $$('.add-option-form').forEach((form) => {
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const questionId = form.dataset.questionId;
        const fd = new FormData(form);
        try {
          await api(ENDPOINTS.options(questionId), {
            method: 'POST',
            body: { text: fd.get('text'), is_correct: !!fd.get('is_correct') },
          });
          state.data.currentQuestionnaire = null;
          loadQuestionnaire(qid);
        } catch (ex) { toast(ex.message, 'error'); }
      });
    });
  }

  /* ====================================================================== *
   *  МЕТОДИСТ: ПОСЛЕДОВАТЕЛЬНОСТИ ТЕСТОВ
   * ====================================================================== */
  function viewSequencesShell() {
    return `
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold">Последовательности тестов</h2>
        <button id="toggle-new-seq"
                class="bg-indigo-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-indigo-700 transition">
          + Создать последовательность
        </button>
      </div>

      <form id="new-seq-form" class="hidden bg-white rounded-xl shadow p-4 mb-4 space-y-3">
        <div>
          <label class="block text-sm mb-1">Название</label>
          <input name="title" required class="w-full border border-slate-300 rounded-lg px-3 py-2" />
        </div>
        <div>
          <label class="block text-sm mb-1">Описание</label>
          <textarea name="description" rows="2" class="w-full border border-slate-300 rounded-lg px-3 py-2"></textarea>
        </div>
        <div>
          <label class="block text-sm mb-1">Тесты в последовательности (по порядку)</label>
          <div id="seq-items" class="space-y-2 mb-2"></div>
          <button type="button" id="seq-add-item"
                  class="text-sm text-indigo-700 hover:underline">+ добавить тест</button>
        </div>
        <button type="submit"
                class="bg-emerald-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-emerald-700 transition">
          Создать
        </button>
      </form>

      <div id="seq-list">${spinner()}</div>`;
  }

  function bindSequences() {
    const toggle = $('#toggle-new-seq');
    const form = $('#new-seq-form');
    const addBtn = $('#seq-add-item');

    if (toggle) {
      toggle.addEventListener('click', () => {
        form.classList.toggle('hidden');
        if (!form.classList.contains('hidden') && !$('#seq-items').children.length) {
          ensureQuestionnaireOptions().then(() => addSeqItemRow());
        }
      });
    }
    if (addBtn) addBtn.addEventListener('click', () => addSeqItemRow());
    if (form) {
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        const items = $$('#seq-items select').map((sel, i) => ({
          questionnaire_id: Number(sel.value),
          order: i + 1,
        }));
        if (!items.length) { toast('Добавьте хотя бы один тест', 'error'); return; }
        try {
          await api(ENDPOINTS.sequences, {
            method: 'POST',
            body: { title: fd.get('title'), description: fd.get('description') || null, items },
          });
          toast('Последовательность создана', 'success');
          form.reset();
          $('#seq-items').innerHTML = '';
          form.classList.add('hidden');
          loadSequences();
        } catch (ex) { toast(ex.message, 'error'); }
      });
    }
  }

  /** Гарантирует наличие списка опросников для выпадающего списка. */
  async function ensureQuestionnaireOptions() {
    if (!state.data.questionnaires) {
      try {
        const list = await api(ENDPOINTS.questionnaires);
        state.data.questionnaires = Array.isArray(list) ? list : (list && list.items) || [];
      } catch (ex) { toast(ex.message, 'error'); state.data.questionnaires = []; }
    }
    return state.data.questionnaires;
  }

  function addSeqItemRow() {
    const wrap = $('#seq-items');
    if (!wrap) return;
    const list = state.data.questionnaires || [];
    const opts = list.length
      ? list.map((q) => `<option value="${q.id}">${esc(q.title)}</option>`).join('')
      : `<option value="">— нет опросников —</option>`;

    const row = document.createElement('div');
    row.className = 'flex gap-2 items-center';
    row.innerHTML = `
      <span class="text-sm text-slate-500 w-6 item-index"></span>
      <select class="flex-1 border border-slate-300 rounded-lg px-3 py-2">${opts}</select>
      <button type="button" class="remove-item text-red-600 px-2 hover:bg-red-50 rounded-lg">✕</button>`;
    row.querySelector('.remove-item').addEventListener('click', () => { row.remove(); renumberSeqItems(); });
    wrap.appendChild(row);
    renumberSeqItems();
  }

  function renumberSeqItems() {
    $$('#seq-items .item-index').forEach((el, i) => { el.textContent = (i + 1) + '.'; });
  }

  async function loadSequences() {
    const box = $('#seq-list');
    if (!box) return;
    box.innerHTML = spinner();
    try {
      const list = await api(ENDPOINTS.sequences);
      const seqs = Array.isArray(list) ? list : (list && list.items) || [];
      box.innerHTML = renderSequenceList(seqs);
    } catch (ex) { box.innerHTML = errorBox(ex.message); }
  }

  function renderSequenceList(list) {
    if (!list.length) return emptyBox('Последовательностей пока нет.');
    return `<div class="grid gap-3">${list.map((s) => {
      const count = (s.items && s.items.length) || s.items_count || 0;
      return `
        <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4 flex items-center justify-between gap-3">
          <div class="min-w-0">
            <button data-action="open-sequence" data-id="${s.id}"
                    class="font-medium text-indigo-700 hover:underline truncate text-left">${esc(s.title)}</button>
            ${s.description ? `<p class="text-sm text-slate-500 truncate">${esc(s.description)}</p>` : ''}
            <p class="text-xs text-slate-400 mt-1">Тестов: ${esc(count)}</p>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <button data-action="open-sequence" data-id="${s.id}"
                    class="text-sm px-3 py-1.5 rounded-lg border border-slate-300 hover:bg-slate-50">Открыть</button>
            <button data-action="delete-sequence" data-id="${s.id}"
                    class="text-sm px-2 py-1.5 rounded-lg text-red-600 hover:bg-red-50">Удалить</button>
          </div>
        </div>`;
    }).join('')}</div>`;
  }

  /* --- Детальный экран последовательности + назначение ------------------ */
  function viewSequenceShell() {
    return `
      <button data-action="nav" data-view="sequences"
              class="text-sm text-slate-600 hover:underline mb-3">← К последовательностям</button>
      <div id="seq-detail">${spinner()}</div>`;
  }

  async function loadSequence(id) {
    const box = $('#seq-detail');
    if (!box) return;
    box.innerHTML = spinner();
    let seq;
    try {
      seq = await api(ENDPOINTS.sequence(id));
    } catch (ex) {
      box.innerHTML = errorBox(ex.message);
      return;
    }
    box.innerHTML = renderSequenceDetail(seq);
    bindAssign(id);
  }

  function renderSequenceDetail(seq) {
    const items = seq.items || [];
    const itemsHtml = items.length
      ? `<ol class="space-y-2 list-decimal list-inside">${items.map((it) => {
          const title = (it.questionnaire && it.questionnaire.title) || it.questionnaire_title || ('Опросник #' + it.questionnaire_id);
          return `<li class="text-sm py-1 border-b border-slate-100 last:border-0">
                    <span class="font-medium">${esc(title)}</span>
                  </li>`;
        }).join('')}</ol>`
      : emptyBox('В последовательности нет тестов');

    return `
      <div class="bg-white rounded-xl shadow p-4 mb-4">
        <h2 class="text-lg font-semibold">${esc(seq.title)}</h2>
        ${seq.description ? `<p class="text-sm text-slate-500 mt-1">${esc(seq.description)}</p>` : ''}
        <div class="mt-3">
          <h3 class="text-sm font-medium text-slate-600 mb-2">Состав последовательности:</h3>
          ${itemsHtml}
        </div>
      </div>

      <form id="assign-form" class="bg-white rounded-xl shadow p-4 space-y-4">
        <h3 class="font-medium">Назначить последовательность студентам</h3>

        <div>
          <label class="block text-sm mb-1">Студенты</label>
          <div id="assign-users-box" class="text-sm text-slate-500">Загрузка списка…</div>
        </div>

        <div class="grid sm:grid-cols-2 gap-3">
          <div>
            <label class="block text-sm mb-1">Начало (необязательно)</label>
            <input type="datetime-local" name="start_at" class="w-full border border-slate-300 rounded-lg px-3 py-2" />
          </div>
          <div>
            <label class="block text-sm mb-1">Окончание (необязательно)</label>
            <input type="datetime-local" name="end_at" class="w-full border border-slate-300 rounded-lg px-3 py-2" />
          </div>
        </div>

        <button type="submit"
                class="bg-emerald-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-emerald-700 transition">
          Назначить
        </button>
      </form>`;
  }

  async function bindAssign(seqId) {
    // Загружаем список студентов (если эндпоинт доступен).
    const box = $('#assign-users-box');
    let students = [];
    try {
      const list = await api(ENDPOINTS.users('role=student'));
      students = Array.isArray(list) ? list : (list && list.items) || [];
    } catch (_) { students = []; }

    function userLabel(u) {
      return u.full_name || u.name || u.username || ('Пользователь #' + u.id);
    }

    if (students.length) {
      box.innerHTML = `
        <div class="max-h-56 overflow-auto border border-slate-200 rounded-lg p-2 space-y-1">
          ${students.map((u) => `
            <label class="flex items-center gap-2 px-2 py-1 rounded hover:bg-slate-50 cursor-pointer">
              <input type="checkbox" class="student-cb" value="${u.id}" />
              <span>${esc(userLabel(u))}</span>
              <span class="text-xs text-slate-400">${esc(u.group || '')}</span>
            </label>`).join('')}
        </div>
        <button type="button" id="toggle-all-students" class="text-xs text-indigo-700 hover:underline mt-1">выбрать всех</button>`;
      $('#toggle-all-students')?.addEventListener('click', () => {
        const cbs = $$('.student-cb');
        const allChecked = cbs.every((c) => c.checked);
        cbs.forEach((c) => { c.checked = !allChecked; });
      });
    } else {
      box.innerHTML = `
        <input name="user_ids" placeholder="ID пользователей через запятую, напр. 3,5,8"
               class="w-full border border-slate-300 rounded-lg px-3 py-2" />
        <p class="text-xs text-slate-400 mt-1">Список студентов недоступен — укажите ID вручную.</p>`;
    }

    const form = $('#assign-form');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      let user_ids = [];
      if (students.length) {
        user_ids = $$('.student-cb').filter((c) => c.checked).map((c) => Number(c.value));
      } else {
        user_ids = String(fd.get('user_ids') || '')
          .split(',').map((s) => s.trim()).filter(Boolean).map(Number).filter((n) => !isNaN(n));
      }
      if (!user_ids.length) { toast('Выберите хотя бы одного студента', 'error'); return; }

      try {
        await api(ENDPOINTS.assign(seqId), {
          method: 'POST',
          body: {
            user_ids,
            start_at: toIso(fd.get('start_at')),
            end_at: toIso(fd.get('end_at')),
          },
        });
        toast(`Назначено студентам: ${user_ids.length}`, 'success');
      } catch (ex) { toast(ex.message, 'error'); }
    });
  }

  /* ====================================================================== *
   *  СТУДЕНТ: НАЗНАЧЕНИЯ
   * ====================================================================== */
  function viewAssignmentsShell() {
    return `
      <h2 class="text-lg font-semibold mb-4">Мои назначения</h2>
      <div id="assignments-list">${spinner()}</div>`;
  }

  async function loadAssignments() {
    const box = $('#assignments-list');
    if (!box) return;
    box.innerHTML = spinner();
    try {
      const list = await api(ENDPOINTS.myAssignments);
      const items = Array.isArray(list) ? list : (list && list.items) || [];
      box.innerHTML = renderAssignments(items);
    } catch (ex) { box.innerHTML = errorBox(ex.message); }
  }

  function renderAssignments(list) {
    if (!list.length) return emptyBox('Назначений пока нет.');
    return `<div class="grid gap-3">${list.map((a) => {
      const q = a.questionnaire || {};
      const seq = a.sequence || {};
      const title = a.title || q.title || seq.title || ('Назначение #' + a.id);
      const status = a.status || 'assigned';
      const statusMap = {
        assigned: ['Не начато', 'bg-slate-100 text-slate-600'],
        in_progress: ['В процессе', 'bg-amber-100 text-amber-700'],
        completed: ['Завершено', 'bg-emerald-100 text-emerald-700'],
        passed: ['Пройдено', 'bg-emerald-100 text-emerald-700'],
        failed: ['Не пройдено', 'bg-red-100 text-red-700'],
      };
      const [sLabel, sCls] = statusMap[status] || [status, 'bg-slate-100 text-slate-600'];

      const hasResult = !!a.last_attempt_id || status === 'completed' || status === 'passed' || status === 'failed';
      const attemptId = a.last_attempt_id || (a.attempts && a.attempts.length ? a.attempts[a.attempts.length - 1].id : null);

      const startLabel = status === 'in_progress' ? 'Продолжить' : 'Начать тест';

      return `
        <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <p class="font-medium truncate">${esc(title)}</p>
              ${seq.title && a.questionnaire ? `<p class="text-sm text-slate-500 truncate">Последовательность: ${esc(seq.title)}</p>` : ''}
              <p class="text-xs text-slate-400 mt-1">
                ${a.start_at ? 'с ' + fmtDate(a.start_at) : ''}
                ${a.end_at ? ' по ' + fmtDate(a.end_at) : ''}
              </p>
            </div>
            <span class="text-xs px-2 py-1 rounded-full whitespace-nowrap ${sCls}">${esc(sLabel)}</span>
          </div>

          <div class="mt-3 flex items-center gap-2">
            ${!hasResult ? `
              <button data-action="start-attempt" data-id="${a.id}"
                      class="text-sm px-3 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 transition">
                ${esc(startLabel)}
              </button>` : ''}
            ${attemptId ? `
              <button data-action="open-result" data-id="${attemptId}"
                      class="text-sm px-3 py-2 rounded-lg border border-slate-300 hover:bg-slate-50">
                Результат
              </button>` : ''}
          </div>
        </div>`;
    }).join('')}</div>`;
  }

  /* ====================================================================== *
   *  СТУДЕНТ: ПРОХОЖДЕНИЕ ТЕСТА
   * ====================================================================== */
  async function startAttempt(assignmentId) {
    try {
      const res = await api(ENDPOINTS.startAttempt, { method: 'POST', body: { assignment_id: assignmentId } });
      const attempt = (res && res.attempt) || res;
      if (!attempt || attempt.id === undefined) throw new Error('Сервер не вернул попытку');

      // На всякий случай подтягиваем вопросы, если их не вернули.
      if (!attempt.questions) {
        if (attempt.questionnaire && attempt.questionnaire.questions) {
          attempt.questions = attempt.questionnaire.questions;
        } else if (attempt.questionnaire_id) {
          const q = await api(ENDPOINTS.questionnaire(attempt.questionnaire_id));
          attempt.questions = q.questions || [];
          attempt.questionnaire = q;
        }
      }
      state.answers = {};
      navigate('attempt', { attempt, attemptId: attempt.id });
    } catch (ex) {
      toast(ex.message, 'error');
    }
  }

  function viewAttemptShell() {
    return `
      <div class="mb-4">
        <h2 id="attempt-title" class="text-lg font-semibold">Прохождение теста</h2>
        <p class="text-sm text-slate-500">Выберите ответы и нажмите «Завершить тест».</p>
      </div>
      <form id="attempt-form" class="space-y-3 mb-6"></form>
      <div class="flex items-center gap-3">
        <button id="finish-btn"
                class="bg-emerald-600 text-white text-sm px-5 py-2.5 rounded-lg hover:bg-emerald-700 transition">
          Завершить тест
        </button>
        <span id="attempt-hint" class="text-xs text-slate-400"></span>
      </div>`;
  }

  function renderAttempt() {
    const attempt = state.params.attempt;
    const form = $('#attempt-form');
    const titleEl = $('#attempt-title');
    if (!attempt || !form) return;

    const title = (attempt.questionnaire && attempt.questionnaire.title) || attempt.title;
    if (title && titleEl) titleEl.textContent = title;

    const questions = attempt.questions || [];
    if (!questions.length) {
      form.innerHTML = emptyBox('В тесте нет вопросов');
      return;
    }
    form.innerHTML = questions.map((q, i) => renderAttemptQuestion(q, i)).join('');
  }

  function renderAttemptQuestion(q, index) {
    const type = normType(q.question_type || q.type);
    const options = q.options || [];
    let body = '';

    if (type === 'text') {
      body = `<textarea name="q_${q.id}" data-qid="${q.id}" data-type="text" rows="3"
                 class="w-full border border-slate-300 rounded-lg px-3 py-2"></textarea>`;
    } else if (options.length) {
      const inputType = type === 'multiple' ? 'checkbox' : 'radio';
      body = `<div class="space-y-2">${options.map((o) => `
        <label class="flex items-center gap-2 px-3 py-2 rounded-lg border border-slate-200 hover:bg-slate-50 cursor-pointer">
          <input type="${inputType}" name="q_${q.id}" data-qid="${q.id}" data-type="${type}" value="${o.id}" />
          <span>${esc(o.text)}</span>
        </label>`).join('')}</div>`;
    } else {
      body = `<p class="text-sm text-slate-400 italic">Нет вариантов ответа</p>`;
    }

    return `
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
        <p class="font-medium mb-3">${index + 1}. ${esc(q.text)}</p>
        ${body}
      </div>`;
  }

  function bindAttempt() {
    const form = $('#attempt-form');
    form?.addEventListener('change', (e) => {
      const qid = e.target.dataset.qid;
      if (!qid) return;
      state.answers[qid] = gatherAnswer(qid);
      scheduleSave(qid);
    });

    $('#finish-btn')?.addEventListener('click', finishAttempt);
  }

  function gatherAnswer(qid) {
    const form = $('#attempt-form');
    const inputs = $$(`[data-qid="${qid}"]`, form);
    if (!inputs.length) return { option_ids: [], text: null };
    const type = inputs[0].dataset.type;
    if (type === 'text') {
      return { option_ids: [], text: inputs[0].value };
    }
    const option_ids = inputs.filter((i) => i.checked).map((i) => Number(i.value));
    return { option_ids, text: null };
  }

  /** Автосохранение ответа (пользователь переключился — ответ не потеряется). */
  function scheduleSave(qid) {
    const attemptId = state.params.attemptId;
    if (!attemptId) return;
    clearTimeout(state.saving[qid]);
    state.saving[qid] = setTimeout(async () => {
      const ans = state.answers[qid] || { option_ids: [], text: null };
      try {
        await api(ENDPOINTS.answer(attemptId), {
          method: 'POST',
          body: { question_id: Number(qid), option_ids: ans.option_ids, text: ans.text },
        });
        const hint = $('#attempt-hint');
        if (hint) hint.textContent = 'Ответы сохраняются автоматически…';
      } catch (_) { /* тихо: при финише отправим повторно */ }
    }, 400);
  }

  async function finishAttempt() {
    const attempt = state.params.attempt;
    const attemptId = state.params.attemptId;
    if (!attempt || !attemptId) return;

    const btn = $('#finish-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Сохранение…'; }

    try {
      // Сохраняем все ответы (в т.ч. те, что не успели автосохраниться).
      const questions = attempt.questions || [];
      for (const q of questions) {
        const ans = state.answers[q.id] || gatherAnswer(q.id);
        await api(ENDPOINTS.answer(attemptId), {
          method: 'POST',
          body: { question_id: Number(q.id), option_ids: ans.option_ids, text: ans.text },
        }).catch(() => {});
      }
      await api(ENDPOINTS.finish(attemptId), { method: 'POST' });
      toast('Тест завершён', 'success');
      navigate('result', { id: attemptId });
    } catch (ex) {
      toast(ex.message, 'error');
      if (btn) { btn.disabled = false; btn.textContent = 'Завершить тест'; }
    }
  }

  /* ====================================================================== *
   *  СТУДЕНТ: РЕЗУЛЬТАТ
   * ====================================================================== */
  function viewResultShell() {
    return `
      <button data-action="nav" data-view="my_assignments"
              class="text-sm text-slate-600 hover:underline mb-3">← К назначениям</button>
      <div id="result-box">${spinner()}</div>`;
  }

  async function loadResult(attemptId) {
    const box = $('#result-box');
    if (!box) return;
    box.innerHTML = spinner();
    try {
      const res = await api(ENDPOINTS.result(attemptId));
      box.innerHTML = renderResult(res);
    } catch (ex) { box.innerHTML = errorBox(ex.message); }
  }

  function renderResult(res) {
    res = res || {};
    const score = res.score ?? res.points ?? 0;
    const max = res.max_score ?? res.total ?? res.max_points ?? '—';
    const passed = res.passed ?? res.is_passed;
    const passedVal = typeof passed === 'boolean' ? passed : null;

    const badge = passedVal === null
      ? ''
      : passedVal
        ? `<span class="px-3 py-1 rounded-full bg-emerald-100 text-emerald-700 text-sm">Тест пройден</span>`
        : `<span class="px-3 py-1 rounded-full bg-red-100 text-red-700 text-sm">Тест не пройден</span>`;

    const details = res.questions || res.details || res.answers || [];
    const detailsHtml = Array.isArray(details) && details.length
      ? `<div class="mt-4 space-y-2">
           ${details.map((d) => {
             const text = d.text || (d.question && d.question.text) || '';
             const correct = d.is_correct ?? d.correct;
             const mark = correct === true ? '✅' : correct === false ? '❌' : '•';
             return `<div class="flex items-start gap-2 text-sm border-b border-slate-100 pb-2 last:border-0">
                       <span>${mark}</span>
                       <span class="flex-1">${esc(text)}</span>
                     </div>`;
           }).join('')}
         </div>`
      : '';

    return `
      <div class="bg-white rounded-xl shadow p-6">
        <h2 class="text-lg font-semibold mb-3">Результат тестирования</h2>
        <div class="flex items-center gap-4 flex-wrap">
          <div class="text-3xl font-bold text-indigo-700">${esc(score)} <span class="text-lg text-slate-400">/ ${esc(max)}</span></div>
          ${badge}
        </div>
        ${res.completed_at ? `<p class="text-xs text-slate-400 mt-2">Завершено: ${fmtDate(res.completed_at)}</p>` : ''}
        ${detailsHtml}
      </div>`;
  }

  /* ====================================================================== *
   *  Делегированные события (кнопки с data-action)
   * ====================================================================== */
  document.addEventListener('click', async (e) => {
    const el = e.target.closest('[data-action]');
    if (!el) return;
    const action = el.dataset.action;
    const id = el.dataset.id;

    switch (action) {
      case 'nav':
        navigate(el.dataset.view);
        break;

      case 'logout':
        try { await api(ENDPOINTS.logout, { method: 'POST' }); } catch (_) {}
        state.user = null;
        toast('Вы вышли из системы');
        navigate('login');
        break;

      case 'open-questionnaire':
        navigate('questionnaire', { id: Number(id) });
        break;

      case 'delete-questionnaire':
        if (!confirm('Удалить опросник вместе с вопросами?')) return;
        try {
          await api(ENDPOINTS.questionnaire(id), { method: 'DELETE' });
          toast('Опросник удалён', 'success');
          if (state.view === 'questionnaire') navigate('questionnaires');
          else loadQuestionnaires();
        } catch (ex) { toast(ex.message, 'error'); }
        break;

      case 'delete-question':
        if (!confirm('Удалить вопрос?')) return;
        try {
          await api(ENDPOINTS.question(id), { method: 'DELETE' });
          state.data.currentQuestionnaire = null;
          loadQuestionnaire(state.params.id);
        } catch (ex) { toast(ex.message, 'error'); }
        break;

      case 'delete-option':
        try {
          await api(ENDPOINTS.option(id), { method: 'DELETE' });
          state.data.currentQuestionnaire = null;
          loadQuestionnaire(state.params.id);
        } catch (ex) { toast(ex.message, 'error'); }
        break;

      case 'open-sequence':
        navigate('sequence', { id: Number(id) });
        break;

      case 'delete-sequence':
        if (!confirm('Удалить последовательность?')) return;
        try {
          await api(ENDPOINTS.sequence(id), { method: 'DELETE' });
          toast('Последовательность удалена', 'success');
          if (state.view === 'sequence') navigate('sequences');
          else loadSequences();
        } catch (ex) { toast(ex.message, 'error'); }
        break;

      case 'start-attempt':
        await startAttempt(Number(id));
        break;

      case 'open-result':
        navigate('result', { id: Number(id) });
        break;

      default:
        break;
    }
  });

  /* ====================================================================== *
   *  Запуск приложения
   * ====================================================================== */
  async function boot() {
    try {
      const me = await api(ENDPOINTS.me);
      if (me && me.id) {
        state.user = me;
        navigate(isStudent() ? 'my_assignments' : 'questionnaires');
        return;
      }
    } catch (_) { /* не авторизован */ }
    navigate('login');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // Экспорт для отладки в консоли.
  window.AppSurvey = { state, api, navigate, ENDPOINTS };
})();
