import DiffScreen from "../../../desk/screens/DiffScreen";

// Version history for a specific scene. DiffScreen loads `sceneId` from the route params so the URL
// is shareable and survives a refresh.
export default function Page() {
  return <DiffScreen />;
}
