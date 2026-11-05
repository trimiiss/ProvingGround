import { createHash, createHmac, timingSafeEqual } from "node:crypto";

/**
 * Dashboard login. Accounts come from PGROUND_WEB_USERS ("alice:password,bob:password"); with none
 * set, nobody can sign in. A session is a signed cookie, so there is nothing to store on the server.
 *
 * The signing key is derived from the accounts and the admin key, so changing either signs everyone out.
 */

export const COOKIE = "pg_session";
export const MAX_AGE_S = 7 * 24 * 60 * 60;

const sha256 = (text: string) => createHash("sha256").update(text).digest();
const same = (a: string, b: string) => timingSafeEqual(sha256(a), sha256(b));

function accounts(): Map<string, string> {
  const found = new Map<string, string>();
  for (const entry of (process.env.PGROUND_WEB_USERS ?? "").split(",")) {
    const colon = entry.indexOf(":");
    const [name, password] = [entry.slice(0, colon).trim(), entry.slice(colon + 1).trim()];
    if (colon > 0 && name && password) found.set(name, password);
  }
  return found;
}

function sign(body: string): string {
  const key = sha256(`${process.env.PGROUND_WEB_USERS ?? ""}\n${process.env.PGROUND_API_KEY ?? ""}`);
  return createHmac("sha256", key).update(body).digest("base64url");
}

export function checkPassword(name: string, password: string): boolean {
  const expected = accounts().get(name);
  // Compare even for an unknown name, so the response time doesn't reveal which names exist.
  return same(password, expected ?? "") && expected !== undefined;
}

export function createSession(name: string): string {
  const expires = Math.floor(Date.now() / 1000) + MAX_AGE_S;
  const body = `${Buffer.from(name).toString("base64url")}.${expires}`;
  return `${body}.${sign(body)}`;
}

/** The signed-in user's name, or null when the cookie is missing, forged or expired. */
export function readSession(token: string | undefined): string | null {
  if (!token || accounts().size === 0) return null;
  const [name, expires, signature, ...rest] = token.split(".");
  if (!name || !expires || !signature || rest.length) return null;
  if (!same(signature, sign(`${name}.${expires}`))) return null;
  if (Number(expires) * 1000 <= Date.now()) return null;
  const user = Buffer.from(name, "base64url").toString();
  return accounts().has(user) ? user : null;
}
