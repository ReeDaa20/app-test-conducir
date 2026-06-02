const state = {
  tests: [],
  questions: [],
  current: 0,
  answers: new Map(),
  startedAt: null,
  timer: null,
  result: null,
  finishing: false,
  currentTest: null,
};

const EXAM_SECONDS = 30 * 60;

const examList = document.querySelector("#examList");
const testLayout = document.querySelector("#testLayout");
const resultPanel = document.querySelector("#resultPanel");
const questionTitle = document.querySelector("#questionTitle");
const questionCategory = document.querySelector("#questionCategory");
const questionCounter = document.querySelector("#questionCounter");
const answerList = document.querySelector("#answerList");
const questionMap = document.querySelector("#questionMap");
const progressPercent = document.querySelector("#progressPercent");
const timer = document.querySelector("#timer");
const visualStrip = document.querySelector("#visualStrip");
const historyList = document.querySelector("#historyList");
const lastScore = document.querySelector("#lastScore");
const attemptCount = document.querySelector("#attemptCount");
const sourceSummary = document.querySelector("#sourceSummary");
const topActions = document.querySelector("#topActions");

const labels = {
  all: "Aleatorio",
  general: "Test general",
  senales: "Señales",
  prioridad: "Prioridad",
  seguridad: "Seguridad vial",
  testsconducir_b: "TestsConducir B",
  simulacro: "Simulacro",
  simulacro_externo: "Simulacro",
};

const visualMap = {
  warning: ["triangle", "red", "!"],
  mandatory: ["circle", "blue", "↻"],
  stop: ["octagon", "red", "STOP"],
  ban: ["circle", "red", ""],
  line: ["line", "red", "━━"],
  city: ["square", "blue", "30"],
  mirror: ["square", "blue", "↗"],
  agent: ["square", "red", "✋"],
  paper: ["square", "blue", "ITV"],
  protect: ["triangle", "red", "PAS"],
  cross: ["square", "blue", "+"],
  garage: ["square", "blue", "P"],
  crosswalk: ["square", "blue", "▦"],
  ambulance: ["square", "red", "+"],
  roundabout: ["circle", "blue", "↺"],
  rain: ["square", "blue", "☂"],
  phone: ["circle", "red", "☎"],
  sleep: ["square", "blue", "Zz"],
  alcohol: ["circle", "red", "0,0"],
  tire: ["square", "blue", "◎"],
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) throw new Error("No se pudo completar la operación");
  return response.json();
}

