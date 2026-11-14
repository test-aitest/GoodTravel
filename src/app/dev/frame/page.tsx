import type { Metadata } from "next";
import { FrameDemo } from "./FrameDemo";

export const metadata: Metadata = { title: "Frame · ClientOps" };

export default function FramePage() {
  return <FrameDemo />;
}
