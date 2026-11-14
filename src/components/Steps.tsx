import { Step, Stepper } from "@astryxdesign/core/Stepper";

export type StepPhase = "done" | "now" | "next";

export type StepItem = { label: string; detail?: string; state: StepPhase };

export function Steps({ steps }: { steps: StepItem[] }) {
  const now = steps.findIndex((step) => step.state === "now");
  const activeStep = now === -1 ? steps.filter((step) => step.state === "done").length : now;
  return (
    <Stepper label="Steps" orientation="vertical" activeStep={activeStep} density="compact">
      {steps.map((step, index) => (
        <Step key={`${index}-${step.label}`} step={index} label={step.label} description={step.detail} />
      ))}
    </Stepper>
  );
}
