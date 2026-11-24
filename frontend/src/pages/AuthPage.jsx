import { useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { ThemeToggle } from "../components/ui";

const PASSWORD_CHECKS = [
  ["length", (minimumLength) => `At least ${minimumLength} characters`],
  ["uppercase", () => "An uppercase letter"],
  ["lowercase", () => "A lowercase letter"],
  ["number", () => "A number"],
  ["special", () => "A special character"],
];

function passwordChecks(value, minimumLength) {
  return {
    length: [...value].length >= minimumLength,
    uppercase: /\p{Lu}/u.test(value),
    lowercase: /\p{Ll}/u.test(value),
    number: /\p{N}/u.test(value),
    special: /[^\p{L}\p{N}]/u.test(value),
  };
}

function PasswordField({
  name,
  label,
  autoComplete,
  minimumLength,
  value,
  onChange,
  showStrength = false,
}) {
  const [visible, setVisible] = useState(false);
  const checks = passwordChecks(value || "", minimumLength);
  const score = Object.values(checks).filter(Boolean).length;
  const strength = score <= 2 ? "weak" : score <= 4 ? "moderate" : "strong";
  const inputId = `auth-${name}`;
  return (
    <div className="auth-password-field">
      <label htmlFor={inputId}>{label}</label>
      <div className="auth-password-input">
        <input
          id={inputId}
          name={name}
          type={visible ? "text" : "password"}
          required
          minLength={minimumLength}
          maxLength={128}
          autoComplete={autoComplete}
          value={value}
          onChange={onChange}
        />
        <button
          type="button"
          className="auth-password-toggle"
          aria-label={visible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
          aria-pressed={visible}
          onClick={() => setVisible((current) => !current)}
        >
          {visible ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </div>
      {showStrength && (
        <div className="password-strength" aria-live="polite">
          <div className={`password-strength-bar ${strength}`}><span style={{ width: `${score * 20}%` }} /></div>
          <strong className={`password-strength-label ${strength}`}>Password strength: {strength}</strong>
          <ul>
            {PASSWORD_CHECKS.map(([key, describe]) => (
              <li key={key} className={checks[key] ? "met" : ""}>{describe(minimumLength)}</li>
            ))}
          </ul>
          <small className="auth-field-help">Passwords must not exceed 72 UTF-8 bytes.</small>
        </div>
      )}
    </div>
  );
}

export default function AuthPage({
  pathname,
  error,
  successMessage,
  adminSetupAvailable,
  adminSetupError,
  authToken = "",
  authProcessing = false,
  navigate,
  onSubmit,
  onClearError,
  theme,
  onToggleTheme,
}) {
  const registering = pathname === "/register";
  const settingUpAdmin = pathname === "/admin-setup";
  const requestingReset = pathname === "/forgot-password";
  const resendingVerification = pathname === "/resend-verification";
  const resettingPassword = pathname === "/reset-password";
  const verifyingEmail = pathname === "/verify-email";
  const showSetupForm = settingUpAdmin && adminSetupAvailable === true;
  const setupUnavailable = settingUpAdmin && adminSetupAvailable === false;
  const needsPassword = pathname === "/login" || registering || showSetupForm || resettingPassword;
  const minimumPasswordLength = settingUpAdmin || resettingPassword ? 12 : registering ? 8 : 0;
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const pageTitle = registering
    ? "Create your surveyor account"
    : settingUpAdmin
      ? "Set up the Administrator account"
      : requestingReset
        ? "Reset your password"
        : resendingVerification
          ? "Resend verification email"
          : resettingPassword
            ? "Choose a new password"
            : verifyingEmail
              ? "Verify your email address"
              : "Welcome back";
  const pageDescription = registering
    ? "Register to report and track road conditions."
    : settingUpAdmin
      ? "Create the first administrator for this RoadWatch workspace."
      : requestingReset
        ? "Enter your registered email. We will show the same confirmation whether or not an account is eligible."
        : resendingVerification
          ? "Enter your address to request another verification link."
          : resettingPassword
            ? "Choose a strong new password for your account."
            : verifyingEmail
              ? "We are securely confirming the single-use email verification link."
              : "Sign in to your RoadWatch workspace.";
  const canShowEmail = !setupUnavailable && !verifyingEmail && (!settingUpAdmin || showSetupForm);
  const canSubmit = !verifyingEmail && !setupUnavailable && !(resettingPassword && !authToken);

  function clearAndNavigate(path) {
    onClearError?.();
    setPassword("");
    setConfirmPassword("");
    navigate(path);
  }

  return (
    <main className="auth-page">
      <ThemeToggle theme={theme} onToggle={onToggleTheme} className="auth-theme-toggle" />
      <form className="auth-card" onSubmit={onSubmit}>
        <div className="auth-brand"><div className="auth-mark">RW</div><span>ROADWATCH <strong>AI</strong></span></div>
        <div className="auth-heading">
          <p className="page-eyebrow">ROAD INFRASTRUCTURE INTELLIGENCE</p>
          <h1>{pageTitle}</h1>
          <p>{pageDescription}</p>
        </div>
        {registering && <label>Full name<input name="full_name" required maxLength="120" autoComplete="name" /></label>}
        {registering && <label>Access request<select name="requested_role" defaultValue="user"><option value="user">User access</option><option value="admin">Request Admin access</option></select><small className="auth-field-help">User access is immediate after email verification. Admin requests stay pending until an Administrator approves them. Public signup never grants elevated privileges.</small></label>}
        {showSetupForm && <label>Full name<input name="full_name" required maxLength="120" autoComplete="name" /></label>}
        {canShowEmail && <label htmlFor="auth-email">Email address<input id="auth-email" type="email" name="email" required maxLength="254" autoComplete="email" /></label>}
        {needsPassword && (
          <PasswordField
            name="password"
            label={resettingPassword ? "New password" : "Password"}
            autoComplete={resettingPassword ? "new-password" : registering || settingUpAdmin ? "new-password" : "current-password"}
            minimumLength={minimumPasswordLength}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            showStrength={registering || showSetupForm || resettingPassword}
          />
        )}
        {(registering || showSetupForm || resettingPassword) && (
          <PasswordField
            name="confirm_password"
            label="Confirm password"
            autoComplete="new-password"
            minimumLength={minimumPasswordLength}
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
          />
        )}
        {showSetupForm && <div className="auth-password-field"><label htmlFor="auth-setup-secret">One-time setup secret</label><input id="auth-setup-secret" type="password" name="setup_secret" required autoComplete="off" /><small className="auth-field-help">Enter the secret configured privately by your server operator. It is never stored in the website.</small></div>}
        {settingUpAdmin && adminSetupAvailable === null && <div className="auth-field-help" role="status">Checking whether initial Admin setup is available…</div>}
        {setupUnavailable && <div className="auth-message" role="status">Initial Admin setup is unavailable. It may already have been completed, or the backend has not configured setup.</div>}
        {resettingPassword && !authToken && <div className="auth-message" role="alert">This reset link is missing or invalid. Request a new link from the login page.</div>}
        {verifyingEmail && !authToken && <div className="auth-message" role="alert">This verification link is missing or invalid.</div>}
        {verifyingEmail && authProcessing && <div className="auth-field-help" role="status">Verifying the link…</div>}
        {settingUpAdmin && adminSetupError && <div className="auth-message" role="alert">{adminSetupError}</div>}
        {error && <div className="auth-message" role="alert">{error}</div>}
        {successMessage && <div className="auth-message success" role="status">{successMessage}</div>}
        {canSubmit && <button className="button button-primary auth-submit" type="submit" disabled={authProcessing}>{registering ? "Create account" : settingUpAdmin ? "Create first Administrator" : requestingReset ? "Request reset link" : resendingVerification ? "Send verification link" : resettingPassword ? "Update password" : "Sign in"}</button>}
        {pathname === "/login" && <>
          <p className="auth-switch-copy"><button type="button" className="auth-switch" onClick={() => clearAndNavigate("/forgot-password")}>Forgot password?</button></p>
          <p className="auth-switch-copy">Email not verified?<button type="button" className="auth-switch" onClick={() => clearAndNavigate("/resend-verification")}>Resend verification</button></p>
        </>}
        {verifyingEmail && <p className="auth-switch-copy">You can continue to sign in after this link is verified.<button type="button" className="auth-switch" onClick={() => clearAndNavigate("/login")}>Sign in</button></p>}
        {settingUpAdmin ? (
          <p className="auth-switch-copy">Already have an account?<button type="button" className="auth-switch" onClick={() => clearAndNavigate("/login")}>Sign in</button></p>
        ) : pathname !== "/login" && pathname !== "/verify-email" ? (
          <p className="auth-switch-copy"><button type="button" className="auth-switch" onClick={() => clearAndNavigate("/login")}>Back to sign in</button></p>
        ) : null}
        {pathname === "/login" && <p className="auth-switch-copy">New to RoadWatch?<button type="button" className="auth-switch" onClick={() => clearAndNavigate("/register")}>Create an account</button></p>}
        {!registering && !settingUpAdmin && pathname === "/login" && adminSetupAvailable && <p className="auth-switch-copy">No Administrator account yet?<button type="button" className="auth-switch" onClick={() => clearAndNavigate("/admin-setup")}>Set up the first Administrator</button></p>}
        {pathname === "/register" && <p className="auth-switch-copy">Already have an account?<button type="button" className="auth-switch" onClick={() => clearAndNavigate("/login")}>Sign in</button></p>}
        {!registering && pathname === "/login" && adminSetupError && <div className="auth-message" role="alert">{adminSetupError}</div>}
        <p className="auth-footnote">Protected road monitoring workspace</p>
      </form>
    </main>
  );
}
