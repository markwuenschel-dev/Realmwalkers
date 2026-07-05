import { Suspense } from "react";
import PipelineScreen from "../../desk/screens/PipelineScreen";

export default function Page() {
  return (
    <Suspense fallback={null}>
      <PipelineScreen />
    </Suspense>
  );
}
