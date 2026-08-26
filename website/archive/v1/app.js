const documentBody = document.body;
const hero = document.querySelector(".hero");
const revealNodes = document.querySelectorAll(".reveal");
const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const demoShell = document.querySelector(".demo-shell");
const demoPlayButton = document.querySelector(".demo-play");
const demoPlayLabel = document.querySelector(".play-label");
const demoPlayIcon = document.querySelector(".play-icon");
const demoRestartButton = document.querySelector(".demo-restart");
const demoScrubber = document.querySelector("#demo-scrubber");
const demoStatus = document.querySelector("#demo-status");
const phaseCounter = document.querySelector("#phase-counter");
const phaseTitle = document.querySelector("#phase-title");
const demoTime = document.querySelector("#demo-time");

const tweaksTrigger = document.querySelector(".tweaks-trigger");
const tweaksPanel = document.querySelector(".tweaks-panel");
const tweaksClose = document.querySelector(".tweaks-close");
const energyMode = document.querySelector("#energy-mode");
const demoSpeed = document.querySelector("#demo-speed");

const phases = [
  { title: "Request ready", status: "READY", time: "00:00" },
  { title: "Supervisor planning", status: "PLANNING", time: "00:02" },
  { title: "Workers executing", status: "EXECUTING", time: "00:04" },
  { title: "Evidence collected", status: "GATING", time: "00:06" },
  { title: "Review package ready", status: "READY TO REVIEW", time: "00:08" },
];

let currentPhase = 0;
let demoTimer = 0;
let isDemoPlaying = false;
let pointerFrame = 0;

function renderPhase(nextPhase) {
  currentPhase = Math.max(0, Math.min(phases.length - 1, Number(nextPhase)));
  const phase = phases[currentPhase];

  demoShell.dataset.phase = String(currentPhase);
  demoScrubber.value = String(currentPhase);
  demoStatus.textContent = phase.status;
  phaseCounter.textContent = `PHASE ${String(currentPhase + 1).padStart(2, "0")} / 05`;
  phaseTitle.textContent = phase.title;
  demoTime.textContent = `${phase.time} / 00:08`;

  if (currentPhase === phases.length - 1) {
    stopDemo(false);
    demoPlayLabel.textContent = "Replay demo";
    demoPlayIcon.textContent = "↻";
  }
}

function scheduleNextPhase() {
  window.clearTimeout(demoTimer);

  if (!isDemoPlaying) {
    return;
  }

  demoTimer = window.setTimeout(() => {
    if (currentPhase >= phases.length - 1) {
      stopDemo(false);
      return;
    }

    renderPhase(currentPhase + 1);
    scheduleNextPhase();
  }, Number(demoSpeed.value));
}

function playDemo() {
  if (currentPhase >= phases.length - 1) {
    renderPhase(0);
  }

  isDemoPlaying = true;
  demoPlayLabel.textContent = "Pause demo";
  demoPlayIcon.textContent = "Ⅱ";
  scheduleNextPhase();
}

function stopDemo(resetLabel = true) {
  isDemoPlaying = false;
  window.clearTimeout(demoTimer);

  if (resetLabel) {
    demoPlayLabel.textContent = "Resume demo";
    demoPlayIcon.textContent = "▶";
  }
}

function restartDemo() {
  stopDemo(false);
  renderPhase(0);
  demoPlayLabel.textContent = "Run demo";
  demoPlayIcon.textContent = "▶";
}

demoPlayButton.addEventListener("click", () => {
  if (isDemoPlaying) {
    stopDemo();
  } else {
    playDemo();
  }
});

demoRestartButton.addEventListener("click", restartDemo);

demoScrubber.addEventListener("input", (event) => {
  stopDemo();
  renderPhase(event.target.value);
});

demoSpeed.addEventListener("change", () => {
  if (isDemoPlaying) {
    scheduleNextPhase();
  }
});

function setTweaksVisibility(isOpen) {
  tweaksPanel.classList.toggle("is-open", isOpen);
  tweaksPanel.setAttribute("aria-hidden", String(!isOpen));
  tweaksTrigger.setAttribute("aria-expanded", String(isOpen));
  tweaksTrigger.hidden = isOpen;

  if (isOpen) {
    energyMode.focus();
  } else {
    tweaksTrigger.hidden = false;
    tweaksTrigger.focus();
  }
}

tweaksTrigger.addEventListener("click", () => setTweaksVisibility(true));
tweaksClose.addEventListener("click", () => setTweaksVisibility(false));

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && tweaksPanel.classList.contains("is-open")) {
    setTweaksVisibility(false);
  }
});

energyMode.addEventListener("change", (event) => {
  documentBody.dataset.energy = event.target.value;
});

if ("IntersectionObserver" in window && !prefersReducedMotion.matches) {
  const revealObserver = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          revealObserver.unobserve(entry.target);
        }
      });
    },
    { rootMargin: "0px 0px -7%", threshold: 0.08 },
  );

  revealNodes.forEach((node) => revealObserver.observe(node));
} else {
  revealNodes.forEach((node) => node.classList.add("is-visible"));
}

function updateMeshPosition(event) {
  if (prefersReducedMotion.matches || documentBody.dataset.energy === "quiet") {
    return;
  }

  if (pointerFrame) {
    cancelAnimationFrame(pointerFrame);
  }

  pointerFrame = requestAnimationFrame(() => {
    const bounds = hero.getBoundingClientRect();
    const x = Math.max(30, Math.min(90, ((event.clientX - bounds.left) / bounds.width) * 100));
    const y = Math.max(16, Math.min(80, ((event.clientY - bounds.top) / bounds.height) * 100));

    hero.style.setProperty("--mx", `${x}%`);
    hero.style.setProperty("--my", `${y}%`);
    pointerFrame = 0;
  });
}

hero.addEventListener("pointermove", updateMeshPosition, { passive: true });
hero.addEventListener("pointerleave", () => {
  hero.style.setProperty("--mx", "72%");
  hero.style.setProperty("--my", "34%");
});

renderPhase(0);
