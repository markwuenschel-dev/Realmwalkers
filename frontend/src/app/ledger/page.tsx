import { Suspense } from "react";
import LedgerScreen from "../../desk/screens/LedgerScreen";

// useSearchParams (the ?cat&focus deep link) requires a Suspense boundary at prerender —
// same wrapper the packets/production pages use.
export default function Page() {
  return (
    <Suspense fallback={null}>
      <LedgerScreen />
    </Suspense>
  );
}
