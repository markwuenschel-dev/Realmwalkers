import { expect, test } from "@playwright/test";

// Smoke test: the Writers' Desk shell must actually render.
//
// This is a genuine regression guard, not a fake-green placeholder. It exercises the full client
// boot path that every route depends on:
//   RootLayout (app/layout.tsx)
//     -> Providers (app/providers.tsx)
//       -> DeskDataProvider -> DeskProvider -> DeskShell -> TopBar
// If the Next build breaks, a provider throws, an import goes stale, or hydration fails, the desk
// chrome below never renders and these assertions fail. Nothing here asserts a tautology.
//
// Hermetic to what CI provides: the config boots `pnpm build && pnpm start` on :3000 with no
// backend. The banner text and nav are static chrome (not backend-derived), so the smoke passes
// even though the backend/BackendBanner is unreachable in CI — it does NOT depend on live data.

test("root redirects into the Desk and the shell renders", async ({ page }) => {
  // `/` issues a server redirect to `/inbox` (app/page.tsx). Following it guards that redirect too.
  await page.goto("/");
  await expect(page).toHaveURL(/\/inbox$/);

  // Document title comes from the root layout metadata — proves the HTML document rendered.
  await expect(page).toHaveTitle(/Writers' Desk/);

  // The TopBar <header> is the banner landmark, rendered by a client component deep inside the
  // provider tree. Its presence proves the React desk shell actually mounted and rendered.
  const banner = page.getByRole("banner");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("The Dominion Realm");

  // The desk navigation must be present with its first, always-on route (Inbox). This is the
  // load-bearing nav landmark the whole app relies on.
  const nav = page.getByRole("navigation");
  await expect(nav).toBeVisible();
  await expect(nav.getByRole("link", { name: /Inbox/ })).toBeVisible();
});
