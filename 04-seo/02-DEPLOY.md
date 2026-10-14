# Deploy the site (20 minutes, $0, copy-paste)

The deployable artefact is **`docs/`**, and it is generated. Nothing in it is meant to be edited by
hand: `./wf site` writes it from `04-seo/articles/`, `04-seo/pages/` and `03-tool/index.html`, and
CI fails the build if the committed folder doesn't match a fresh rebuild. That rule is the whole
reason this folder can be trusted six months from now.

```bash
./wf site              # write docs/
./wf site --check      # verify without writing: links, placeholders, disclosures, gate findings
```

## 0. One decision first: where it lives

| Option | Cost | Time | Notes |
|---|---|---|---|
| GitHub Pages from the `docs/` folder | $0 | ~8 min | Recommended. No account to create if the repo exists. |
| Netlify (free tier) | $0 | ~4 min | Fastest, friendlier preview URLs, and `docs/netlify.toml` already sets the headers |
| Cloudflare Pages / Vercel | $0 | ~10 min | Fine too; same folder, no build step |
| A custom domain | **not free** | — | Out of scope for a $0 plan. The free subdomain ranks perfectly well at this size |

Then set the address in **one** place:

```jsonc
// config/site.json
{ "base_url": "https://<user>.github.io/<repo>" }
```

and re-run `./wf site`. That single value turns on the canonicals, `sitemap.xml` and the `Sitemap:`
line in `robots.txt`. With it empty the build ships **no** canonical on purpose — a made-up domain in
a `<link rel="canonical">` is worse than none, because it tells Google to index a place that does not
exist. `03-tool/index.html` used to carry one; that is why the tool's own canonical now comes from here.

## A. GitHub Pages

1. Repo → **Settings** → **Pages** → Source: **Deploy from a branch**.
2. Branch: `main`, folder: **`/docs`** → Save. (The folder list is a dropdown; `docs` is there because it exists.)
3. Live URL: `https://<user>.github.io/<repo>/`. First deploy takes 1–3 minutes; watch it under **Actions** → "pages build and deployment".
4. Put that URL in `config/site.json` → `base_url`, run `./wf site`, commit `docs/` and the config.
5. Done. Every later push that changes a source file will fail CI until `docs/` is rebuilt, which is the feature.

If you would rather have the site at the repo root, flip it: **Settings → Pages** → custom workflow
is unnecessary — just keep `/docs`. Do not rename the folder; the CI step, `netlify.toml` and the
deploy doc all say `docs`.

## B. Netlify

**Drop it (fastest, good for a test):** [app.netlify.com/drop](https://app.netlify.com/drop) → drag the
`docs` folder in. You get `https://<random>.netlify.app` immediately. An unclaimed drop expires after
24 hours — create the free account and claim it if you want it to stay.

**Connect it (the one to actually do):** Add new site → **Import a Git repository** → pick this repo →

| Field | Value |
|---|---|
| Base directory | repo root (leave empty) |
| Build command | *(leave empty — there is no build; `docs/` is committed)* |
| Publish directory | `docs` |

`docs/netlify.toml` repeats the publish directory and sets four response headers
(`nosniff`, referrer policy, camera/mic/geolocation all off). If you set the base directory to the
repo root, Netlify reads that file and works with no form fields at all.

## C. Six checks after deploy (5 minutes, do them — every one has caught something)

| # | Check | What a failure means |
|---|---|---|
| 1 | Open `/` | Cards for all 11 pages, including `/planner/` and `/book/` |
| 2 | Open `/planner/`, enter a shift start, press a preset | The tool is a copied file; if it is blank you dropped `index.html` without the folder |
| 3 | Follow three links from a page footer and one in the body | Any 404 = a `base_url` or depth mistake. `./wf site --check` should have caught it first |
| 4 | Visit `/anything-that-does-not-exist` | You should see the site's own 404 page with the full list. Netlify and Pages both serve `404.html` automatically |
| 5 | View source on an article | `<link rel="canonical">` matches the URL you actually opened, and the "Last reviewed" line is there |
| 6 | `/robots.txt`, `/sitemap.xml` | Both exist only when `base_url` is set — if sitemap is missing, that's step 0 |

**On the labelled photo boxes:** an `<img>` appears only for a file that exists in `04-seo/images/`.
Everything else renders as a visible "photo to supply" slot. That is the intended state for a site
whose author has not taken the photos yet — do not fake it with a stock image, and do not delete the
slot to make it look finished. Drop the real JPEG in with the same filename, run `./wf site`, commit.

## D. Get it indexed (10 minutes, while you're still in the browser)

1. [Search Console](https://search.google.com/search-console) → add property → **URL prefix** → paste the live URL.
2. Verify with the HTML-tag option **only if** you are willing to add the tag to `config/site.json`… it is not a field the builder supports, so use the **DNS record** or the **Google Analytics tag** route, or upload the provided HTML file into `docs/` and reference it — the folder is yours, the builder leaves unknown files alone as long as you commit them.
3. Sitemaps → submit `sitemap.xml`.
4. Request indexing on `/`, `/planner/` and your best article. The quota is 10 URLs a month; spend it on those three.

## E. What still needs a human hand after this

- **The store link.** `/book/` says plainly that the listing does not exist yet. When `02-kdp/02` is
  done, edit `04-seo/pages/03-book.md` (the page's own text), not `docs/book/index.html`.
- **Affiliate accounts.** A shop link is only legal here with the disclosure, and the disclosure is
  automatic: the builder detects an affiliate host in a page, injects the line at the top, and adds
  `rel="sponsored nofollow"` to the link. `config/site.json` → `affiliate_hosts` is where a new
  network gets added.
- **The contact address** in the footer (`config/site.json` → `contact`) — a site that sells something
  and offers no way to reach the author is a trust problem and an AdSense/Amazon-associate problem.
- The fact-check rows in `00-strategy/03-90-DAY-ROADMAP.md`, which every claim on the site defers to.

*Operational doc, not reader-facing content and not medical advice; it never appears on the site
because `04-seo/*.md` at the top level is not in the builder's source list.*
