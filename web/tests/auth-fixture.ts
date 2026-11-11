import type { Page } from "@playwright/test";

// Explicit credentials for mocked APIs only. Production never authenticates a
// localStorage email. Existing fixtures keep their user IDs to preserve drafts.
export async function seedUser(page: Page, userId: string) {
  await page.route("**/exercises/*", route => route.request().method() === "GET"
    ? route.fulfill({ json: [] }) : route.fallback());
  await page.addInitScript((id) => {
    if (!sessionStorage.getItem("mindmorph.auth")) {
      const user = localStorage.getItem("mindmorph.userId") || id;
      sessionStorage.setItem("mindmorph.auth", JSON.stringify({
        accessToken: "fixture-access-token", userId: user, email: user,
      }));
    }
  }, userId);
}
