/**
 * Estado del wizard de importación: paso activo + flags de fuentes cargadas +
 * progreso simulado del paso "run".
 *
 * Es UI-state local de la feature (no entra a TanStack Query — no es server
 * data). El progreso se anima vía `setInterval` cuando se entra al step `run`.
 */

import { useEffect, useState } from "react";
import { WIZARD_STEPS, type WizardStep } from "@/entities/import-job";

export type StepKey = WizardStep["key"];

export function useUploadWizard() {
  const [step, setStep] = useState<StepKey>("src");
  const [excelLoaded, setExcelLoaded] = useState(true);
  const [folderLoaded, setFolderLoaded] = useState(true);
  const [progress, setProgress] = useState(0);
  const [running, setRunning] = useState(false);

  // Auto-start on entering `run`
  useEffect(() => {
    if (step === "run" && progress === 0 && !running) {
      const timeout = setTimeout(() => setRunning(true), 400);
      return () => clearTimeout(timeout);
    }
  }, [step, progress, running]);

  // Progress sim
  useEffect(() => {
    if (step !== "run" || !running) return;
    const id = setInterval(() => {
      setProgress((p) => {
        if (p >= 100) {
          clearInterval(id);
          return 100;
        }
        return p + 1.2;
      });
    }, 80);
    return () => clearInterval(id);
  }, [step, running]);

  const idx = WIZARD_STEPS.findIndex((s) => s.key === step);
  const next = () => setStep(WIZARD_STEPS[Math.min(idx + 1, WIZARD_STEPS.length - 1)].key);
  const prev = () => setStep(WIZARD_STEPS[Math.max(idx - 1, 0)].key);

  return {
    step,
    setStep,
    idx,
    next,
    prev,
    excelLoaded,
    setExcelLoaded,
    folderLoaded,
    setFolderLoaded,
    progress,
    running,
    setRunning,
  };
}
