import { Instrument_Serif, Mrs_Saint_Delafield } from "next/font/google";
import { TravelApp } from "@/travel/TravelApp";

const script = Mrs_Saint_Delafield({ weight: "400", subsets: ["latin"], display: "swap", variable: "--travel-script" });
const serif = Instrument_Serif({ weight: "400", subsets: ["latin"], display: "swap", variable: "--travel-serif" });

export default function Travel() {
  return (
    <div className={`${script.variable} ${serif.variable}`}>
      <TravelApp mode={process.env.CLIENTOPS_TRAVEL === "live" ? "live" : "playback"} />
    </div>
  );
}
