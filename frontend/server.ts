import 'dotenv/config';
import express from 'express';
import type {Request, Response} from 'express';
import path from 'path';
import {fileURLToPath} from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = 3000;
const FASTAPI_URL = process.env.VITE_BACKEND_URL || 'http://127.0.0.1:8001';

interface IncomingHistoryMessage {
  role: 'user' | 'model';
  text: string;
}

interface ChatRequestBody {
  message?: string;
  query?: string;
  history?: IncomingHistoryMessage[];
  responseStyle?: 'balanced' | 'concise' | 'detailed';
  response_style?: 'balanced' | 'concise' | 'detailed';
}

async function startServer() {
  const app = express();
  app.use(express.json({limit: '2mb'}));

  // Proxy direct RepoMind FastAPI endpoints
  app.all(
    [
      '/query',
      '/query/stream',
      '/health',
      '/repo/index',
      '/repo/active',
      '/api/query',
      '/api/query/stream',
      '/api/health',
      '/api/repo/index',
      '/api/repo/active',
    ],
    async (req: Request, res: Response) => {
      try {
        const targetUrl = `${FASTAPI_URL}${req.path}`;
        const response = await fetch(targetUrl, {
          method: req.method,
          headers: {
            'Content-Type': 'application/json',
            ...(req.headers.authorization ? {Authorization: req.headers.authorization} : {}),
          },
          body: ['POST', 'PUT', 'PATCH'].includes(req.method) ? JSON.stringify(req.body) : undefined,
        });

        const contentType = response.headers.get('content-type') || '';
        res.status(response.status);

        if (contentType.includes('text/event-stream')) {
          res.setHeader('Content-Type', 'text/event-stream');
          res.setHeader('Cache-Control', 'no-cache');
          res.setHeader('Connection', 'keep-alive');
          if (response.body) {
            const reader = response.body.getReader();
            while (true) {
              const {done, value} = await reader.read();
              if (done) break;
              res.write(value);
            }
          }
          res.end();
        } else {
          const text = await response.text();
          if (contentType) {
            res.setHeader('Content-Type', contentType);
          }
          res.send(text);
        }
      } catch (err: any) {
        res.status(502).json({
          error: `Failed to proxy to RepoMind backend at ${FASTAPI_URL}: ${err.message}`,
        });
      }
    }
  );

  // Backward compatibility bridge for /api/chat
  app.post('/api/chat', async (req: Request, res: Response) => {
    try {
      const body = (req.body ?? {}) as ChatRequestBody;
      const queryText = (body.query || body.message || '').trim();
      if (!queryText) {
        res.status(400).json({error: 'Query or message text is required.'});
        return;
      }

      const response = await fetch(`${FASTAPI_URL}/query/stream`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          query: queryText,
          repo_url: (body as any).repo_url,
          repo_revision: (body as any).repo_revision,
          history: body.history || [],
          response_style: body.response_style || body.responseStyle || 'balanced',
        }),
      });

      res.setHeader('Content-Type', 'text/event-stream');
      res.setHeader('Cache-Control', 'no-cache');
      res.setHeader('Connection', 'keep-alive');

      if (response.body) {
        const reader = response.body.getReader();
        while (true) {
          const {done, value} = await reader.read();
          if (done) break;
          res.write(value);
        }
        res.end();
      } else {
        res.end();
      }
    } catch (err: any) {
      res.status(502).json({error: `RepoMind backend unavailable: ${err.message}`});
    }
  });

  if (process.env.NODE_ENV !== 'production') {
    const {createServer: createViteServer} = await import('vite');
    const vite = await createViteServer({
      server: {middlewareMode: true},
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distDir = path.resolve(__dirname, 'dist');
    app.use(express.static(distDir));
    app.get('*', (_req: Request, res: Response) => {
      res.sendFile(path.join(distDir, 'index.html'));
    });
  }

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`Frontend server running on http://0.0.0.0:${PORT} (proxying to ${FASTAPI_URL})`);
  });
}

startServer().catch((err) => {
  console.error('Failed to start server:', err);
  process.exit(1);
});
