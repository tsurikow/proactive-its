import { FormEvent, useState } from "react";

interface LearnerGateProps {
  onSubmit: (learnerId: string) => void;
}

export function LearnerGate({ onSubmit }: LearnerGateProps) {
  const [value, setValue] = useState("");

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const learnerId = value.trim();
    if (!learnerId) {
      return;
    }
    onSubmit(learnerId);
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/35 px-5 backdrop-blur-sm">
      <form className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-xl" onSubmit={handleSubmit}>
        <h2 className="text-3xl font-bold leading-tight text-slate-900">Welcome to Calculus Tutor</h2>
        <p className="mt-2 text-sm text-slate-600">
          Enter your learner ID to start and keep your progress in this browser.
        </p>
        <input
          data-testid="learner-id-input"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="e.g. student-001"
          autoFocus
          className="mt-4 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-[15px] outline-none transition focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20"
        />
        <button
          data-testid="learner-submit"
          type="submit"
          className="mt-3 w-full rounded-xl bg-teal-600 px-4 py-2.5 font-semibold text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Continue
        </button>
      </form>
    </div>
  );
}
