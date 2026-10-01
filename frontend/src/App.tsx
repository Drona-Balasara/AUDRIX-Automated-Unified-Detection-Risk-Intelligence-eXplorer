import { AppShell } from "./components/AppShell";
import { useHealth } from "./hooks/useHealth";
import { Overview } from "./pages/Overview";

export default function App() {
  const { refresh, ...health } = useHealth();
  const environment = health.status === "connected" ? health.data?.environment : undefined;

  return (
    <AppShell environment={environment}>
      <Overview health={health} onRetry={refresh} />
    </AppShell>
  );
}
