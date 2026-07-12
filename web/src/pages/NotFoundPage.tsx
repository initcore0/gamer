import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="state">
      <h1>Page not found</h1>
      <p className="muted">That route doesn’t exist.</p>
      <Link to="/" className="btn">
        Back to dashboard
      </Link>
    </div>
  );
}
