import { Buffer } from "node:buffer";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

const SESSION_COOKIE = "vento_nse_session";
const SESSION_TTL_SECONDS = 12 * 60 * 60;

type AuthConfig = {
  username: string;
  password: string;
  secret: string;
};

type SessionPayload = {
  username: string;
  expiresAt: number;
};

function getAuthConfig(): AuthConfig | null {
  const username = process.env.APP_USERNAME?.trim();
  const password = process.env.APP_PASSWORD;
  const secret = process.env.AUTH_SECRET;

  if (!username || !password || !secret || secret.length < 32) return null;
  return { username, password, secret };
}

function constantTimeEqual(left: string, right: string): boolean {
  const leftBytes = new TextEncoder().encode(left);
  const rightBytes = new TextEncoder().encode(right);
  const length = Math.max(leftBytes.length, rightBytes.length);
  let mismatch = leftBytes.length ^ rightBytes.length;

  for (let index = 0; index < length; index += 1) {
    mismatch |= (leftBytes[index] ?? 0) ^ (rightBytes[index] ?? 0);
  }

  return mismatch === 0;
}

async function sign(payload: string, secret: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(payload));
  return Buffer.from(signature).toString("base64url");
}

export function isAuthConfigured(): boolean {
  return getAuthConfig() !== null;
}

export async function authenticate(username: string, password: string): Promise<boolean> {
  const config = getAuthConfig();
  if (!config) return false;

  return (
    constantTimeEqual(username.trim(), config.username) &&
    constantTimeEqual(password, config.password)
  );
}

export async function createSessionToken(username: string): Promise<string> {
  const config = getAuthConfig();
  if (!config) throw new Error("Application authentication is not configured.");

  const payload: SessionPayload = {
    username,
    expiresAt: Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS,
  };
  const encodedPayload = Buffer.from(JSON.stringify(payload)).toString("base64url");
  const signature = await sign(encodedPayload, config.secret);
  return `${encodedPayload}.${signature}`;
}

export async function getSessionUser(): Promise<string | null> {
  const config = getAuthConfig();
  if (!config) return null;

  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;
  if (!token) return null;

  const [encodedPayload, suppliedSignature, extra] = token.split(".");
  if (!encodedPayload || !suppliedSignature || extra) return null;

  const expectedSignature = await sign(encodedPayload, config.secret);
  if (!constantTimeEqual(suppliedSignature, expectedSignature)) return null;

  try {
    const payload = JSON.parse(
      Buffer.from(encodedPayload, "base64url").toString("utf8"),
    ) as SessionPayload;

    if (
      payload.username !== config.username ||
      !Number.isFinite(payload.expiresAt) ||
      payload.expiresAt <= Math.floor(Date.now() / 1000)
    ) {
      return null;
    }

    return payload.username;
  } catch {
    return null;
  }
}

export async function requireSessionUser(): Promise<string> {
  const username = await getSessionUser();
  if (username) return username;
  redirect("/login");
}

export function sessionCookie(token: string, secure: boolean): string {
  return [
    `${SESSION_COOKIE}=${token}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    `Max-Age=${SESSION_TTL_SECONDS}`,
    secure ? "Secure" : "",
  ]
    .filter(Boolean)
    .join("; ");
}

export function expiredSessionCookie(secure: boolean): string {
  return [
    `${SESSION_COOKIE}=`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    "Max-Age=0",
    secure ? "Secure" : "",
  ]
    .filter(Boolean)
    .join("; ");
}

export function isSecureRequest(request: Request): boolean {
  return (
    request.headers.get("x-forwarded-proto") === "https" ||
    new URL(request.url).protocol === "https:"
  );
}
