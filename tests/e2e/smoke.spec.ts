import { test, expect } from "@playwright/test";

test("landing page loads", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/JOBSIM/);
});

test("hero section is visible", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".hero-h")).toBeVisible();
});

test("simulation section exists", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#simulation")).toBeVisible();
});

test("app container mounts and loads missions", async ({ page }) => {
  await page.goto("/");
  // #app should eventually leave the loading state
  await expect(page.locator("#app")).not.toBeEmpty({ timeout: 15_000 });
  // job cards should appear (select screen)
  await expect(page.locator(".jc").first()).toBeVisible({ timeout: 15_000 });
});
