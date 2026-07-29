import Link from "next/link";

export default function NotFound() {
  return (
    <section className="empty-state ph-card">
      <p className="eyebrow">Not found</p>
      <h1>This workflow does not exist.</h1>
      <Link className="button" href="/">Return to runs</Link>
    </section>
  );
}
