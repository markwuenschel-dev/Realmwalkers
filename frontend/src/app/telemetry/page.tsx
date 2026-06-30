import { Suspense } from "react";
import TelemetryScreen from "../../desk/screens/TelemetryScreen";

export default function Page() {
  return (
    <Suspense fallback={null}>
      <TelemetryScreen />
    </Suspense>
  );
}
