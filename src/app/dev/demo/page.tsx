import type { Metadata } from "next";
import { DemoReset } from "@/console/DemoReset";

export const metadata: Metadata = { title: "Demo · ClientOps" };

export default function DemoPage() {
  return <DemoReset />;
}
