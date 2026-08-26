const documentBody = document.body;
const siteHeader = document.querySelector(".site-header");
const menuToggle = document.querySelector(".menu-toggle");
const primaryNav = document.querySelector("#primary-nav");
const navLinks = document.querySelectorAll(".site-nav a");
const revealNodes = document.querySelectorAll(".reveal");
const hero = document.querySelector(".hero");
const economicsChart = document.querySelector("#economics-chart");
const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const architectureStage = document.querySelector(".architecture-stage");
const architectureStatus = document.querySelector("#architecture-status");
const phaseNumber = document.querySelector("#phase-number");
const phaseTitle = document.querySelector("#phase-title");
const phaseCopy = document.querySelector("#phase-copy");
const branchScene = document.querySelector("#branch-scene");

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
    title: "Workers take bounded work.",
    copy: "Economical routes search, build, and test in parallel inside isolated workspaces.",
    status: "EXECUTING",
  },
  {
    title: "Evidence converges.",
    copy: "Patches, tests, usage, and scope checks return to one review boundary.",
    status: "EVIDENCE GATE",
  },
  {
    title: "Judgment makes the final call.",
    copy: "The Supervisor accepts, revises, or rejects with the complete record visible.",
    status: "DECISION READY",
  },
];

let currentPhase = 0;
let routingTimer = 0;
let routingVisible = false;
let headerFrame = 0;
let heroPointerFrame = 0;
let branchPointerFrame = 0;

function setMenuOpen(isOpen) {
  siteHeader.classList.toggle("menu-open", isOpen);
  menuToggle.setAttribute("aria-expanded", String(isOpen));
  menuToggle.setAttribute("aria-label", isOpen ? "Close navigation" : "Open navigation");
  documentBody.classList.toggle("menu-is-open", isOpen);
}

menuToggle.addEventListener("click", () => {
  setMenuOpen(!siteHeader.classList.contains("menu-open"));
});

navLinks.forEach((link) => {
  link.addEventListener("click", () => setMenuOpen(false));
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    setMenuOpen(false);
  }
});

window.addEventListener("resize", () => {
  if (window.innerWidth > 720) {
    setMenuOpen(false);
  }
});

function updateHeaderState() {
  siteHeader.classList.toggle("is-scrolled", window.scrollY > 18);
  headerFrame = 0;
}

window.addEventListener(
  "scroll",
  () => {
    if (!headerFrame) {
      headerFrame = requestAnimationFrame(updateHeaderState);
    }
  },
  { passive: true },
);

updateHeaderState();

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

if (economicsChart) {
  if ("IntersectionObserver" in window && !prefersReducedMotion.matches) {
    const economicsObserver = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          economicsChart.classList.add("is-chart-active");
          economicsObserver.unobserve(economicsChart);
        }
      },
      { rootMargin: "0px 0px -12%", threshold: 0.18 },
    );

    economicsObserver.observe(economicsChart);
  } else {
    economicsChart.classList.add("is-chart-active");
  }
}

if ("IntersectionObserver" in window) {
  const sectionObserver = new IntersectionObserver(
    (entries) => {
      const visibleEntry = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];

      if (!visibleEntry) {
        return;
      }

      navLinks.forEach((link) => {
        const isCurrent = link.getAttribute("href") === `#${visibleEntry.target.id}`;
        if (isCurrent) {
          link.setAttribute("aria-current", "true");
        } else {
          link.removeAttribute("aria-current");
        }
      });
    },
    { rootMargin: "-20% 0px -60%", threshold: [0.05, 0.2, 0.45] },
  );

  ["product", "workflow", "controls", "architecture"].forEach((id) => {
    const section = document.getElementById(id);
    if (section) {
      sectionObserver.observe(section);
    }
  });
}

function animatePhaseText() {
  if (prefersReducedMotion.matches || typeof phaseTitle.animate !== "function") {
    return;
  }

  [phaseNumber, phaseTitle, phaseCopy, architectureStatus].forEach((node, index) => {
    node.getAnimations().forEach((animation) => animation.cancel());
    node.animate(
      [
        { opacity: 0.2, transform: "translateY(10px)", filter: "blur(2px)" },
        { opacity: 1, transform: "translateY(0)", filter: "blur(0)" },
      ],
      {
        duration: 480 + index * 45,
        delay: index * 24,
        easing: "cubic-bezier(0.16, 1, 0.3, 1)",
        fill: "both",
      },
    );
  });
}

