(() => {
  'use strict';

  document.documentElement.classList.add('js');

  const scenarios = {
    reserve: {
      id: 'reserve',
      query: 'Найди backend-разработчиков в резерве со знанием Python и FastAPI',
      type: 'candidate',
      stages: ['Читаю доступные данные ATS', 'Проверяю критерии и варианты написания', 'Ранжирую совпадения', 'Добавляю подтверждения'],
      results: [
        { title: 'Анна К.', avatar: 'АК', summary: '2 подтверждённых совпадения', signals: [['Python', 'исходный критерий'], ['FastAPI', 'найдено в резюме'], ['Кадровый резерв', '']], disclaimer: 'Демонстрационный порядок, не оценка вероятности найма', evidence: [['Python', 'Исходный критерий · теги'], ['FastAPI', 'Исходный критерий · резюме: «Разрабатывала сервисы на FastAPI для внутренних команд»']] },
        { title: 'Михаил Р.', avatar: 'МР', summary: '2 подтверждённых совпадения', signals: [['Python', 'исходный критерий'], ['backend', 'вариант написания'], ['Кадровый резерв', '']], disclaimer: 'Демонстрационный порядок, не оценка вероятности найма', evidence: [['Python', 'Исходный критерий · должность'], ['backend', 'Добавленный вариант написания · должность']] }
      ]
    },
    reopen: {
      id: 'reopen',
      query: 'Кого стоит пересмотреть после повышения вилки до 350 000 ₽?',
      type: 'reopen',
      stages: ['Читаю доступные данные ATS', 'Проверяю критерии и варианты написания', 'Ранжирую совпадения', 'Добавляю подтверждения'],
      results: [
        { title: 'Дарья С.', avatar: 'ДС', summary: '2 наблюдаемых сигнала', signals: [['Вилка до 350 000 ₽', 'новое условие'], ['Ожидания до 330 000 ₽', 'прежнее ограничение снято'], ['Прошлый кандидат', '']], disclaimer: 'Демонстрационный порядок, не оценка вероятности найма', evidence: [['Вилка', 'Прежняя: до 280 000 ₽ · новая: до 350 000 ₽'], ['Ожидания', 'Указано в карточке: до 330 000 ₽']] },
        { title: 'Илья П.', avatar: 'ИП', summary: '1 наблюдаемый сигнал', signals: [['Удалённый график', 'новое условие'], ['Прошлый кандидат', '']], disclaimer: 'Демонстрационный порядок, не оценка вероятности найма', evidence: [['График', 'Прежний: fullDay · новый: remote']] }
      ]
    },
    job: {
      id: 'job',
      query: 'Какие внутренние вакансии подходят Python-разработчику и чего не хватает?',
      type: 'job',
      stages: ['Читаю доступные данные ATS', 'Проверяю критерии и варианты написания', 'Ранжирую совпадения', 'Добавляю подтверждения'],
      results: [
        { title: 'Backend-разработчик', avatar: 'BR', summary: '2 совпавших требования', signals: [['Python', 'совпавшее требование'], ['FastAPI', 'совпавшее требование'], ['Kubernetes', 'пробел'], ['Город', 'неизвестно']], disclaimer: 'Демонстрационный порядок, не оценка вероятности найма', evidence: [['Совпадения', 'Python, FastAPI · требования вакансии'], ['Пробелы', 'Kubernetes · требование вакансии'], ['Неизвестно', 'Город не указан ни в профиле, ни в вакансии']] },
        { title: 'Инженер платформы', avatar: 'ИП', summary: '1 совпавшее требование', signals: [['Python', 'совпавшее требование'], ['Terraform', 'пробел'], ['Город', 'неизвестно']], disclaimer: 'Демонстрационный порядок, не оценка вероятности найма', evidence: [['Совпадения', 'Python · требование вакансии'], ['Пробелы', 'Terraform · требование вакансии'], ['Неизвестно', 'Город не указан ни в профиле, ни в вакансии']] }
      ]
    }
  };

  const query = document.querySelector('[data-demo-query]');
  const status = document.querySelector('[data-demo-status]');
  const results = document.querySelector('[data-demo-results]');
  const chips = [...document.querySelectorAll('[data-scenario]')];
  const demoLink = document.querySelector('[data-demo-link]');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let current = scenarios.reserve;
  let timers = [];
  let running = false;

  const clearTimers = () => {
    timers.forEach(window.clearTimeout);
    timers = [];
  };

  const createElement = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text) element.textContent = text;
    if (className) element.className = className;
    return element;
  };

  const renderResults = (scenario) => {
    results.replaceChildren();
    scenario.results.forEach((result, index) => {
      const card = createElement('article', '', 'result-card');
      const top = createElement('div', '', 'result-topline');
      top.append(createElement('span', result.avatar, 'avatar'), createElement('strong', result.title), createElement('span', result.summary));
      const list = createElement('ul', '', 'signals');
      result.signals.forEach(([label, detail]) => {
        const item = document.createElement('li');
        item.append(createElement('b', label));
        if (detail) item.append(createElement('span', detail));
        list.append(item);
      });
      const evidenceId = `evidence-${scenario.id}-${index}`;
      const toggle = createElement('button', 'Почему найден', 'evidence-toggle');
      toggle.type = 'button';
      toggle.setAttribute('aria-expanded', 'false');
      toggle.setAttribute('aria-controls', evidenceId);
      const evidence = createElement('div', '', 'evidence');
      evidence.id = evidenceId;
      evidence.setAttribute('aria-label', `Подтверждения для ${result.title}`);
      evidence.hidden = true;
      const definitionList = document.createElement('dl');
      result.evidence.forEach(([term, detail]) => {
        const row = document.createElement('div');
        row.append(createElement('dt', term), createElement('dd', detail));
        definitionList.append(row);
      });
      evidence.append(definitionList);
      card.append(top, list, createElement('p', result.disclaimer, 'result-disclaimer'), toggle, evidence, createElement('small', 'Демонстрационные данные'));
      results.append(card);
    });
  };

  const setChip = (id) => chips.forEach((chip) => chip.setAttribute('aria-pressed', String(chip.dataset.scenario === id)));

  const finish = () => {
    clearTimers();
    running = false;
    renderResults(current);
    status.textContent = 'Готово: показаны демонстрационные результаты.';
  };

  const run = (scenario) => {
    clearTimers();
    current = scenario;
    query.textContent = scenario.query;
    setChip(scenario.id);
    if (reducedMotion) {
      scenario.stages.forEach((stage) => { status.textContent = stage; });
      finish();
      return;
    }
    running = true;
    results.replaceChildren();
    scenario.stages.forEach((stage, index) => {
      timers.push(window.setTimeout(() => { status.textContent = stage; }, index * 430));
    });
    timers.push(window.setTimeout(finish, 1750));
  };

  chips.forEach((chip) => chip.addEventListener('click', () => run(scenarios[chip.dataset.scenario])));
  results.addEventListener('click', (event) => {
    const toggle = event.target.closest('.evidence-toggle');
    if (!toggle) return;
    const evidence = document.getElementById(toggle.getAttribute('aria-controls'));
    const expanded = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!expanded));
    evidence.hidden = expanded;
  });

  demoLink.addEventListener('click', () => {
    window.setTimeout(() => chips[0].focus(), reducedMotion ? 0 : 250);
  });

  const copyButton = document.querySelector('[data-copy-button]');
  const copySource = document.querySelector('[data-copy-source]');
  const copyStatus = document.querySelector('[data-copy-status]');
  copyButton.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(copySource.textContent);
      copyButton.textContent = 'Скопировано';
      copyStatus.textContent = 'Команды скопированы в буфер обмена.';
      window.setTimeout(() => { copyButton.textContent = 'Скопировать команды'; }, 1800);
    } catch (error) {
      copyStatus.textContent = 'Не удалось скопировать команды. Выделите их вручную.';
    }
  });

  document.querySelectorAll('.feature-flip').forEach((card) => {
    const front = card.querySelector('.feature-front');
    const back = card.querySelector('.feature-back button');
    const setFlipped = (flipped) => {
      card.classList.toggle('is-flipped', flipped);
      front.setAttribute('aria-expanded', String(flipped));
    };
    front.addEventListener('click', () => setFlipped(true));
    back.addEventListener('click', () => setFlipped(false));
  });
})();
