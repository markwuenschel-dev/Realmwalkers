import { Suspense } from "react";
import PacketsScreen from "../../desk/screens/PacketsScreen";

export default function Page() {
  return (
    <Suspense fallback={null}>
      <PacketsScreen />
    </Suspense>
  );
}
