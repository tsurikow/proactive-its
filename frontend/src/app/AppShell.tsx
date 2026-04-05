import { AuthScreen } from "../features/auth/AuthScreen";
import { useAuthSession } from "../features/auth/useAuthSession";
import { useTutorSession } from "../features/session/useTutorSession";
import { TeacherWorkspace } from "../features/teacher-workspace/TeacherWorkspace";

export function AppShell() {
  const auth = useAuthSession();
  const session = useTutorSession(auth.learner);

  if (!auth.learner) {
    return <AuthScreen auth={auth} />;
  }

  return <TeacherWorkspace session={session} auth={auth} />;
}
