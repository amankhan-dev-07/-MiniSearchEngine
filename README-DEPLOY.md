# AMEJA v96 deployment-ready scaffold

This scaffold contains:
- `app/main.py` based on the tested v95 backend, with same-origin frontend
  serving and production URL rewriting.
- `frontend/index.html` from the existing AMEJA UI integration.
- `requirements.txt`
- `.python-version`
- `render.yaml`
- `.gitignore`

IMPORTANT:
Your actual search/index/data files from `D:\MiniSearchEngine` must remain in
the GitHub repository. This scaffold does not fabricate missing project data.

Before deployment:
1. Copy/merge these files into your real project root.
2. Ensure the repository contains the existing `app/search`, `app/wikipedia.py`,
   and any local index/data files required by the working project.
3. Ensure your local `test_site/pages` directory is committed if you want the
   local source pages to remain clickable after deployment.
4. On Render, set `AMEJA_PUBLIC_BASE_URL` to the final `https://...onrender.com`
   service URL after creation, then redeploy.

Render build:
`pip install -r requirements.txt`

Render start:
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`
