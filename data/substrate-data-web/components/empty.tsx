"use client";
import { Database } from "lucide-react";
export function Empty({ text }: { text: string }) {
  return (
    <div className="empty">
      <Database size={23} />
      <p>{text}</p>
    </div>
  );
}
