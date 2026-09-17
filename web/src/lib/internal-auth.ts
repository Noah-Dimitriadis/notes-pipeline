import { createHmac } from "node:crypto";

// TypeScript port of notes_pipeline/internal_auth.py — must stay byte-for-byte
// compatible, since the api service verifies these tokens with the Python
// implementation. See that file's module docstring for the trust model.
//
// Token shape: "<b64url(email)>.<b64url(issued_at)>.<b64url(hmac-sha256)>"
// The signed message is the literal string `${email}.${issuedAt}` (decoded
// values joined with a dot), not the encoded token parts.

function b64u(data: Buffer): string {
  return data.toString("base64url");
}

function sign(email: string, issuedAt: string, secret: string): Buffer {
  const message = `${email}.${issuedAt}`;
  return createHmac("sha256", secret).update(message, "utf-8").digest();
}

/**
 * Mint an internal_auth token asserting `email` is the caller.
 *
 * `issuedAt` is injectable only so the cross-language parity test can hold
 * it fixed against a Python-generated token; every real call site omits it
 * and gets the current time.
 */
export function signInternalToken(
  email: string,
  secret: string,
  issuedAt: string = String(Math.floor(Date.now() / 1000)),
): string {
  const signature = sign(email, issuedAt, secret);
  return [
    b64u(Buffer.from(email, "utf-8")),
    b64u(Buffer.from(issuedAt, "utf-8")),
    b64u(signature),
  ].join(".");
}
