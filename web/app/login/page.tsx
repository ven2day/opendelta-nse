import { LockKeyhole, ShieldCheck } from "lucide-react";
import { redirect } from "next/navigation";
import { getSessionUser, isAuthConfigured } from "../server-auth";

export const dynamic = "force-dynamic";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  if (await getSessionUser()) redirect("/");

  const { error } = await searchParams;
  const configurationMissing = !isAuthConfigured();

  return (
    <main className="login-shell">
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-brand">
          <div className="brand-mark" aria-hidden="true">₹</div>
          <div>
            <strong>OpenDelta</strong>
            <span>Market intelligence</span>
          </div>
        </div>

        <div className="login-heading">
          <span className="login-icon"><LockKeyhole size={20} /></span>
          <div>
            <h1 id="login-title">Sign in</h1>
            <p>Enter your dashboard credentials to continue.</p>
          </div>
        </div>

        {(error === "invalid" || configurationMissing) && (
          <div className="login-error" role="alert">
            {configurationMissing
              ? "Login is not configured on this server."
              : "The username or password is incorrect."}
          </div>
        )}

        <form className="login-form" action="/api/login" method="post">
          <label>
            <span>Username</span>
            <input name="username" autoComplete="username" required />
          </label>
          <label>
            <span>Password</span>
            <input name="password" type="password" autoComplete="current-password" required />
          </label>
          <button type="submit" disabled={configurationMissing}>Sign in</button>
        </form>

        <div className="login-security">
          <ShieldCheck size={15} />
          Your session is protected with a secure, HTTP-only cookie.
        </div>
      </section>
    </main>
  );
}
