/* =========================================================================
 *  static/app.js — Общие утилиты для Jinja2-шаблонов
 * ========================================================================= */

// 1. Уведомления (toast)
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

window.toast = function(msg, type = 'info') {
    const colors = { info: 'bg-gray-800', success: 'bg-green-600', error: 'bg-red-600' };
    const el = document.createElement('div');
    el.className = `${colors[type] || colors.info} text-white px-4 py-2 rounded shadow-lg text-sm`;
    el.textContent = msg;
    toastHost().appendChild(el);
    setTimeout(() => {
        el.style.transition = 'opacity .3s';
        el.style.opacity = '0';
        setTimeout(() => el.remove(), 300);
    }, 3000);
};

// 2. Экранирование HTML
window.esc = function(v) {
    if (v === null || v === undefined) return '';
    return String(v)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
};

// 3. API клиент для вызовов из шаблонов.
// Сигнатура, которую ждут ваши HTML-шаблоны: api(method, url, body)
window.api = async function(method, url, body = null) {
    const options = {
        method: method.toUpperCase(),
        headers: { 'Accept': 'application/json' },
        credentials: 'same-origin'
    };

    if (body !== null) {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
    }

    let res;
    try {
        res = await fetch(url, options);
    } catch (e) {
        throw new Error('Нет соединения с сервером');
    }

    if (res.status === 204) return null;

    const ct = res.headers.get('content-type') || '';
    let data = null;
    try {
        data = ct.includes('application/json') ? await res.json() : await res.text();
    } catch (_) {}

    if (!res.ok) {
        if (res.status === 401) {
            window.location.href = '/login';
            return;
        }
        let msg = (data && (data.detail || data.message || data.error)) || res.statusText;
        if (Array.isArray(msg)) msg = msg.map(m => m.msg || JSON.stringify(m)).join('; ');
        throw new Error(typeof msg === 'string' ? msg : 'Ошибка запроса');
    }
    return data;
};

// 4. Логаут.
// В main.py выход идет по эндпоинту GET /logout, который очищает cookie.
window.logout = function() {
    window.location.href = '/logout';
};

// 5. Модальные окна (создание тестов, назначение последовательностей)
// В base.html есть контейнер <div id="modal-root"></div>
window.openModal = function(htmlContent) {
    const root = document.getElementById('modal-root');
    if (!root) return;
    root.innerHTML = `
        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50">
            <div class="bg-white rounded-lg p-6 max-w-lg w-full mx-4 shadow-xl max-h-[90vh] overflow-y-auto">
                ${htmlContent}
            </div>
        </div>`;
};

window.closeModal = function() {
    const root = document.getElementById('modal-root');
    if (root) root.innerHTML = '';
};