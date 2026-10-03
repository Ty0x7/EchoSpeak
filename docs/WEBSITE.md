# The EchoSpeak website

The public site (front page + docs) lives in `apps/web/src/site/` and builds separately from the app:

```
cd apps/web
npm run build:site     # -> apps/web/dist-site
npm run preview:site   # http://localhost:5175/EchoSpeak/
```

## Where it's hosted

**GitHub Pages:** <https://ty0x7.github.io/EchoSpeak/>. `.github/workflows/website.yml` rebuilds and publishes
it on every merge to `main` that touches `apps/web`. The download button asks GitHub for the newest release, so it
always offers the latest installer. Nothing needs updating per release.

Pages use hash links (`#/docs`), so GitHub Pages never returns 404 on a deep link.

## Moving to a real domain later

- **GitHub Pages + your domain:** in the repo, open Settings › Pages › Custom domain, then build with
  `SITE_BASE=/` (change it in `website.yml`).
- **Vercel:** import the repo with root directory `apps/web`, build command `npm run build:site`, output directory
  `dist-site`, and environment variable `SITE_BASE=/`. You get `echospeak.vercel.app` right away, and you can add
  your domain later.
