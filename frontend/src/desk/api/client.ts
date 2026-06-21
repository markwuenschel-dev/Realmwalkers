// The Writers' Desk API boundary. The desk reuses the review-app's typed client and wire DTOs
// (frontend/src/legacy) rather than re-implementing them; adapters.ts maps these DTOs into the desk
// view-models declared in ../types.ts. Import the live API as `import { api } from "../api/client"`.
export { api } from "../../legacy/api/client";
export type {
  AnnotationIn,
  AnnotationOut,
  BeatOut,
  BookOut,
  CanonOut,
  ChapterOut,
  CharacterOut,
  Critique,
  ContinuityResolveIn,
  DecisionIn,
  ManuscriptChapter,
  ManuscriptOut,
  ManuscriptScene,
  SceneDetail,
  SceneOut,
  SceneVersionOut,
  SuggestionIn,
  SuggestionOut,
  ThreadOut,
} from "../../legacy/types";
