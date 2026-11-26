-- Run after 001_chat_history_auth.sql. All history writes use the user's JWT.
ALTER TABLE public.conversations ADD COLUMN IF NOT EXISTS repo_revision text;
ALTER TABLE public.messages ADD COLUMN IF NOT EXISTS repo_snapshot jsonb;

CREATE INDEX IF NOT EXISTS idx_conversation_owner_activity
    ON public.conversations(user_id, updated_at DESC);

-- A message must belong to both the current user and a conversation they own.
DROP POLICY IF EXISTS "Users can view their own messages" ON public.messages;
DROP POLICY IF EXISTS "Users can insert their own messages" ON public.messages;
DROP POLICY IF EXISTS "Users can delete their own messages" ON public.messages;

CREATE POLICY "Users can view their own messages" ON public.messages FOR SELECT TO authenticated
    USING (auth.uid() = user_id AND EXISTS (
        SELECT 1 FROM public.conversations AS c
        WHERE c.id = conversation_id AND c.user_id = auth.uid()
    ));
CREATE POLICY "Users can insert their own messages" ON public.messages FOR INSERT TO authenticated
    WITH CHECK (auth.uid() = user_id AND EXISTS (
        SELECT 1 FROM public.conversations AS c
        WHERE c.id = conversation_id AND c.user_id = auth.uid()
    ));
CREATE POLICY "Users can delete their own messages" ON public.messages FOR DELETE TO authenticated
    USING (auth.uid() = user_id AND EXISTS (
        SELECT 1 FROM public.conversations AS c
        WHERE c.id = conversation_id AND c.user_id = auth.uid()
    ));

REVOKE ALL ON public.conversations, public.messages FROM anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.conversations TO authenticated;
GRANT SELECT, INSERT, DELETE ON public.messages TO authenticated;

CREATE OR REPLACE FUNCTION public.touch_saved_conversation()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
BEGIN
    UPDATE public.conversations SET updated_at = now()
    WHERE id = NEW.conversation_id AND user_id = NEW.user_id;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS saved_message_touch_conversation ON public.messages;
CREATE TRIGGER saved_message_touch_conversation AFTER INSERT ON public.messages
FOR EACH ROW EXECUTE FUNCTION public.touch_saved_conversation();
