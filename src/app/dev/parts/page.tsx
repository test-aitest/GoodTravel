import type { Metadata } from "next";
import { PartsGallery } from "./PartsGallery";

export const metadata: Metadata = { title: "Parts · ClientOps" };

export default function PartsPage() {
  return <PartsGallery />;
}
