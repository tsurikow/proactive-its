import { FormEvent, useState } from "react";

import type { AuthSessionState } from "./useAuthSession";

export function AuthScreen({ auth }: { auth: AuthSessionState }) {
  const [resetToken] = useState<string | null>(() => auth.consumeResetToken());
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [nextPassword, setNextPassword] = useState("");

  const confirmPasswordError =
    auth.mode === "signup" && confirmPassword && confirmPassword !== password
      ? "Passwords must match."
      : null;
  const signupDisabled =
    auth.loading ||
    !firstName.trim() ||
    !lastName.trim() ||
    !email.trim() ||
    !password.trim() ||
    !confirmPassword.trim() ||
    Boolean(confirmPasswordError) ||
    Boolean(auth.authUnavailableReason);

  const title =
    auth.mode === "signup"
      ? "Create learner account"
      : auth.mode === "forgot"
        ? "Request password reset"
        : auth.mode === "reset"
          ? "Choose a new password"
          : "Sign in";

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (auth.mode === "signup") {
      if (confirmPasswordError) {
        return;
      }
      await auth.signup(firstName, lastName, email, password);
      return;
    }
    if (auth.mode === "forgot") {
      await auth.requestReset(email);
      return;
    }
    if (auth.mode === "reset") {
      if (!resetToken) {
        return;
      }
      await auth.confirmReset(resetToken, nextPassword);
      return;
    }
    await auth.login(email, password);
  };

  const inputClass =
    "w-full rounded-[var(--radius-md)] border border-[color:var(--line-soft)] bg-[color:var(--surface)] px-3.5 py-2.5 text-[15px] text-[color:var(--ink-strong)] outline-none transition placeholder:text-[color:var(--ink-soft)] focus:border-[color:var(--accent-strong)] focus:ring-1 focus:ring-[color:var(--accent-strong)]";

  return (
    <div className="flex min-h-dvh items-center justify-center bg-[color:var(--bg-app)] px-4 py-8">
      <form
        className="w-full max-w-md rounded-[var(--radius-lg)] border border-[color:var(--line-soft)] bg-[color:var(--surface)] p-6 shadow-[var(--shadow-large)] sm:p-8"
        onSubmit={(event) => void handleSubmit(event)}
      >
        <div className="text-xs font-semibold uppercase tracking-widest text-[color:var(--ink-soft)]">
          Learner access
        </div>
        <h2 className="mt-2 text-2xl font-semibold text-[color:var(--ink-strong)]">{title}</h2>
        <p className="mt-2 text-sm leading-relaxed text-[color:var(--ink-muted)]">
          The teacher workspace is tied to one authenticated learner account and a durable study path.
        </p>

        {auth.authUnavailableReason ? (
          <div className="mt-4 rounded-[var(--radius-sm)] border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-sm text-amber-800">
            {auth.authUnavailableReason}
          </div>
        ) : null}
        {auth.resetNotice ? (
          <div className="mt-4 rounded-[var(--radius-sm)] border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-sm text-emerald-800">
            {auth.resetNotice}
          </div>
        ) : null}
        {auth.error ? (
          <div className="mt-4 rounded-[var(--radius-sm)] border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-sm text-rose-700">
            {auth.error}
          </div>
        ) : null}

        {auth.mode === "signup" ? (
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            <div>
              <input
                value={firstName}
                onChange={(event) => setFirstName(event.target.value)}
                placeholder="First name"
                autoFocus
                className={inputClass}
              />
              {auth.fieldErrors.first_name ? (
                <p className="mt-1.5 text-xs text-rose-600">{auth.fieldErrors.first_name}</p>
              ) : null}
            </div>
            <div>
              <input
                value={lastName}
                onChange={(event) => setLastName(event.target.value)}
                placeholder="Last name"
                className={inputClass}
              />
              {auth.fieldErrors.last_name ? (
                <p className="mt-1.5 text-xs text-rose-600">{auth.fieldErrors.last_name}</p>
              ) : null}
            </div>
          </div>
        ) : null}

        {auth.mode !== "reset" ? (
          <div>
            <input
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="Email"
              autoFocus={auth.mode !== "signup"}
              type="email"
              className={`mt-4 ${inputClass}`}
            />
            {auth.fieldErrors.email ? <p className="mt-1.5 text-xs text-rose-600">{auth.fieldErrors.email}</p> : null}
          </div>
        ) : null}

        {auth.mode === "signup" || auth.mode === "login" ? (
          <div>
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Password"
              type="password"
              className={`mt-3 ${inputClass}`}
            />
            {auth.fieldErrors.password ? (
              <p className="mt-1.5 text-xs text-rose-600">{auth.fieldErrors.password}</p>
            ) : null}
          </div>
        ) : null}

        {auth.mode === "signup" ? (
          <div>
            <input
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              placeholder="Confirm password"
              type="password"
              className={`mt-3 ${inputClass}`}
            />
            {confirmPasswordError ? <p className="mt-1.5 text-xs text-rose-600">{confirmPasswordError}</p> : null}
          </div>
        ) : null}

        {auth.mode === "reset" ? (
          <input
            value={nextPassword}
            onChange={(event) => setNextPassword(event.target.value)}
            placeholder="New password"
            type="password"
            autoFocus
            className={`mt-5 ${inputClass}`}
          />
        ) : null}

        <button
          type="submit"
          disabled={auth.mode === "signup" ? signupDisabled : auth.loading || Boolean(auth.authUnavailableReason)}
          className="mt-5 w-full rounded-[var(--radius-md)] bg-[color:var(--accent-strong)] px-4 py-2.5 font-semibold text-white transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {auth.mode === "signup"
            ? "Create account"
            : auth.mode === "forgot"
              ? "Send reset link"
              : auth.mode === "reset"
                ? "Update password"
                : "Sign in"}
        </button>

        <div className="mt-4 flex flex-wrap gap-3 text-sm">
          {auth.mode !== "login" ? (
            <button type="button" onClick={() => auth.setMode("login")} className="font-medium text-[color:var(--accent-strong)] hover:underline">
              Sign in
            </button>
          ) : null}
          {auth.mode !== "signup" ? (
            <button type="button" onClick={() => auth.setMode("signup")} className="font-medium text-[color:var(--accent-strong)] hover:underline">
              Create account
            </button>
          ) : null}
          {auth.mode !== "forgot" && auth.passwordResetEnabled ? (
            <button type="button" onClick={() => auth.setMode("forgot")} className="font-medium text-[color:var(--accent-strong)] hover:underline">
              Forgot password
            </button>
          ) : null}
        </div>
      </form>
    </div>
  );
}
