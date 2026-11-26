import {createClient} from '@supabase/supabase-js';
import type {ChatMessage, RepoTarget} from '../types/chat';

const url = import.meta.env.VITE_SUPABASE_URL;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;
export const supabase = url && key ? createClient(url, key, {
  auth: {detectSessionInUrl: false},
}) : null;

export async function ensureImmediateSignup(): Promise<void> {
  const response = await fetch(`${url.replace(/\/+$/, '')}/auth/v1/settings`, {
    headers: {apikey: key},
    cache: 'no-store',
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error('Could not check sign-up settings. Try again.');
  const settings: {mailer_autoconfirm?: boolean} = await response.json();
  if (settings.mailer_autoconfirm !== true) {
    throw new Error('Sign-up is unavailable until the project owner turns off Confirm email in Supabase.');
  }
}

export async function workspaceAuthHeaders(): Promise<{Authorization: string}> {
  const {data, error} = await supabase?.auth.getSession() ?? {data: null, error: null};
  if (error || !data?.session || data.session.user.is_anonymous) {
    throw new Error('Sign in to use RepoMind.');
  }
  return {Authorization: `Bearer ${data.session.access_token}`};
}

export interface SavedChat {
  id: string;
  title: string;
  repo_name: string;
  repo_url: string;
  repo_revision: string | null;
  updated_at: string;
}

export interface HistoryTarget {
  id: string;
  userId: string;
  title: string;
  repo?: RepoTarget;
  persisted?: boolean;
}

export function describeHistoryError(error: unknown, fallback: string): string {
  const code = error && typeof error === 'object' && 'code' in error ? error.code : undefined;
  if (code === 'PGRST205' || code === '42P01') {
    return 'Supabase can’t find the saved-chat tables. Run migrations 001 and 002 in the connected project’s SQL Editor.';
  }
  if (code === '42703' || code === 'PGRST204') {
    return 'The saved-chat schema is out of date. Run migration 002 in the connected Supabase project.';
  }
  if (code === '42501') {
    return 'Supabase denied access to saved chats. Check the authenticated permissions in migration 002.';
  }
  if (code === 'PGRST301' || code === 'PGRST303') {
    return 'Your session could not be verified. Sign out and sign in again.';
  }
  return fallback;
}

function restoreRepo(value: unknown): RepoTarget | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const {name, url, revision} = value as Partial<RepoTarget>;
  if (!name || !url || !/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+$/i.test(url)) return undefined;
  return {
    name, url,
    revision: typeof revision === 'string' && /^[a-f0-9]{40}$/.test(revision) ? revision : undefined,
    indexed: true,
  };
}

export function savedChatRepo(chat: SavedChat): RepoTarget | undefined {
  return restoreRepo({name: chat.repo_name, url: chat.repo_url, revision: chat.repo_revision});
}

export async function listSavedChats(userId: string): Promise<SavedChat[]> {
  if (!supabase) return [];
  const {data, error} = await supabase.from('conversations')
    .select('id,title,repo_name,repo_url,repo_revision,updated_at')
    .eq('user_id', userId).order('updated_at', {ascending: false}).limit(50);
  if (error) throw error;
  return data ?? [];
}

export async function loadSavedMessages(chatId: string, userId: string): Promise<ChatMessage[]> {
  if (!supabase) return [];
  const {data, error} = await supabase.from('messages')
    .select('id,sender,text,citations,repo_snapshot,created_at')
    .eq('user_id', userId).eq('conversation_id', chatId)
    .order('created_at', {ascending: false}).order('id', {ascending: false}).limit(200);
  if (error) throw error;
  return (data ?? []).reverse().map(row => ({
    id: row.id,
    sender: row.sender === 'user' ? 'user' : 'contact',
    senderLabel: row.sender === 'user' ? 'You' : 'RepoMind',
    timestamp: new Date(row.created_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}),
    text: row.text,
    repoTarget: restoreRepo(row.repo_snapshot),
    verifiedCitations: Array.isArray(row.citations)
      ? row.citations.filter((citation: unknown): citation is string => typeof citation === 'string') : [],
  }));
}

export async function saveChatMessage(target: HistoryTarget, message: ChatMessage): Promise<void> {
  if (!supabase || !message.text) return;
  if (!target.persisted) {
    const {error} = await supabase.from('conversations').upsert({
      id: target.id,
      user_id: target.userId,
      title: target.title,
      repo_name: target.repo?.name ?? '',
      repo_url: target.repo?.url ?? '',
      repo_revision: target.repo?.revision ?? null,
    }, {onConflict: 'id', ignoreDuplicates: true});
    if (error) throw error;
    target.persisted = true;
  }
  const {error} = await supabase.from('messages').upsert({
    id: message.id,
    conversation_id: target.id,
    user_id: target.userId,
    sender: message.sender === 'user' ? 'user' : 'assistant',
    text: message.text,
    citations: message.verifiedCitations ?? [],
    repo_snapshot: restoreRepo(message.repoTarget) ?? null,
  }, {onConflict: 'id', ignoreDuplicates: true});
  if (error) throw error;
}

export async function renameSavedChat(chatId: string, userId: string, title: string): Promise<void> {
  if (!supabase) throw new Error('Saved chats are unavailable.');
  const name = title.trim();
  if (!name || name.length > 90) throw new Error('Enter a chat name between 1 and 90 characters.');
  const {data, error} = await supabase.from('conversations')
    .update({title: name}).eq('id', chatId).eq('user_id', userId).select('id').maybeSingle();
  if (error) throw error;
  if (!data) throw new Error('Chat is no longer available.');
}

export async function deleteSavedChat(chatId: string, userId: string): Promise<void> {
  if (!supabase) throw new Error('Saved chats are unavailable.');
  const {error} = await supabase.from('conversations')
    .delete().eq('id', chatId).eq('user_id', userId);
  if (error) throw error;
}
