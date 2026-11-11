import { test, expect, type Page } from "@playwright/test";

async function mockAuth(page: Page, { failLogin = false, expire = false } = {}) {
  const requests: { path: string; authorization?: string }[] = [];
  await page.route("**://localhost:8000/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/auth/login" || path === "/auth/register") {
      const body = route.request().postDataJSON();
      if (failLogin) return route.fulfill({ status: 401, json: { detail: "Invalid email or password" } });
      return route.fulfill({ status: path.endsWith("register") ? 201 : 200,
        json: { access_token: `token-${body.email}`, user_id: body.email.startsWith("other") ? "usr_other" : "usr_learner", email: body.email } });
    }
    const authorization = route.request().headers().authorization;
    requests.push({ path, authorization });
    if (!authorization || expire) return route.fulfill({ status: 401, json: { detail: "Token expired or invalid" } });
    if (path.includes("/knowledge")) {
      expect(route.request().headers()["content-type"]).toContain("multipart/form-data; boundary=");
      return route.fulfill({ json: { filename: "notes.pdf", chunks: 1 } });
    }
    if (path === "/sessions/usr_learner") return route.fulfill({ json: [{ session_id: "private", title: "Learner private path", updated_at: null }] });
    return route.fulfill({ json: [] });
  });
  return requests;
}

async function login(page: Page, email = "learner@example.com") {
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill("fixture-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

test("legacy email does not authenticate; password login uses server identity and survives reload", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mindmorph.userId", "legacy@example.com"));
  const requests = await mockAuth(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  expect(requests).toHaveLength(0);
  await login(page);
  await expect(page.getByText("Learner private path", { exact: true })).toBeVisible();
  expect(requests.some(r => r.path === "/sessions/usr_learner")).toBeTruthy();
  expect(requests.every(r => r.authorization === "Bearer token-learner@example.com")).toBeTruthy();
  const stored = await page.evaluate(() => sessionStorage.getItem("mindmorph.auth"));
  expect(stored).toContain('"userId":"usr_learner"');
  expect(stored).not.toContain("fixture-password");
  await page.reload();
  await expect(page.getByText("Learner private path", { exact: true })).toBeVisible();
});

test("registration obtains credentials and rejected login remains signed out", async ({ page }) => {
  await mockAuth(page, { failLogin: true });
  await page.goto("/");
  await login(page);
  await expect(page.locator("form").getByRole("alert")).toHaveText("Invalid email or password");
  expect(await page.evaluate(() => sessionStorage.getItem("mindmorph.auth"))).toBeNull();
  await page.unroute("**://localhost:8000/**");
  const requests = await mockAuth(page);
  await page.getByRole("button", { name: "Create an account", exact: true }).click();
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await expect(page.getByText("Learner private path", { exact: true })).toBeVisible();
  expect(requests.every(r => r.authorization === "Bearer token-learner@example.com")).toBeTruthy();
});

test("sign-out and another account clear cached paths while retaining drafts", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mindmorph.draft.retained", "Unfinished answer"));
  await mockAuth(page);
  await page.goto("/");
  await login(page);
  await expect(page.getByText("Learner private path", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /learner@example.com.*sign out/ }).click();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("mindmorph.auth"))).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem("mindmorph.draft.retained"))).toBe("Unfinished answer");
  await login(page, "other@example.com");
  await expect(page.getByRole("button", { name: /other@example.com.*sign out/ })).toBeVisible();
  await expect(page.getByText("Learner private path", { exact: true })).toHaveCount(0);
});

test("expired credentials return to sign-in without deleting a draft", async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("mindmorph.auth", JSON.stringify({ accessToken: "expired", userId: "usr_learner", email: "learner@example.com" }));
    localStorage.setItem("mindmorph.draft.retained", "Keep my work");
  });
  await mockAuth(page, { expire: true });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("mindmorph.auth"))).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem("mindmorph.draft.retained"))).toBe("Keep my work");
});

test("multipart document upload includes the authenticated bearer token", async ({ page }) => {
  const requests = await mockAuth(page);
  await page.goto("/");
  await login(page);
  await expect(page.getByText("Learner private path", { exact: true })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles({ name: "notes.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\nfixture") });
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect.poll(() => requests.some(r => r.path === "/users/usr_learner/knowledge")).toBeTruthy();
  expect(requests.filter(r => r.path.includes("/knowledge"))[0].authorization).toBe("Bearer token-learner@example.com");
});
