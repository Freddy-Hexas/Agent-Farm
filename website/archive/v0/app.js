const documentBody = document.body;
const hero = document.querySelector(".hero-primary");
const revealNodes = document.querySelectorAll(".reveal");
const tweaksTrigger = document.querySelector(".tweaks-trigger");
const tweaksPanel = document.querySelector(".tweaks-panel");
const tweaksClose = document.querySelector(".tweaks-close");
const motionMode = document.querySelector("#motion-mode");
const densityToggle = document.querySelector("#density-toggle");

const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function setTweaksVisibility(isOpen) {
  tweaksPanel.classList.toggle("is-open", isOpen);
  tweaksPanel.setAttribute("aria-hidden", String(!isOpen));
  tweaksTrigger.setAttribute("aria-expanded", String(isOpen));
  tweaksTrigger.hidden = isOpen;

  if (isOpen) {
    motionMode.focus();
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

motionMode.addEventListener("change", (event) => {
  documentBody.dataset.motion = event.target.value;
});

densityToggle.addEventListener("change", (event) => {
  documentBody.dataset.density = event.target.checked ? "dense" : "focused";
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
    { rootMargin: "0px 0px -8%", threshold: 0.08 },
  );

  revealNodes.forEach((node) => revealObserver.observe(node));
} else {
  revealNodes.forEach((node) => node.classList.add("is-visible"));
}

let pointerFrame = 0;

function updateMeshPosition(event) {
  if (prefersReducedMotion.matches || documentBody.dataset.motion === "quiet") {
    return;
  }

  if (pointerFrame) {
    cancelAnimationFrame(pointerFrame);
  }

  pointerFrame = requestAnimationFrame(() => {
    const bounds = hero.getBoundingClientRect();
    const x = Math.max(25, Math.min(88, ((event.clientX - bounds.left) / bounds.width) * 100));
    const y = Math.max(18, Math.min(78, ((event.clientY - bounds.top) / bounds.height) * 100));

    hero.style.setProperty("--mx", `${x}%`);
    hero.style.setProperty("--my", `${y}%`);
    pointerFrame = 0;
  });
}

hero.addEventListener("pointermove", updateMeshPosition, { passive: true });
hero.addEventListener("pointerleave", () => {
  hero.style.setProperty("--mx", "64%");
  hero.style.setProperty("--my", "34%");
});

document.querySelectorAll(".review-tabs button").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".review-tabs button").forEach((item) => {
      const selected = item === tab;
      item.classList.toggle("is-active", selected);
      item.setAttribute("aria-selected", String(selected));
    });
  });
});
