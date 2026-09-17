import { Suspense } from "react";
import NotesScreen from "../../desk/screens/NotesScreen";

// Suspense: NotesScreen reads `?id=` (useSearchParams) to open a specific read-through.
export default function Page() {
  return (
    <Suspense fallback={null}>
      <NotesScreen />
    </Suspense>
  );
}
