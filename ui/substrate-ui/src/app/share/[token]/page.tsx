import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { SharedConversation, type SharedConversationData } from "@/components/SharedConversation";

// A shared link is private by obscurity of its token: keep it out of search engines and out of referrers.
export const metadata: Metadata = {
  title: "Shared conversation",
  robots: { index: false, follow: false },
  referrer: "no-referrer",
};

const BACKEND_URL = process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default async function SharedPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  let res: Response;
  try {
    res = await fetch(`${BACKEND_URL}/shared/${encodeURIComponent(token)}`, { cache: "no-store" });
  } catch {
    return (
      <main className="mx-auto max-w-md px-6 py-24 text-center">
        <h1 className="text-lg font-semibold">This page could not be loaded</h1>
        <p className="mt-2 text-sm text-muted">Please try again in a moment.</p>
      </main>
    );
  }
  if (res.status === 404) notFound();
  if (!res.ok) {
    return (
      <main className="mx-auto max-w-md px-6 py-24 text-center">
        <h1 className="text-lg font-semibold">This page could not be loaded</h1>
        <p className="mt-2 text-sm text-muted">Please try again in a moment.</p>
      </main>
    );
  }
  return <SharedConversation data={(await res.json()) as SharedConversationData} />;
}
