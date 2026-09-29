# SaveNowX — X (Twitter) Video Downloader Extension

Adds a small download button directly onto videos while you browse X (Twitter) — click it, and it opens [SaveNowX](https://savenowx.com) with that post already loaded, ready to download.

**Install it from the Chrome Web Store:** https://chromewebstore.google.com/detail/bhjmopehdjkeibglekjcbnjdjfkakafk

No account, no watermark, nothing stored server-side. Works in Chrome, Edge, Brave, and most other Chromium-based browsers (Manifest V3).

## What it does

- **Content script** (`content.js`): watches the page for tweets containing a video (X loads tweets dynamically as you scroll, so this uses a `MutationObserver`, not a one-time scan) and injects a small pink download button in the corner of each video. Clicking it opens `savenowx.com/<username>/status/<id>` in a new tab — the same auto-resolving page the main site already has.
- **Popup** (`popup.html`/`popup.js`): what you get when you click the extension's own toolbar icon instead of a video's button. Pre-fills from the current tab if you're already on a status page; otherwise, paste any X post link and hit Open.
- **Permissions**: `activeTab` only. No background scraping, no data collection, no requests to anywhere except opening `savenowx.com` in a new tab.

## Running it from source

Most people should just install from the [Chrome Web Store link above](https://chromewebstore.google.com/detail/bhjmopehdjkeibglekjcbnjdjfkakafk) — it installs and updates itself automatically. This is for anyone who wants to inspect the code, modify it, or test a change before it ships.

1. Clone or download this repo.
2. Chrome/Edge → go to `chrome://extensions` (or `edge://extensions`).
3. Turn on **Developer mode** (top right).
4. Click **Load unpacked**, and select this folder.
5. Visit x.com — video posts should show a small pink download button.

## Honest maintenance note

The content script targets X's tweet markup using `data-testid` attributes (`article[data-testid='tweet']`, `[data-testid='videoPlayer']`), which have historically been more stable than X's CSS class names — but X does change its DOM periodically. If the button stops appearing on real tweets, `content.js`'s `findTweetUrl`/`findVideoContainer`/`injectButton` functions are exactly where to look first. This is an inherent trade-off of any unofficial extension that reads a site's live DOM rather than using an official API.

## License

MIT — see [LICENSE](LICENSE).

## Related

- [SaveNowX](https://savenowx.com) — the web app this extension opens, for downloading videos and MP3 audio from public X posts without an account.
