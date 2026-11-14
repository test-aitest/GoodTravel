const store = "http://127.0.0.1:3002";
const sku = "KC-ETH-200";
const target = "6";
const saveLabel = "Save changes";

const username = 'input[name="username"]';
const password = 'input[name="password"][type="password"]';
const signIn = 'form[action="/login"] button[type="submit"]';
const field = `form[action="/inventory"] input[name="available-${sku}"]`;
const save = 'form[action="/inventory"] button[type="submit"][name="action"][value="save"]';
const status = '[role="status"]';

const page = new Page();
const fail = (message) => {
  throw new Error(`check failed: ${message}`);
};
const expectOne = (selector) => {
  const found = page.extract({ matches: [selector] }).matches.length;
  if (found !== 1) fail(`${selector} matched ${found} elements, expected 1`);
};
const path = () => page.evaluate("location.pathname");
const valueOf = (selector) => page.evaluate(`document.querySelector(${JSON.stringify(selector)}).value`);

await page.goto(`${store}/login`);
for (const selector of [username, password, signIn]) expectOne(selector);
page.fill(username, "$LP_STORE_USERNAME");
page.fill(password, "$LP_STORE_PASSWORD");
page.click(signIn);
if (path() !== "/inventory") fail(`after signing in the page is ${path()}, expected /inventory`);

expectOne(field);
expectOne(save);
const { label } = page.extract({ label: save });
if (label !== saveLabel) fail(`${save} says "${label}", expected "${saveLabel}"`);
const before = valueOf(field);
page.fill(field, target);
if (valueOf(field) !== target) fail(`${field} is ${valueOf(field)} after filling, expected ${target}`);

page.click(save);
expectOne(status);
const { message } = page.extract({ message: status });
if (path() !== "/inventory" || !message.startsWith("Saved.")) fail(`after saving the page is ${path()} and says "${message}"`);
const after = valueOf(field);
if (after !== target) fail(`${field} reads ${after} after saving, expected ${target}`);

return { sku, before, after, message };
