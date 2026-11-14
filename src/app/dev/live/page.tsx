import type { Metadata } from "next";
import { ConsoleApp } from "@/console/ConsoleApp";

export const metadata: Metadata = { title: "Live jobs · ClientOps" };

export default function LiveJobsPage() {
  return <ConsoleApp jobsMode="live" />;
}
