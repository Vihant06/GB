import {useState, type FormEvent} from 'react';
import {FolderGit2, Loader2} from 'lucide-react';
import {ensureImmediateSignup, supabase} from '../lib/chatHistory';

export type AuthMode = 'sign-in' | 'sign-up';

interface AuthPageProps {
  mode: AuthMode;
}

export function AuthPage({mode}: AuthPageProps) {
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const isSignUp = mode === 'sign-up';
  const field = 'mt-1 block w-full rounded-lg border border-neutral-400 bg-transparent px-3 py-2 dark:border-neutral-500';

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!supabase || pending) return;
    setPending(true);
    setError('');
    try {
      if (isSignUp) {
        if (!/^[a-zA-Z0-9_.-]{2,40}$/.test(username.trim())) {
          setError('Use 2–40 letters, numbers, periods, underscores, or hyphens in your username.');
          return;
        }
        await ensureImmediateSignup();
        const {data, error: authError} = await supabase.auth.signUp({
          email: email.trim(),
          password,
          options: {
            data: {username: username.trim()},
          },
        });
        if (authError) {
          setError(authError.message);
        } else if (!data.session) {
          setError('The server did not open a session. Ask the project owner to check the Email settings.');
        }
      } else {
        const {error: authError} = await supabase.auth.signInWithPassword({email: email.trim(), password});
        if (authError) {
          setError(authError.code === 'email_not_confirmed'
            ? 'This account is still unconfirmed. Ask the project owner to activate it.'
            : 'Sign-in failed. Check your email and password, then try again.');
        }
      }
    } catch (err) {
      setError(err instanceof Error && isSignUp
        ? err.message : 'Could not reach the authentication service. Try again.');
    } finally {
      setPassword('');
      setPending(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#FAFAF5] px-5 py-6 text-[#111111] dark:bg-[#181815] dark:text-[#ECEBE4]">
      <div className="mx-auto grid min-h-[calc(100vh-3rem)] max-w-sm place-items-center">
        <section aria-labelledby="auth-title" className="w-full rounded-[24px] border-2 border-[#111111] bg-white p-7 shadow-[4px_4px_0_#111111] dark:border-[#42413A] dark:bg-[#20201C] dark:shadow-[4px_4px_0_#0E0E0C]">
          <div className="mb-5 inline-flex items-center gap-2 font-mono text-sm">
            <span className="grid size-9 place-items-center rounded-lg border border-[#111111] bg-[#9BD5F7] text-[#111111]">
              <FolderGit2 size={19} aria-hidden="true" />
            </span>
            RepoMind
          </div>
          <h1 id="auth-title" className="text-2xl font-semibold">{isSignUp ? 'Create your account' : 'Sign in to RepoMind'}</h1>
          <p className="mt-2 text-sm text-neutral-600 dark:text-neutral-300">
            {isSignUp ? 'Save repository chats and pick them up on another device.' : 'Open your saved repository chats.'}
          </p>
          <form onSubmit={submit} className="mt-6 space-y-3 text-sm">
            {isSignUp && (
              <label className="block">Username
                <input className={field} type="text" autoComplete="nickname" minLength={2} maxLength={40}
                  required value={username} onChange={event => setUsername(event.target.value)} />
              </label>
            )}
            <label className="block">Email
              <input className={field} type="email" autoComplete={isSignUp ? 'email' : 'username'}
                required value={email} onChange={event => setEmail(event.target.value)} />
            </label>
            <label className="block">Password
              <input className={field} type="password" autoComplete={isSignUp ? 'new-password' : 'current-password'}
                required minLength={isSignUp ? 8 : undefined} value={password}
                onChange={event => setPassword(event.target.value)} />
            </label>
            <button type="submit" disabled={!supabase || pending}
              className="flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border-2 border-[#111111] bg-[#9BD5F7] px-4 font-semibold text-[#111111] hover:bg-[#b9e1fb] focus-visible:outline-2 focus-visible:outline-offset-2 disabled:opacity-50">
              {pending && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
              {pending ? (isSignUp ? 'Creating account…' : 'Signing in…') : (isSignUp ? 'Sign up' : 'Sign in')}
            </button>
          </form>
          {(!supabase || error) && (
            <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-300">
              {!supabase ? 'Sign-in is unavailable until this deployment is connected to Supabase.'
                : error}
            </p>
          )}
          <p className="mt-5 text-sm">
            {isSignUp ? 'Already have an account?' : 'New to RepoMind?'}{' '}
            <a href={isSignUp ? '#sign-in' : '#sign-up'} className="font-semibold underline underline-offset-4">
              {isSignUp ? 'Sign in' : 'Sign up'}
            </a>
          </p>
        </section>
      </div>
    </main>
  );
}
