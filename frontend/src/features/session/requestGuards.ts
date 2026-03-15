import { useEffect, useRef } from "react";

export function useRequestGuards(learnerId: string | null) {
  const learnerIdRef = useRef<string | null>(learnerId);
  const requestVersionRef = useRef(0);

  useEffect(() => {
    learnerIdRef.current = learnerId;
  }, [learnerId]);

  const beginRequestVersion = () => {
    requestVersionRef.current += 1;
    return requestVersionRef.current;
  };

  const invalidateRequests = () => {
    requestVersionRef.current += 1;
  };

  const isActiveRequest = (activeLearner: string, version: number) =>
    learnerIdRef.current === activeLearner && requestVersionRef.current === version;

  const currentRequestVersion = () => requestVersionRef.current;

  return {
    beginRequestVersion,
    invalidateRequests,
    isActiveRequest,
    currentRequestVersion,
  };
}
