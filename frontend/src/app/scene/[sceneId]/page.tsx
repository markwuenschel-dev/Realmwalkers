import SceneScreen from "../../../desk/screens/SceneScreen";

// A specific scene (e.g. an approved one opened from the board). SceneScreen reads `sceneId` from
// the route params and loads it ahead of the pending queue.
export default function Page() {
  return <SceneScreen />;
}