function formatSeconds(seconds) {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
  const rest = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${rest}`;
}

function elapsedSeconds() {
  if (!state.startedAt) return 0;
  return Math.floor((Date.now() - state.startedAt) / 1000);
}

function startTimer() {
  clearInterval(state.timer);
  state.startedAt = Date.now();
  timer.textContent = formatSeconds(EXAM_SECONDS);
  state.timer = setInterval(() => {
    const remaining = Math.max(0, EXAM_SECONDS - elapsedSeconds());
    timer.textContent = formatSeconds(remaining);
    if (remaining === 0) {
      finishTest({ confirmBeforeFinish: false });
    }
  }, 500);
}

function stopTimer() {
  clearInterval(state.timer);
  state.timer = null;
}

function confirmFinishExam() {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML = `
      <section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="confirmTitle">
        <h3 id="confirmTitle">¿Seguro que quieres finalizar el examen?</h3>
        <p>Se guardará tu resultado y podrás revisar los fallos al terminar.</p>
        <div class="modal-actions">
          <button class="ghost-button" type="button" data-action="cancel">Cancelar</button>
          <button class="primary-button" type="button" data-action="finish">Finalizar examen</button>
        </div>
      </section>
    `;
    document.body.appendChild(overlay);

    const close = (accepted) => {
      overlay.remove();
      resolve(accepted);
    };

    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) close(false);
      const action = event.target?.dataset?.action;
      if (action === "cancel") close(false);
      if (action === "finish") close(true);
    });

    overlay.querySelector("[data-action='finish']").focus();
  });
}

function renderExamList() {
  const rows = state.tests
    .map((test) => {
      const attempt = test.lastAttempt;
      const statusClass = attempt ? (attempt.passed ? "is-pass" : "is-fail") : "is-pending";
      const status = attempt ? (attempt.passed ? "Apto" : "No apto") : "Sin hacer";
      const errors = attempt ? attempt.errors : "-";
      const score = attempt ? attempt.score.toFixed(1) : "-";
      const date = attempt
        ? new Date(`${attempt.createdAt}Z`).toLocaleDateString("es-ES")
        : "-";
      return `
        <button class="exam-row ${statusClass}" data-test-id="${test.id}" title="Abrir test ${test.number}">
          <span class="exam-number">${test.number.toString().padStart(3, "0")}</span>
          <span class="exam-status"><i></i>${status}</span>
          <span>${errors}</span>
          <span>${score}</span>
          <span>${date}</span>
          <span class="exam-chevron">›</span>
        </button>
      `;
    })
    .join("");

  examList.innerHTML = `
    <div class="exam-help">
      <p>El examen consta de 30 preguntas. Para aprobar puedes cometer como máximo 3 fallos.</p>
    </div>
    <div class="exam-table">
      <div class="exam-header">
        <span>Examen</span>
        <span>Estado</span>
        <span>Fallos</span>
        <span>Nota</span>
        <span>Fecha</span>
        <span></span>
      </div>
      ${rows}
    </div>
  `;

  examList.querySelectorAll(".exam-row").forEach((button) => {
    button.addEventListener("click", () => startTest(Number(button.dataset.testId)));
  });
}

function renderQuestion() {
  const question = state.questions[state.current];
  const selected = state.answers.get(question.id);
  questionTitle.textContent = question.title;
  questionCategory.textContent = labels[question.category] || question.category;
  questionCounter.textContent = `${state.current + 1}/${state.questions.length}`;

  const visual = visualMap[question.imageKey] || ["square", "blue", ""];
  visualStrip.classList.toggle("has-image", Boolean(question.imageUrl));
  if (question.imageUrl) {
    visualStrip.hidden = false;
    visualStrip.removeAttribute("data-shape");
    visualStrip.removeAttribute("data-tone");
    visualStrip.innerHTML = `<img src="${question.imageUrl}" alt="" />`;
  } else {
    visualStrip.hidden = true;
    visualStrip.innerHTML = "";
  }

  answerList.innerHTML = question.options
    .map(
      (option, index) => `
        <button class="answer-button ${selected === index ? "is-selected" : ""}" data-index="${index}">
          <span class="answer-letter">${String.fromCharCode(65 + index)}</span>
          <span>${option}</span>
        </button>
      `,
    )
    .join("");

  answerList.querySelectorAll(".answer-button").forEach((button) => {
    button.addEventListener("click", () => {
      state.answers.set(question.id, Number(button.dataset.index));
      renderQuestion();
      renderProgress();
    });
  });

  document.querySelector("#prevButton").disabled = state.current === 0;
  document.querySelector("#nextButton").textContent =
    state.current === state.questions.length - 1 ? "Finalizar" : "Siguiente";
  renderProgress();
}

function renderProgress() {
  const answered = state.answers.size;
  const percent = Math.round((answered / state.questions.length) * 100);
  progressPercent.textContent = `${percent}%`;
  document.querySelector(".progress-ring").style.setProperty("--progress", `${percent}%`);

  questionMap.innerHTML = state.questions
    .map((question, index) => {
      const classes = [
        "map-button",
        index === state.current ? "is-current" : "",
        state.answers.has(question.id) ? "is-answered" : "",
      ]
        .filter(Boolean)
        .join(" ");
      return `<button class="${classes}" data-index="${index}" title="Pregunta ${index + 1}">${index + 1}</button>`;
    })
    .join("");

  questionMap.querySelectorAll(".map-button").forEach((button) => {
    button.addEventListener("click", () => {
      state.current = Number(button.dataset.index);
      renderQuestion();
    });
  });
}

async function startTest(testId) {
  const data = await api(`/api/questions?testId=${testId}`);
  state.questions = data.questions;
  if (!state.questions.length) {
    throw new Error("No hay preguntas disponibles para este test.");
  }
  state.current = 0;
  state.answers = new Map();
  state.result = null;
  state.finishing = false;
  state.currentTest = data.test;
  state.category = "simulacro";

  examList.hidden = true;
  topActions.hidden = false;
  resultPanel.hidden = true;
  testLayout.hidden = false;
  startTimer();
  renderQuestion();
}

async function finishTest({ confirmBeforeFinish = true } = {}) {
  if (!state.questions.length || state.finishing) return;
  if (confirmBeforeFinish) {
    const accepted = await confirmFinishExam();
    if (!accepted) return;
  }
  state.finishing = true;
  stopTimer();
  const payload = {
    category: state.category,
    testId: state.currentTest?.id,
    durationSeconds: Math.min(elapsedSeconds(), EXAM_SECONDS),
    answers: state.questions.map((question) => ({
      questionId: question.id,
      selectedIndex: state.answers.has(question.id) ? state.answers.get(question.id) : null,
    })),
  };
  state.result = await api("/api/results", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  testLayout.hidden = true;
  resultPanel.hidden = false;
  renderResult();
  await refreshHistory();
  await refreshTests();
}

function renderResult() {
  const result = state.result;
  const passText = result.passed ? "Apto" : "No apto";
  resultPanel.innerHTML = `
    <div class="result-hero">
      <div class="score-badge ${result.passed ? "" : "is-fail"}">${result.score}</div>
      <div>
        <span class="pill">${passText}</span>
        <h2>${result.correct} correctas de ${result.total}</h2>
        <p class="muted">${result.errors} fallos · ${formatSeconds(result.durationSeconds)}</p>
      </div>
    </div>
    <div class="result-actions">
      <button class="ghost-button" id="backToCategories">Elegir otro test</button>
      <button class="primary-button" id="repeatTest">Repetir test</button>
    </div>
    <div class="review-list">
      ${result.review.map(renderReviewCard).join("")}
    </div>
  `;

  document.querySelector("#backToCategories").addEventListener("click", resetHome);
  document.querySelector("#repeatTest").addEventListener("click", () => startTest(result.testId));
}

function renderReviewCard(question, index) {
  const selectedText =
    question.selectedIndex === null ? "Sin responder" : question.options[question.selectedIndex];
  const correctText = question.options[question.correctIndex];
  return `
    <article class="review-card ${question.isCorrect ? "" : "is-error"}">
      <strong>${index + 1}. ${question.title}</strong>
      <p class="answer-review ${question.isCorrect ? "is-good" : "is-bad"}">Tu respuesta: ${selectedText}</p>
      <p class="answer-review is-good">Correcta: ${correctText}</p>
      <p class="muted">${question.explanation}</p>
    </article>
  `;
}

function resetHome() {
  stopTimer();
  timer.textContent = "00:00";
  topActions.hidden = true;
  state.questions = [];
  state.answers = new Map();
  state.result = null;
  state.currentTest = null;
  state.finishing = false;
  examList.hidden = false;
  testLayout.hidden = true;
  resultPanel.hidden = true;
}

async function refreshTests() {
  const data = await api("/api/tests");
  state.tests = data.tests;
  const completed = state.tests.filter((test) => test.lastAttempt).length;
  sourceSummary.textContent = `${state.tests.length} simulacros · ${completed} realizados`;
  attemptCount.textContent = completed.toString();
  const latest = state.tests
    .map((test) => test.lastAttempt)
    .filter(Boolean)
    .sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))[0];
  lastScore.textContent = latest ? latest.score.toFixed(1) : "--";
  renderExamList();
}

async function refreshHistory() {
  const data = await api("/api/results");
  const results = data.results;

  if (!results.length) {
    historyList.innerHTML = `<div class="empty-state">Aún no hay tests guardados.</div>`;
    return;
  }

  historyList.innerHTML = results
    .map((result) => {
      const date = new Date(`${result.created_at}Z`).toLocaleString("es-ES", {
        dateStyle: "short",
        timeStyle: "short",
      });
      const test = state.tests.find((item) => item.id === result.test_id);
      const title = test ? `Test ${test.number.toString().padStart(3, "0")}` : labels[result.category] || result.category;
      return `
        <article class="history-item">
          <div>
            <strong>${title} · Nota ${result.score.toFixed(1)}</strong>
            <span class="muted">${result.correct}/${result.total} correctas · ${result.errors} fallos · ${formatSeconds(result.duration_seconds)} · ${date}</span>
          </div>
          <span class="status ${result.passed ? "" : "is-fail"}">${result.passed ? "Apto" : "No apto"}</span>
        </article>
      `;
    })
    .join("");
}

function bindEvents() {
  document.querySelector("#prevButton").addEventListener("click", () => {
    state.current = Math.max(0, state.current - 1);
    renderQuestion();
  });

  document.querySelector("#nextButton").addEventListener("click", () => {
    if (state.current === state.questions.length - 1) {
      finishTest();
      return;
    }
    state.current += 1;
    renderQuestion();
  });

  document.querySelector("#newTestButton").addEventListener("click", resetHome);

  document.querySelectorAll(".nav-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".nav-tab").forEach((item) => item.classList.remove("is-active"));
      tab.classList.add("is-active");
      document.querySelectorAll(".view").forEach((view) => view.classList.remove("is-visible"));
      document.querySelector(`#${tab.dataset.view}View`).classList.add("is-visible");
    });
  });
}

async function boot() {
  bindEvents();
  await refreshTests();
  await refreshHistory();
}

boot().catch((error) => {
  document.body.innerHTML = `<main class="empty-state">${error.message}</main>`;
});
