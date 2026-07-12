// SkillRACE — lightweight progressive enhancement.
// Everything degrades gracefully: with JS off, the page is fully readable.

document.addEventListener("DOMContentLoaded", () => {
  const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ── scroll progress bar ── */
  const bar = document.getElementById("progress-bar");
  if (bar) {
    const update = () => {
      const h = document.documentElement;
      const scrolled = h.scrollTop;
      const max = h.scrollHeight - h.clientHeight;
      bar.style.width = max > 0 ? (scrolled / max) * 100 + "%" : "0%";
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
  }

  /* ── reveal-on-scroll ── */
  const revealTargets = document.querySelectorAll(
    ".card, .step, .rq, .panel, .setup-item, .ex-card, .example-takeaway, .pipeline, .abstract-body, .section-title, .section-sub"
  );
  if (prefersReduced || !("IntersectionObserver" in window)) {
    revealTargets.forEach((el) => el.classList.add("in"));
  } else {
    revealTargets.forEach((el) => el.classList.add("reveal"));
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry, i) => {
          if (entry.isIntersecting) {
            const el = entry.target;
            // gentle stagger within a group
            const delay = Math.min(i * 60, 240);
            setTimeout(() => el.classList.add("in"), delay);
            io.unobserve(el);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -8% 0px" }
    );
    revealTargets.forEach((el) => io.observe(el));

    // Failsafe: nothing should ever stay invisible. If the observer never
    // fires for an element (JS hiccup, headless render, print, etc.), reveal
    // whatever is still hidden after a grace period.
    const failsafe = () => {
      document.querySelectorAll(".reveal:not(.in)").forEach((el) => {
        io.unobserve(el);
        el.classList.add("in");
      });
    };
    window.setTimeout(failsafe, 2500);
    window.addEventListener("beforeprint", failsafe);
  }

  /* ── copy BibTeX ── */
  const copyBtn = document.getElementById("copy-cite");
  const bib = document.getElementById("bibtex");
  if (copyBtn && bib) {
    copyBtn.addEventListener("click", async () => {
      const text = bib.innerText;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
        } else {
          const ta = document.createElement("textarea");
          ta.value = text;
          ta.style.position = "fixed";
          ta.style.opacity = "0";
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
        }
        copyBtn.textContent = "Copied ✓";
        copyBtn.classList.add("copied");
        setTimeout(() => {
          copyBtn.textContent = "Copy";
          copyBtn.classList.remove("copied");
        }, 1800);
      } catch (e) {
        copyBtn.textContent = "Copy failed";
        setTimeout(() => (copyBtn.textContent = "Copy"), 1800);
      }
    });
  }
});
