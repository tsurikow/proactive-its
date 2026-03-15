export const tutorQueryKeys = {
  health: () => ["health"] as const,
  startMessage: (learnerId: string, version: number) => ["start-message", learnerId, version] as const,
  currentLesson: (learnerId: string, version: number) => ["lesson-current", learnerId, version] as const,
};
