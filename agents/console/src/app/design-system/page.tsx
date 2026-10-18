import { notFound } from "next/navigation";
import { DesignSystemGallery } from "./Lazy";

/** Every primitive in every variant, in the current theme. Development only: the visual reference and the manual regression check. */
export default function DesignSystemPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <DesignSystemGallery />;
}
