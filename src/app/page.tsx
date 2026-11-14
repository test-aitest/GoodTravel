import { ConsoleApp } from "@/console/ConsoleApp";

export default function Home() {
  return <ConsoleApp jobsMode={process.env.CLIENTOPS_JOBS === "playback" ? "playback" : "live"} />;
}
