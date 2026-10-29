import { pathToFileURL } from 'node:url';
import { existsSync } from 'node:fs';
const fallback = '/Users/perry/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs';
let playwright;
if (process.env.HOMEWORK_PLAYWRIGHT_MODULE) playwright = await import(pathToFileURL(process.env.HOMEWORK_PLAYWRIGHT_MODULE));
else { try { playwright = await import('playwright'); } catch { playwright = await import(pathToFileURL(fallback)); } }
const chrome = process.env.HOMEWORK_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const browser = await playwright.chromium.launch({headless:true, ...(existsSync(chrome) ? {executablePath:chrome} : {})});
const page = await browser.newPage({viewport:{width:1500,height:1000},deviceScaleFactor:1});
await page.goto(pathToFileURL(process.argv[2]).href, {waitUntil:'load'});
await page.screenshot({path:process.argv[3],fullPage:true});
await browser.close();
