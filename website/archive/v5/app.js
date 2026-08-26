const documentBody = document.body;
const hero = document.querySelector(".hero");
const revealNodes = document.querySelectorAll(".reveal");
const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const demoStage = document.querySelector(".demo-stage");
const demoStatus = document.querySelector("#demo-status");
const phaseNumber = document.querySelector("#phase-number");
const phaseTitle = document.querySelector("#phase-title");
const phaseCopy = document.querySelector("#phase-copy");
const branchScene = document.querySelector("#branch-scene");

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
    copy: "Economical routes search, build, and test in parallel inside bounded workspaces.",
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
let isDemoVisible = false;
let pointerFrame = 0;
let branchPointerFrame = 0;

function renderPhase(nextPhase) {
  currentPhase = Math.max(0, Math.min(phases.length - 1, Number(nextPhase)));
  const phase = phases[currentPhase];

  demoStage.dataset.phase = String(currentPhase);
  demoStatus.textContent = phase.status;
  phaseNumber.textContent = String(currentPhase + 1).padStart(2, "0");
  phaseTitle.textContent = phase.title;
  phaseCopy.textContent = phase.copy;

  if (!prefersReducedMotion.matches && typeof phaseTitle.animate === "function") {
    [phaseNumber, phaseTitle, phaseCopy, demoStatus].forEach((node, index) => {
      node.getAnimations().forEach((animation) => animation.cancel());
      node.animate(
        [
          { opacity: 0.16, transform: "translateY(12px)", filter: "blur(3px)" },
          { opacity: 1, transform: "translateY(0)", filter: "blur(0)" },
        ],
        {
          duration: 520 + index * 55,
          delay: index * 28,
          easing: "cubic-bezier(0.16, 1, 0.3, 1)",
          fill: "both",
        },
      );
    });
  }
}

function stopRoutingLoop() {
  window.clearTimeout(demoTimer);
  demoTimer = 0;
}

function scheduleRoutingLoop(delay) {
  stopRoutingLoop();

  if (prefersReducedMotion.matches || !isDemoVisible || document.hidden) {
    return;
  }

  const pace = Number(demoSpeed.value);
  const phaseDelay = delay ?? (currentPhase === phases.length - 1 ? pace * 1.45 : pace);

  demoTimer = window.setTimeout(() => {
    renderPhase((currentPhase + 1) % phases.length);
    scheduleRoutingLoop();
  }, phaseDelay);
}

demoSpeed.addEventListener("change", () => {
  scheduleRoutingLoop();
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

function syncRoutingMotion() {
  stopRoutingLoop();

  if (prefersReducedMotion.matches) {
    renderPhase(phases.length - 1);
    return;
  }

  renderPhase(0);

  if (isDemoVisible) {
    scheduleRoutingLoop(Number(demoSpeed.value) * 0.7);
  }
}

if ("IntersectionObserver" in window) {
  const routingObserver = new IntersectionObserver(
    ([entry]) => {
      const wasVisible = isDemoVisible;
      isDemoVisible = entry.isIntersecting;

      if (isDemoVisible && !wasVisible) {
        syncRoutingMotion();
      } else if (!isDemoVisible) {
        stopRoutingLoop();
      }
    },
    { threshold: 0.18 },
  );

  routingObserver.observe(branchScene);
} else {
  isDemoVisible = true;
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopRoutingLoop();
  } else if (isDemoVisible && !prefersReducedMotion.matches) {
    scheduleRoutingLoop();
  }
});

prefersReducedMotion.addEventListener("change", syncRoutingMotion);

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

function updateBranchPerspective(event) {
  if (prefersReducedMotion.matches || documentBody.dataset.energy === "quiet") {
    return;
  }

  if (branchPointerFrame) {
    cancelAnimationFrame(branchPointerFrame);
  }

  branchPointerFrame = requestAnimationFrame(() => {
    const bounds = branchScene.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width - 0.5;
    const y = (event.clientY - bounds.top) / bounds.height - 0.5;

    branchScene.style.setProperty("--scene-rx", `${2.5 - y * 5}deg`);
    branchScene.style.setProperty("--scene-ry", `${-2.5 + x * 7}deg`);
    branchPointerFrame = 0;
  });
}

branchScene.addEventListener("pointermove", updateBranchPerspective, { passive: true });
branchScene.addEventListener("pointerleave", () => {
  branchScene.style.setProperty("--scene-rx", "2.5deg");
  branchScene.style.setProperty("--scene-ry", "-2.5deg");
});

syncRoutingMotion();
