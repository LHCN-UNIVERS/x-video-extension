// SaveNowX content script.
//
// X is a single-page app that loads content dynamically (both as you
// scroll a timeline, and when a single-post page's video finishes
// loading in), so a one-time DOM scan on page load isn't enough -- a
// MutationObserver watches for changes and re-scans.
//
// Deliberately video-first, not article-first: earlier versions of
// this required finding article[data-testid='tweet'] BEFORE checking
// for a video at all, which meant a single missing/renamed wrapper
// broke injection entirely, on the whole page, silently. A single
// post's permalink page structures its main tweet differently from
// how a scrolling timeline structures each item in some versions of
// X's markup, which is the likely cause if the button worked on a
// timeline but not here. Starting from the <video> element itself
// removes that single point of failure -- a video tag is unambiguous
// regardless of what wraps it.
//
// Honest note for future maintenance: still can't be verified against
// the live, current x.com DOM from the environment this was built in.
// If the button still doesn't appear after this, the next real signal
// would be console.log output confirming how many <video> elements
// this script actually finds on the page -- see the DEBUG toggle below.

const DEBUG = false; // flip true and check the browser console for diagnostics

const PROCESSED_ATTR = "data-savenowx-processed";
const SITE_BASE = "https://savenowx.com";

function findTweetUrl(video) {
  // Walk up from the video to the nearest ancestor that plausibly
  // represents "this tweet" -- article and [role="article"] cover
  // every version of X's markup seen so far, but if X drops both,
  // this falls through to searching the whole document as a last
  // resort rather than giving up (worse to occasionally attach to
  // the wrong nearby link than to never show a button at all).
  const scope =
    video.closest("article") ||
    video.closest('[role="article"]') ||
    document;

  const timeEl = scope.querySelector("time");
  const viaTime = timeEl?.closest("a[href*='/status/']");
  if (viaTime) return viaTime.getAttribute("href");

  const anyStatusLink = scope.querySelector("a[href*='/status/']");
  if (anyStatusLink) return anyStatusLink.getAttribute("href");

  // Last resort: the current page itself, if it's already a status
  // permalink (true for the exact page this bug was reported on).
  const pageMatch = location.pathname.match(/^\/[^/]+\/status\/\d+/);
  return pageMatch ? pageMatch[0] : null;
}

function findVideoContainer(video) {
  return (
    video.closest("[data-testid='videoPlayer']") ||
    video.closest("[data-testid='videoComponent']") ||
    video.parentElement
  );
}

function injectButton(video, tweetPath) {
  const container = findVideoContainer(video);
  if (!container) return;

  if (getComputedStyle(container).position === "static") {
    container.style.position = "relative";
  }

  const btn = document.createElement("button");
  btn.className = "savenowx-download-btn";
  btn.type = "button";
  btn.title = "Download with SaveNowX";
  btn.innerHTML = `
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
      <path d="M12 3v12m0 0l-4-4m4 4l4-4M5 21h14" stroke="white" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
    <span>Download with SaveNowX</span>
  `;

  btn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const url = tweetPath.startsWith("http")
      ? tweetPath
      : `${SITE_BASE}${tweetPath}`;
    window.open(url, "_blank", "noopener,noreferrer");
  });

  container.appendChild(btn);
}

function processVideo(video) {
  if (video.hasAttribute(PROCESSED_ATTR)) return;
  video.setAttribute(PROCESSED_ATTR, "1");

  const tweetPath = findTweetUrl(video);
  if (!tweetPath) {
    if (DEBUG) console.log("[SaveNowX] video found, but no tweet URL could be determined", video);
    return;
  }

  injectButton(video, tweetPath);
  if (DEBUG) console.log("[SaveNowX] button injected for", tweetPath);
}

function scan(root) {
  const videos = root.tagName === "VIDEO" ? [root] : Array.from(root.querySelectorAll?.("video") ?? []);
  if (DEBUG && videos.length) console.log(`[SaveNowX] scan found ${videos.length} video(s)`);
  videos.forEach(processVideo);
}

scan(document.body);

const observer = new MutationObserver((mutations) => {
  for (const mutation of mutations) {
    for (const node of mutation.addedNodes) {
      if (node.nodeType === Node.ELEMENT_NODE) {
        scan(node);
      }
    }
  }
});

observer.observe(document.body, { childList: true, subtree: true });
