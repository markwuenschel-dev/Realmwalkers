import DiffScreen from "../../desk/screens/DiffScreen";

// Compares the currently-loaded scene's versions. A shareable, refresh-safe history lives at
// /diff/[sceneId].
export default function Page() {
  return <DiffScreen />;
}