function renderPhase(nextPhase) {
  currentPhase = Math.max(0, Math.min(phases.length - 1, Number(nextPhase)));
  const phase = phases[currentPhase];

  architectureStage.dataset.phase = String(currentPhase);
  architectureStatus.textContent = phase.status;
  phaseNumber.textContent = String(currentPhase + 1).padStart(2, "0");
  phaseTitle.textContent = phase.title;
  phaseCopy.textContent = phase.copy;
  animatePhaseText();
}

function stopRoutingLoop() {
  window.clearTimeout(routingTimer);
  routingTimer = 0;
}

function scheduleRoutingLoop(delay) {
  stopRoutingLoop();

  if (prefersReducedMotion.matches || !routingVisible || document.hidden) {
    return;
  }

  const phaseDelay = delay ?? (currentPhase === phases.length - 1 ? 2200 : 1500);
  routingTimer = window.setTimeout(() => {
    renderPhase((currentPhase + 1) % phases.length);
    scheduleRoutingLoop();
  }, phaseDelay);
}

function syncRoutingMotion() {
  stopRoutingLoop();

  if (prefersReducedMotion.matches) {
    renderPhase(phases.length - 1);
    return;
  }

  renderPhase(0);
  if (routingVisible) {
    scheduleRoutingLoop(950);
  }
}

if ("IntersectionObserver" in window) {
  const routingObserver = new IntersectionObserver(
    ([entry]) => {
      const wasVisible = routingVisible;
      routingVisible = entry.isIntersecting;

      if (routingVisible && !wasVisible) {
        syncRoutingMotion();
      } else if (!routingVisible) {
        stopRoutingLoop();
      }
    },
    { threshold: 0.16 },
  );

  routingObserver.observe(branchScene);
} else {
  routingVisible = true;
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopRoutingLoop();
  } else if (routingVisible && !prefersReducedMotion.matches) {
    scheduleRoutingLoop();
  }
});

prefersReducedMotion.addEventListener("change", syncRoutingMotion);

function updateHeroMesh(event) {
  if (prefersReducedMotion.matches) {
    return;
  }

  if (heroPointerFrame) {
    cancelAnimationFrame(heroPointerFrame);
  }

  heroPointerFrame = requestAnimationFrame(() => {
    const bounds = hero.getBoundingClientRect();
    const x = Math.max(48, Math.min(94, ((event.clientX - bounds.left) / bounds.width) * 100));
    const y = Math.max(18, Math.min(82, ((event.clientY - bounds.top) / bounds.height) * 100));

    hero.style.setProperty("--mx", `${x}%`);
    hero.style.setProperty("--my", `${y}%`);
    heroPointerFrame = 0;
  });
}

hero.addEventListener("pointermove", updateHeroMesh, { passive: true });
hero.addEventListener("pointerleave", () => {
  hero.style.setProperty("--mx", "74%");
  hero.style.setProperty("--my", "34%");
});

function updateBranchPerspective(event) {
  if (prefersReducedMotion.matches || window.innerWidth <= 720) {
    return;
  }

  if (branchPointerFrame) {
    cancelAnimationFrame(branchPointerFrame);
  }

  branchPointerFrame = requestAnimationFrame(() => {
    const bounds = branchScene.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width - 0.5;
    const y = (event.clientY - bounds.top) / bounds.height - 0.5;

    branchScene.style.setProperty("--scene-rx", `${2.3 - y * 4.5}deg`);
    branchScene.style.setProperty("--scene-ry", `${-2.3 + x * 6.5}deg`);
    branchPointerFrame = 0;
  });
}

branchScene.addEventListener("pointermove", updateBranchPerspective, { passive: true });
branchScene.addEventListener("pointerleave", () => {
  branchScene.style.setProperty("--scene-rx", "2.3deg");
  branchScene.style.setProperty("--scene-ry", "-2.3deg");
});

syncRoutingMotion();
