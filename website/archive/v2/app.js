const documentBody = document.body;
const hero = document.querySelector(".hero");
const revealNodes = document.querySelectorAll(".reveal");
const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const demoStage = document.querySelector(".demo-stage");
const demoPlayButton = document.querySelector(".demo-play");
const demoPlayLabel = document.querySelector(".play-label");
const demoPlayIcon = document.querySelector(".play-icon");
const demoRestartButton = document.querySelector(".demo-restart");
const demoScrubber = document.querySelector("#demo-scrubber");
const demoStatus = document.querySelector("#demo-status");
const phaseNumber = document.querySelector("#phase-number");
const phaseTitle = document.querySelector("#phase-title");
const phaseCopy = document.querySelector("#phase-copy");

const tweaksTrigger = document.querySelector(".tweaks-trigger");
const tweaksPanel = document.querySelector(".tweaks-panel");
const tweaksClose = document.querySelector(".tweaks-close");
const energyMode = document.querySelector("#energy-mode");
const demoSpeed = document.querySelector("#demo-speed");

const phases = [
  {
    title: "Intent enters.",
    copy: "A single outcome arrives with boundaries attached.",
    status: "INTENT READY",
  },
  {
    title: "The Supervisor holds the plan.",
    copy: "Premium capability decomposes the task and defines acceptance.",
    status: "PLANNING",
  },
  {
    title: "Workers take the busywork.",
    copy: "Economical routes search, build, and test inside bounded workspaces.",
    status: "EXECUTING",
  },
  {
    title: "Evidence returns, not promises.",
    copy: "Patches, tests, and scope checks converge at one gate.",
    status: "EVIDENCE GATE",
  },
  {
    title: "Judgment makes the final call.",
    copy: "The Supervisor accepts, revises, or rejects with the full record visible.",
    status: "DECISION READY",
  },
];

let currentPhase = 0;
let demoTimer = 0;
let isDemoPlaying = false;
let pointerFrame = 0;

function renderPhase(nextPhase) {
  currentPhase = Math.max(0, Math.min(phases.length - 1, Number(nextPhase)));
  const phase = phases[currentPhase];

  demoStage.dataset.phase = String(currentPhase);
  demoScrubber.value = String(currentPhase);
  demoStatus.textContent = phase.status;
  phaseNumber.textContent = String(currentPhase + 1).padStart(2, "0");
  phaseTitle.textContent = phase.title;
  phaseCopy.textContent = phase.copy;

  if (currentPhase === phases.length - 1) {
    stopDemo(false);
    demoPlayLabel.textContent = "Replay routing";
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
  demoPlayLabel.textContent = "Pause routing";
  demoPlayIcon.textContent = "Ⅱ";
  scheduleNextPhase();
}

function stopDemo(resetLabel = true) {
  isDemoPlaying = false;
  window.clearTimeout(demoTimer);

  if (resetLabel) {
    demoPlayLabel.textContent = "Resume routing";
    demoPlayIcon.textContent = "▶";
  }
}

function restartDemo() {
  stopDemo(false);
  renderPhase(0);
  demoPlayLabel.textContent = "Play routing";
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
    const x = Math.max(34, Math.min(92, ((event.clientX - bounds.left) / bounds.width) * 100));
    const y = Math.max(16, Math.min(82, ((event.clientY - bounds.top) / bounds.height) * 100));

    hero.style.setProperty("--mx", `${x}%`);
    hero.style.setProperty("--my", `${y}%`);
    pointerFrame = 0;
  });
}

hero.addEventListener("pointermove", updateMeshPosition, { passive: true });
hero.addEventListener("pointerleave", () => {
  hero.style.setProperty("--mx", "73%");
  hero.style.setProperty("--my", "37%");
});

renderPhase(0);
