import { useEffect, useState } from "react";

import { ChartPage } from "./pages/ChartPage";
import { DecisionsPage } from "./pages/DecisionsPage";
import { HealthPage } from "./pages/HealthPage";
import { OverviewPage } from "./pages/OverviewPage";
import { TradesPage } from "./pages/TradesPage";

const routes = ["overview", "chart", "decisions", "trades", "health"] as const;
type Route = (typeof routes)[number];

function currentRoute(): Route {
  const route = window.location.hash.slice(1);
  return routes.includes(route as Route) ? (route as Route) : "overview";
}

export default function App() {
  const [route, setRoute] = useState<Route>(currentRoute);
  useEffect(() => {
    const onHashChange = () => setRoute(currentRoute());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a href="#overview" className="brand" aria-label="Adaptive Trading Bot overview">
          <span className="brand-mark">AT</span>
          <span><strong>Adaptive</strong><small>Observer console</small></span>
        </a>
        <nav aria-label="Dashboard navigation">
          <NavLink route="overview" current={route}>Overview / Market</NavLink>
          <NavLink route="chart" current={route}>Chart</NavLink>
          <NavLink route="decisions" current={route}>Decisions</NavLink>
          <NavLink route="trades" current={route}>Trades</NavLink>
          <NavLink route="health" current={route}>Health</NavLink>
          <span className="nav-disabled" aria-disabled="true">Experience <small>Phase 12</small></span>
        </nav>
        <div className="observer-lock"><span aria-hidden="true">◉</span><div><strong>Read-only</strong><small>Observation APIs only</small></div></div>
      </aside>
      <div className="content">
        <div className="topbar"><span>V1 · BTC / ETH</span><span className="read-only-pill">Observer mode</span></div>
        {route === "overview" && <OverviewPage />}
        {route === "chart" && <ChartPage />}
        {route === "decisions" && <DecisionsPage />}
        {route === "trades" && <TradesPage />}
        {route === "health" && <HealthPage />}
      </div>
    </div>
  );
}

function NavLink({ route, current, children }: { route: Route; current: Route; children: React.ReactNode }) {
  return <a href={`#${route}`} className={current === route ? "active" : undefined} aria-current={current === route ? "page" : undefined}>{children}</a>;
}
