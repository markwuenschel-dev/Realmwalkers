import { Suspense } from "react";
import ProductionScreen from "../../desk/screens/ProductionScreen";

export default function Page() {
  return (
    <Suspense fallback={null}>
      <ProductionScreen />
    </Suspense>
  );
}
