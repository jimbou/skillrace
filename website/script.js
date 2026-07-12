document.addEventListener("DOMContentLoaded", () => {
  // Top navigation + active section.
  const navLinks = document.querySelectorAll(".topbar nav a");
  const sections = Array.from(document.querySelectorAll("main section[id]"));
  const linkForId = new Map();
  const lastSectionId = sections.at(-1)?.id;
  const topbar = document.querySelector(".topbar");

  navLinks.forEach((link) => {
    const id = link.getAttribute("href")?.replace("#", "");
    if (id) linkForId.set(id, link);
  });

  const setActiveSection = (id) => {
    if (!id) return;
    navLinks.forEach((link) => link.classList.remove("is-active"));
    const activeLink = linkForId.get(id);
    if (activeLink) activeLink.classList.add("is-active");
  };

  const onScrollNav = () => {
    const y = window.scrollY + 130;

    const isAtBottom =
      window.scrollY + window.innerHeight >=
      document.documentElement.scrollHeight - 18;
    if (isAtBottom && lastSectionId) {
      setActiveSection(lastSectionId);
      return;
    }

    let active = sections[0]?.id;
    for (const section of sections) {
      if (section.offsetTop <= y) active = section.id;
    }
    setActiveSection(active);
  };

  const progress = document.querySelector(".scroll-progress span");
  const onScrollProgress = () => {
    const total = Math.max(document.documentElement.scrollHeight - window.innerHeight, 1);
    const pct = Math.min(100, Math.round((window.scrollY / total) * 100));
    if (progress) progress.style.width = `${pct}%`;
  };

  // Back-to-top control.
  const toTop = document.getElementById("to-top");
  const onToTop = () => {
    if (!toTop) return;
    toTop.classList.toggle("is-visible", window.scrollY > 520);
  };

  const scrollToSection = (id) => {
    const section = document.getElementById(id);
    if (!section) return;
    const headerHeight = topbar?.offsetHeight ?? 100;
    const targetY = Math.max(0, section.getBoundingClientRect().top + window.scrollY - headerHeight - 12);
    if (window.location.hash !== `#${id}`) {
      history.replaceState(null, "", `#${id}`);
    }
    window.scrollTo({ top: targetY, behavior: "smooth" });
    setActiveSection(id);
  };

  window.addEventListener("scroll", onScrollNav, { passive: true });
  window.addEventListener("scroll", onScrollProgress, { passive: true });
  window.addEventListener("scroll", onToTop, { passive: true });
  onScrollNav();
  onScrollProgress();
  onToTop();

  navLinks.forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const id = link.getAttribute("href")?.replace("#", "");
      if (id) {
        scrollToSection(id);
      }
    });
  });

  if (toTop) {
    toTop.addEventListener("click", () => {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  // Card reveal with light stagger.
  const cards = document.querySelectorAll(".card, .feature-card, .metric-card, .arch-card, .step-detail");
  cards.forEach((card, idx) => card.style.setProperty("--delay", `${(idx % 12) * 0.045}s`));

  const cardObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          entry.target.classList.add("in-view");
          cardObserver.unobserve(entry.target);
        }
      }
    },
    { threshold: 0.12 }
  );
  cards.forEach((card) => cardObserver.observe(card));

  // Method comparison micro-interaction.
  const compareCopy = {
    random: {
      text: "Random ignores execution feedback and samples candidates from the base generator.",
      note: "No search loop. No branch feedback. Minimal exploration."
    },
    verigrey: {
      text: "VeriGrey tracks novelty of transition states and steers sampling by event-level diversity.",
      note: "Useful novelty pressure, with limited branch-level target specificity."
    },
    skillrace: {
      text: "SkillRACE mutates promising branches using episode signatures and validity checks.",
      note: "Designed to expand control-flow coverage and uncover brittle edges."
    },
  };

  const modeTabs = document.querySelectorAll(".compare-tab");
  const compareText = document.getElementById("compare-copy");
  const compareNote = document.getElementById("compare-note");

  const setCompareMode = (mode) => {
    if (!compareCopy[mode] || !compareText || !compareNote) return;
    modeTabs.forEach((tab) => {
      const isActive = tab.dataset.mode === mode;
      tab.classList.toggle("is-active", isActive);
      tab.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    compareText.textContent = compareCopy[mode].text;
    compareNote.textContent = compareCopy[mode].note;
  };

  modeTabs.forEach((tab) => {
    tab.addEventListener("click", () => setCompareMode(tab.dataset.mode));
  });
  setCompareMode("random");

  // Animated counters in overview.
  const animateStat = (el) => {
    const target = Number.parseInt(el.dataset.target || "0", 10);
    if (!Number.isFinite(target) || target < 0) return;
    const start = performance.now();
    const duration = 900;

    const step = (now) => {
      const progress = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      el.textContent = `${Math.round(eased * target)}`;
      if (progress < 1) requestAnimationFrame(step);
    };

    requestAnimationFrame(step);
  };

  const io = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const stat = entry.target.querySelector(".stat-num");
        if (!stat || stat.classList.contains("done")) continue;
        animateStat(stat);
        stat.classList.add("done");
      }
    },
    { threshold: 0.2 }
  );

  const protocolSection = document.querySelector("#protocol-overview");
  if (protocolSection) io.observe(protocolSection);
});
