import { useCallback, useEffect, useState } from "react";
import Console from "./Console";
import Landing from "./Landing";

// Hash routes so the API can serve the app as static files: #/, #/console, #/console/<run|new>
function useRoute(): [string, (p: string) => void] {
  const read = () => (location.hash.replace(/^#/, "") || "/");
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const legacy = new URLSearchParams(location.search).get("run");
    if (legacy) { history.replaceState(null, "", location.pathname + `#/console/${legacy}`); setRoute(`/console/${legacy}`); }
    const onHash = () => setRoute(read());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const navigate = useCallback((p: string) => {
    if (read() !== p) location.hash = p;
    window.scrollTo({ top: 0 });
  }, []);
  return [route, navigate];
}

export default function App() {
  const [route, navigate] = useRoute();
  const inConsole = route.startsWith("/console");
  const runId = inConsole ? route.split("/")[2] ?? null : null;
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const go = (anchor: string) => {
    if (inConsole) { navigate("/"); setTimeout(() => document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth" }), 60); }
    else document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <div className={`app ${inConsole ? "is-console" : "is-site"}`}>
      <header className={`nav ${scrolled || inConsole ? "nav-solid" : ""}`}>
        <div className="nav-inner">
          <a className="wordmark" href="#/" onClick={(e) => { e.preventDefault(); navigate("/"); }} aria-label="Forge home">
            <Mark />
            <span>Forge</span>
          </a>
          <nav className="nav-links" aria-label="Primary">
            <button onClick={() => go("platform")}>Platform</button>
            <button onClick={() => go("controls")}>Controls</button>
            <button onClick={() => go("results")}>Results</button>
            <button onClick={() => go("autonomy")}>Governance</button>
          </nav>
          <div className="nav-cta">
            {inConsole
              ? <button className="btn btn-quiet btn-sm" onClick={() => navigate("/")}>Overview</button>
              : <button className="nav-doc" onClick={() => go("footer")}>Docs</button>}
            <button className="btn btn-primary btn-sm" onClick={() => navigate(inConsole ? "/console/new" : "/console")}>
              {inConsole ? "New run" : "Open console"}
            </button>
          </div>
        </div>
      </header>
      {inConsole ? <Console runId={runId} navigate={navigate} /> : <Landing navigate={navigate} />}
    </div>
  );
}

export function Mark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" className="mark">
      <rect x="0.5" y="0.5" width="23" height="23" rx="4" fill="currentColor" />
      <path d="M7 6h10v2.6H9.6v2.3H15v2.6H9.6V18H7z" fill="var(--mark-ink, #fff)" />
    </svg>
  );
}
