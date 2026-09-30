"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Client redirect — required for Next static export (no server redirect). */
export default function Home() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/desk");
  }, [router]);
  return (
    <main className="min-h-screen flex items-center justify-center text-[var(--muted)] text-sm">
      Opening Desk…
    </main>
  );
}
