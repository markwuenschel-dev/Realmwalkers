export default function History() {
  return (
    <div className="history">
      <h2>History</h2>
      <p className="muted">
        Version browsing lands in Phase 2. Every revision is already a new row that supersedes its
        parent, so the full lineage of each scene is captured in Postgres from day one.
      </p>
    </div>
  );
}
